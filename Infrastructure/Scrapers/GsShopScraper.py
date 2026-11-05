from datetime import date, timedelta
from typing import List, Dict, Any
import asyncio
import json
import re
import logging
import aiohttp
from bs4 import BeautifulSoup
from Domain.Models.Schedule import ScheduleItem
from Infrastructure.Scrapers.BaseChannelScraper import BaseChannelScraper, ScraperParseException

logger = logging.getLogger(__name__)

class GsShopScraper(BaseChannelScraper):
    """
    GS SHOP mobile public schedule scraper (D+0 ~ D+6).
    Uses POST request to tvScheduleAjax.gs with XHR headers and dual JSON/HTML parsing.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="GS SHOP", max_days_ahead=6, timeout_seconds=15)
        self.ajax_schedule_url = "https://m.gsshop.com/shop/tv/tvScheduleAjax.gs"
        self.api_schedule_url = "https://m.gsshop.com/api/shop/tv/tvScheduleList.gs"
        self.fallback_page_url = "https://www.gsshop.com/shop/tv/tvSchedule.gs"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches GS SHOP schedule from start_date to end_date (capped at D+0 ~ D+6) via POST AJAX endpoints.
        """
        collected_items: List[ScheduleItem] = []
        max_allowed_date = start_date + timedelta(days=self.max_days_ahead)

        target_end_date = end_date
        if (target_end_date > max_allowed_date):
            target_end_date = max_allowed_date

        async with aiohttp.ClientSession() as session:
            current_date = start_date
            while (current_date <= target_end_date):
                date_str_compact = current_date.strftime("%Y%m%d")
                date_str_iso = current_date.strftime("%Y-%m-%d")

                daily_items = await self._fetch_daily_schedule(session, date_str_compact, date_str_iso)
                for item in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await asyncio.sleep(0.3)

        return collected_items

    async def _fetch_daily_schedule(
        self,
        session: aiohttp.ClientSession,
        date_str_compact: str,
        date_str_iso: str
    ) -> List[ScheduleItem]:
        """
        Queries GS SHOP mobile schedule via POST to tvScheduleAjax.gs with form data.
        """
        form_data = {}
        form_data["selectDate"] = date_str_compact
        form_data["searchDate"] = date_str_compact
        form_data["ch"] = "live"

        extra_headers = {}
        extra_headers["X-Requested-With"] = "XMLHttpRequest"
        extra_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        extra_headers["Referer"] = "https://m.gsshop.com/shop/tv/tvScheduleMain.gs"
        extra_headers["Accept"] = "text/html,application/xhtml+xml,application/json,text/javascript,*/*;q=0.9"

        last_error: Exception = ScraperParseException("GS_AllEndpointsFailed")

        # 1. Primary: POST to tvScheduleAjax.gs
        try:
            raw_text = await self.fetch_post_text(
                session,
                self.ajax_schedule_url,
                form_data=form_data,
                extra_headers=extra_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            if (len(items) > 0 or "html" in raw_text or "prd" in raw_text):
                return items
        except Exception as e1:
            last_error = e1

        # 2. Secondary: GET to tvScheduleAjax.gs with params
        try:
            params = {}
            params["selectDate"] = date_str_compact
            params["searchDate"] = date_str_compact
            params["ch"] = "live"
            raw_text = await self.fetch_text(
                session,
                self.ajax_schedule_url,
                params=params,
                extra_headers=extra_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e2:
            last_error = e2

        # 3. Fallback: PC Schedule URL
        try:
            pc_params = {}
            pc_params["searchDate"] = date_str_compact
            pc_headers = {}
            pc_headers["Referer"] = "https://www.gsshop.com/"
            raw_text = await self.fetch_text(
                session,
                self.fallback_page_url,
                params=pc_params,
                extra_headers=pc_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e3:
            last_error = e3

        raise last_error

    def _parse_payload(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses GS SHOP schedule response supporting direct JSON or HTML fragment parsing.
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        stripped = raw_text.strip()

        # Try JSON parsing
        if (stripped.startswith("{") or stripped.startswith("[")):
            try:
                data = json.loads(stripped)
                return self._extract_from_json(data, date_str_iso)
            except Exception:
                pass

        # Try extracting JSON blocks inside response
        json_blocks = re.findall(
            r"(?:__NEXT_DATA__|scheduleList|prdList|tvScheduleList|broadList|data)\s*[=:]\s*(\{.*?\}|\[.*?\])\s*[;<\n]",
            raw_text,
            re.DOTALL
        )
        for block_str in json_blocks:
            try:
                parsed_json = json.loads(block_str)
                return self._extract_from_json(parsed_json, date_str_iso)
            except Exception:
                continue

        # Try HTML DOM parsing via BeautifulSoup
        items = self._parse_html_dom(raw_text, date_str_iso)
        return items

    def _parse_html_dom(self, html_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses GS SHOP HTML fragment for schedule items.
        """
        soup = BeautifulSoup(html_text, "html.parser")
        product_elements = soup.select(
            "li.prd-item, div.prd-item, dl.prd-info, div.schedule-item, article.tv-item, "
            "li[data-prd-cd], div.broad-item, .product-item, .item-box, li.broad_item, div.tv-item"
        )

        if (len(product_elements) == 0):
            # Check for any li containing prd links
            product_elements = soup.select("li:has(a[href*='prd'])")

        if (len(product_elements) == 0):
            raise ScraperParseException("SelectorNotFound")

        items: List[ScheduleItem] = []
        current_time_str = "00:00"

        for elem in product_elements:
            time_node = elem.select_one(".time, .broad-time, .txt-time, span.hour, strong.time, .txt_time")
            if (time_node is not None):
                extracted_time = time_node.get_text(strip=True)
                time_match = re.search(r"(\d{1,2}:\d{2})", extracted_time)
                if (time_match is not None):
                    current_time_str = time_match.group(1)

            name_node = elem.select_one(".prd-name, .tit, .name, dt, a.prd-link, p.title, .txt_title, .prd_name")
            if (name_node is None):
                continue

            product_name = name_node.get_text(strip=True)
            if (not product_name):
                continue

            cat_node = elem.select_one(".cate, .category, .badge, .tag")
            category_text = "건강식품"
            if (cat_node is not None):
                category_text = cat_node.get_text(strip=True)

            if (not self.is_health_food_item(product_name, category_text)):
                continue

            price_str = ""
            price_node = elem.select_one(".price, .set-price, .selling-price, strong.num, .txt_price")
            if (price_node is not None):
                raw_price = price_node.get_text(strip=True)
                digits = re.sub(r"[^\d]", "", raw_price)
                if (digits):
                    price_str = digits

            link_url = ""
            link_node = elem.select_one("a[href]")
            if (link_node is not None):
                href_val = str(link_node.get("href", ""))
                if (href_val.startswith("http")):
                    link_url = href_val
                elif (href_val.startswith("/")):
                    link_url = f"https://m.gsshop.com{href_val}"

            schedule_item = ScheduleItem(
                broadcast_date=date_str_iso,
                broadcast_time=current_time_str,
                channel=self.channel_name,
                category="건강식품",
                product_name=product_name,
                price=price_str,
                link_url=link_url
            )
            items.append(schedule_item)

        return items

    def _extract_from_json(self, data: Any, date_str_iso: str) -> List[ScheduleItem]:
        """
        Recursively walks JSON dictionary/list structures to extract GS SHOP ScheduleItem records.
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if (isinstance(node, dict)):
                if (
                    "prdNm" in node
                    or "goodsNm" in node
                    or "itemName" in node
                    or "productName" in node
                    or "pgmNm" in node
                    or "dtlPrdNm" in node
                ):
                    candidates.append(node)
                for val in node.values():
                    _walk(val)
            elif (isinstance(node, list)):
                for sub in node:
                    _walk(sub)

        _walk(data)
        if (len(candidates) == 0):
            raise ScraperParseException("EmptyJSONScheduleNodes")

        for item_dict in candidates:
            product_name = str(
                item_dict.get("prdNm")
                or item_dict.get("dtlPrdNm")
                or item_dict.get("goodsNm")
                or item_dict.get("productName")
                or item_dict.get("itemName")
                or item_dict.get("pgmNm")
                or ""
            ).strip()

            if (not product_name):
                continue

            category_text = str(
                item_dict.get("catNm")
                or item_dict.get("lrgCatNm")
                or item_dict.get("sectNm")
                or item_dict.get("category")
                or ""
            )
            if (not self.is_health_food_item(product_name, category_text)):
                continue

            raw_time = str(
                item_dict.get("broadTime")
                or item_dict.get("broadStrDtm")
                or item_dict.get("startTime")
                or item_dict.get("pgmStartTime")
                or item_dict.get("startDtm")
                or "00:00"
            )
            time_match = re.search(r"(\d{1,2}:\d{2})", raw_time)
            if (time_match is not None):
                time_str = time_match.group(1)
            elif (len(raw_time) >= 12 and raw_time.isdigit()):
                time_str = f"{raw_time[8:10]}:{raw_time[10:12]}"
            elif (len(raw_time) == 4 and raw_time.isdigit()):
                time_str = f"{raw_time[0:2]}:{raw_time[2:4]}"
            else:
                time_str = "00:00"

            raw_price = str(
                item_dict.get("salePrice")
                or item_dict.get("exposePrice")
                or item_dict.get("price")
                or item_dict.get("prmPrice")
                or ""
            )
            price_str = re.sub(r"[^\d]", "", raw_price)

            prd_cd = str(item_dict.get("prdCd") or item_dict.get("goodsCd") or "")
            link_url = str(item_dict.get("linkUrl") or item_dict.get("prdUrl") or "")
            if (not link_url and prd_cd):
                link_url = f"https://m.gsshop.com/prd/prd.gs?prdid={prd_cd}"

            schedule_item = ScheduleItem(
                broadcast_date=date_str_iso,
                broadcast_time=time_str,
                channel=self.channel_name,
                category="건강식품",
                product_name=product_name,
                price=price_str,
                link_url=link_url
            )
            results.append(schedule_item)

        return results
