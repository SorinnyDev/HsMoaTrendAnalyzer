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

class LotteImallScraper(BaseChannelScraper):
    """
    롯데홈쇼핑 mobile public schedule scraper (D+0 ~ D+3).
    Directly targets mobile AJAX schedule endpoint (selectTvScheduleListAjax.lotte) via POST.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="롯데홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.mobile_ajax_url = "https://m.lotteimall.com/display/selectTvScheduleListAjax.lotte"
        self.pc_ajax_url = "https://www.lotteimall.com/display/selectTvScheduleListAjax.lotte"
        self.mobile_view_url = "https://m.lotteimall.com/display/viewTvSchedule.lotte"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches 롯데홈쇼핑 schedule from start_date to end_date (capped at D+0 ~ D+3).
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
        Queries 롯데홈쇼핑 schedule via POST to selectTvScheduleListAjax.lotte with form data.
        """
        form_data = {}
        form_data["bd_date"] = date_str_compact
        form_data["chnl_gb"] = "01"
        form_data["searchDate"] = date_str_compact

        extra_headers = {}
        extra_headers["X-Requested-With"] = "XMLHttpRequest"
        extra_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        extra_headers["Referer"] = "https://m.lotteimall.com/display/viewTvSchedule.lotte"
        extra_headers["Accept"] = "application/json, text/javascript, text/html, */*; q=0.01"

        last_error: Exception = ScraperParseException("Lotte_AllEndpointsFailed")

        # 1. Primary: Mobile POST selectTvScheduleListAjax.lotte
        try:
            raw_text = await self.fetch_post_text(
                session,
                self.mobile_ajax_url,
                form_data=form_data,
                extra_headers=extra_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e1:
            last_error = e1

        # 2. Secondary: PC POST selectTvScheduleListAjax.lotte
        try:
            pc_headers = {}
            pc_headers["X-Requested-With"] = "XMLHttpRequest"
            pc_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            pc_headers["Referer"] = "https://www.lotteimall.com/display/viewTvSchedule.lotte"
            raw_text = await self.fetch_post_text(
                session,
                self.pc_ajax_url,
                form_data=form_data,
                extra_headers=pc_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e2:
            last_error = e2

        # 3. Fallback: Mobile GET viewTvSchedule.lotte with params
        try:
            params = {}
            params["bd_date"] = date_str_compact
            params["chnl_gb"] = "01"
            raw_text = await self.fetch_text(
                session,
                self.mobile_view_url,
                params=params,
                extra_headers=extra_headers
            )
            items = self._parse_payload(raw_text, date_str_iso)
            return items
        except Exception as e3:
            last_error = e3

        raise last_error

    def _parse_payload(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses 롯데홈쇼핑 response (JSON or HTML fragment).
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

        # 2. Embedded JSON script blocks
        json_blocks = re.findall(
            r"(?:__NEXT_DATA__|scheduleList|tvScheduleList|broadList|goodsList|dataList)\s*[=:]\s*(\{.*?\}|\[.*?\])\s*[;<\n]",
            raw_text,
            re.DOTALL
        )
        for block_str in json_blocks:
            try:
                parsed_json = json.loads(block_str)
                return self._extract_from_json(parsed_json, date_str_iso)
            except Exception:
                continue

        # 3. HTML DOM parsing via BeautifulSoup
        items = self._parse_html_dom(raw_text, date_str_iso)
        return items

    def _parse_html_dom(self, html_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses 롯데홈쇼핑 HTML markup for schedule product items.
        """
        soup = BeautifulSoup(html_text, "html.parser")
        product_elements = soup.select(
            "li.broad-item, div.broad-item, li.schedule-item, div.prd-item, ul.list_tv > li, "
            "div.tv_schedule_list li, div.schedule_list li, div.box_prd, li[data-goods-no], "
            "div.item_box, li.item, div.tv-item"
        )

        if (len(product_elements) == 0):
            product_elements = soup.select("li:has(a[href*='goods'])")

        if (len(product_elements) == 0):
            raise ScraperParseException("SelectorNotFound")

        items: List[ScheduleItem] = []
        current_time_str = "00:00"

        for elem in product_elements:
            time_node = elem.select_one(".time, .broad_time, .txt_time, span.time, em.time, strong.time")
            if (time_node is not None):
                time_match = re.search(r"(\d{1,2}:\d{2})", time_node.get_text(strip=True))
                if (time_match is not None):
                    current_time_str = time_match.group(1)

            name_node = elem.select_one(".prd_name, .tit_prd, .name, .title, .txt_tit, p.tit, a.link, .prd-name")
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
            price_node = elem.select_one(".price, .txt_price, .num, strong.price, .sale_price")
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
                    link_url = f"https://m.lotteimall.com{href_val}"

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
        Extracts ScheduleItem objects from 롯데홈쇼핑 JSON structure.
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if (isinstance(node, dict)):
                if (
                    "goods_nm" in node
                    or "goodsNm" in node
                    or "prdNm" in node
                    or "itemName" in node
                    or "pgm_nm" in node
                    or "pgmNm" in node
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
                item_dict.get("goods_nm")
                or item_dict.get("goodsNm")
                or item_dict.get("prdNm")
                or item_dict.get("itemName")
                or item_dict.get("pgm_nm")
                or item_dict.get("pgmNm")
                or ""
            ).strip()

            if (not product_name):
                continue

            category_text = str(
                item_dict.get("cat_nm")
                or item_dict.get("catNm")
                or item_dict.get("disp_nm")
                or item_dict.get("category")
                or ""
            )
            if (not self.is_health_food_item(product_name, category_text)):
                continue

            raw_time = str(
                item_dict.get("bd_bgn_dtm")
                or item_dict.get("bdBgnDtm")
                or item_dict.get("broad_str_dtm")
                or item_dict.get("startTime")
                or item_dict.get("broadTime")
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
                item_dict.get("sale_price")
                or item_dict.get("salePrice")
                or item_dict.get("final_price")
                or item_dict.get("price")
                or ""
            )
            price_str = re.sub(r"[^\d]", "", raw_price)

            goods_no = str(item_dict.get("goods_no") or item_dict.get("goodsNo") or "")
            link_url = str(item_dict.get("linkUrl") or "")
            if (not link_url and goods_no):
                link_url = f"https://m.lotteimall.com/goods/viewGoodsDetail.lotte?goods_no={goods_no}"

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
