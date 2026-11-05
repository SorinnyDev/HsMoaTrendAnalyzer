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

class NsMallScraper(BaseChannelScraper):
    """
    NS홈쇼핑 mobile public schedule scraper (D+0 ~ D+3).
    Queries getTvScheduleList API with dual JSON and BeautifulSoup HTML fallback parsing.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="NS홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.rest_api_url = "https://m.nsmall.com/tv/schedule/getTvScheduleList"
        self.page_url_template = "https://m.nsmall.com/store/fixed/schedule/tv/{date_compact}"
        self.ajax_legacy_url = "https://m.nsmall.com/NSAjaxTVSchedule"
        self.mobile_schedule_url = "https://m.nsmall.com/tv/schedule"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches NS홈쇼핑 schedule from start_date to end_date (capped at D+0 ~ D+3).
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
        Queries NS홈쇼핑 mobile endpoints with JSON parsing and BeautifulSoup HTML fallback.
        """
        params = {}
        params["selectDay"] = date_str_compact
        params["cnnlCd"] = "TV"

        extra_headers = {}
        extra_headers["Accept"] = "application/json, text/plain, text/html, */*"
        extra_headers["Referer"] = "https://m.nsmall.com/tv/schedule"
        extra_headers["X-Requested-With"] = "XMLHttpRequest"

        last_error: Exception = ScraperParseException("NS_AllEndpointsFailed")

        # 1. Primary REST API: getTvScheduleList
        try:
            raw_text = await self.fetch_text(
                session,
                self.rest_api_url,
                params=params,
                extra_headers=extra_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e1:
            last_error = e1

        # 2. Page URL Template (HTML)
        try:
            page_url = self.page_url_template.format(date_compact=date_str_compact)
            page_headers = {}
            page_headers["Referer"] = "https://m.nsmall.com/"
            raw_text = await self.fetch_text(
                session,
                page_url,
                extra_headers=page_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e2:
            last_error = e2

        # 3. Legacy AJAX URL
        try:
            legacy_params = {}
            legacy_params["selectDay"] = date_str_compact
            legacy_params["cnnlCd"] = "TV"
            raw_text = await self.fetch_text(
                session,
                self.ajax_legacy_url,
                params=legacy_params,
                extra_headers=extra_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e3:
            last_error = e3

        raise last_error

    def _parse_payload(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Extracts schedule items from NS홈쇼핑 JSON response or BeautifulSoup HTML fallback.
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        stripped = raw_text.strip()

        # 1. Direct JSON response
        if (stripped.startswith("{") or stripped.startswith("[")):
            try:
                data = json.loads(stripped)
                return self._extract_from_json(data, date_str_iso)
            except Exception:
                pass

        # 2. Embedded JSON blocks in HTML
        json_blocks = re.findall(
            r"(?:__NEXT_DATA__|__NUXT_DATA__|scheduleList|TotalOrgan|tvScheduleList|goodsList|msg)\s*[=:]\s*(\{.*?\}|\[.*?\])\s*[;<\n]",
            raw_text,
            re.DOTALL
        )
        for block_str in json_blocks:
            try:
                parsed_json = json.loads(block_str)
                return self._extract_from_json(parsed_json, date_str_iso)
            except Exception:
                continue

        # 3. HTML DOM parsing via BeautifulSoup fallback
        items = self._parse_html_dom(raw_text, date_str_iso)
        return items

    def _parse_html_dom(self, html_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses NS홈쇼핑 HTML markup for schedule product items.
        """
        soup = BeautifulSoup(html_text, "html.parser")
        product_elements = soup.select(
            ".product-item, div.schedule-item, li.schedule-item, div.prd-box, li.prd-item, "
            "div.tv-schedule-item, .goods-item, li.item, div.item-box, li[data-goods-cd]"
        )

        if (len(product_elements) == 0):
            # Broader selection for product links
            product_elements = soup.select("li:has(a[href*='goods'])")

        if (len(product_elements) == 0):
            raise ScraperParseException("SelectorNotFound")

        items: List[ScheduleItem] = []
        current_time_str = "00:00"

        for elem in product_elements:
            time_node = elem.select_one(".time, .broad-time, .txt-time, span.time, em.time, strong.time")
            if (time_node is not None):
                time_match = re.search(r"(\d{1,2}:\d{2})", time_node.get_text(strip=True))
                if (time_match is not None):
                    current_time_str = time_match.group(1)

            name_node = elem.select_one(".prd-name, .goods-name, .tit, .title, p.name, .goods_name, .product_name")
            if (name_node is None):
                continue

            product_name = name_node.get_text(strip=True)
            if (not product_name):
                continue

            cat_node = elem.select_one(".cate, .category, .tag, .badge")
            category_text = "건강식품"
            if (cat_node is not None):
                category_text = cat_node.get_text(strip=True)

            if (not self.is_health_food_item(product_name, category_text)):
                continue

            price_str = ""
            price_node = elem.select_one(".price, .num, .selling-price, .txt_price, strong.price")
            if (price_node is not None):
                digits = re.sub(r"[^\d]", "", price_node.get_text(strip=True))
                if (digits):
                    price_str = digits

            link_url = ""
            link_node = elem.select_one("a[href]")
            if (link_node is not None):
                href_val = str(link_node.get("href", ""))
                if (href_val.startswith("http")):
                    link_url = href_val
                elif (href_val.startswith("/")):
                    link_url = f"https://m.nsmall.com{href_val}"

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
        Extracts ScheduleItem objects from NS홈쇼핑 JSON structure.
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if (isinstance(node, dict)):
                if (
                    "goodsName" in node
                    or "unitName" in node
                    or "productName" in node
                    or "pgmNm" in node
                    or "goodsNm" in node
                    or "itnEpsuNm" in node
                    or "headCopy" in node
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
                item_dict.get("goodsName")
                or item_dict.get("unitName")
                or item_dict.get("itnEpsuNm")
                or item_dict.get("productName")
                or item_dict.get("goodsNm")
                or item_dict.get("pgmNm")
                or ""
            ).strip()

            if (not product_name):
                continue

            category_text = str(
                item_dict.get("catNm")
                or item_dict.get("dispGrpNm")
                or item_dict.get("category")
                or ""
            )
            if (not self.is_health_food_item(product_name, category_text)):
                continue

            raw_time = str(
                item_dict.get("startTime")
                or item_dict.get("formStartDttm")
                or item_dict.get("startDtm")
                or item_dict.get("time")
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
                or item_dict.get("offerPrice")
                or item_dict.get("busChnIdPrice")
                or item_dict.get("price")
                or ""
            )
            price_str = re.sub(r"[^\d]", "", raw_price)

            goods_cd = str(item_dict.get("goodsCd") or item_dict.get("partNumber") or "")
            link_url = str(item_dict.get("linkUrl") or "")
            if (not link_url and goods_cd):
                link_url = f"https://m.nsmall.com/goods/{goods_cd}"

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
