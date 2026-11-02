from datetime import date, timedelta
from typing import List, Dict, Any
import asyncio
import json
import re
import logging
import aiohttp
from Domain.Models.Schedule import ScheduleItem
from Infrastructure.Scrapers.BaseChannelScraper import BaseChannelScraper, ScraperParseException

logger = logging.getLogger(__name__)

class GsShopScraper(BaseChannelScraper):
    """
    GS SHOP mobile public schedule scraper (D+0 ~ D+6).
    Directly calls GS SHOP mobile internal JSON/AJAX schedule APIs without HTML DOM reliance.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="GS SHOP", max_days_ahead=6, timeout_seconds=15)
        self.ajax_schedule_url = "https://m.gsshop.com/shop/tv/tvScheduleAjax.gs"
        self.api_schedule_url = "https://m.gsshop.com/api/shop/tv/tvScheduleList.gs"
        self.detail_schedule_url = "https://m.gsshop.com/shop/tv/tvScheduleDetail.gs"
        self.page_schedule_url = "https://m.gsshop.com/shop/tv/tvSchedule.gs"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches GS SHOP schedule from start_date to end_date (capped at D+0 ~ D+6) via JSON/AJAX endpoints.
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

                daily_items = await self._fetch_daily_json_schedule(session, date_str_compact, date_str_iso)
                for item in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await asyncio.sleep(0.3)

        return collected_items

    async def _fetch_daily_json_schedule(
        self,
        session: aiohttp.ClientSession,
        date_str_compact: str,
        date_str_iso: str
    ) -> List[ScheduleItem]:
        """
        Queries GS SHOP mobile internal JSON/AJAX endpoints in priority order.
        """
        params = {}
        params["searchDate"] = date_str_compact
        params["broadDate"] = date_str_compact
        params["selectDate"] = date_str_compact
        params["ch"] = "live"
        params["_"] = date_str_compact

        extra_headers = {}
        extra_headers["Referer"] = "https://m.gsshop.com/shop/tv/tvSchedule.gs"
        extra_headers["X-Requested-With"] = "XMLHttpRequest"
        extra_headers["Accept"] = "application/json, text/javascript, */*; q=0.01"

        endpoints = []
        endpoints.append(self.ajax_schedule_url)
        endpoints.append(self.api_schedule_url)
        endpoints.append(self.detail_schedule_url)
        endpoints.append(self.page_schedule_url)

        last_error: Exception = ScraperParseException("GS_JSONEndpointNotFound")

        for endpoint in endpoints:
            try:
                raw_text = await self.fetch_text(
                    session,
                    endpoint,
                    params=params,
                    extra_headers=extra_headers
                )
                items = self._parse_payload(raw_text, date_str_iso)
                return items
            except Exception as err:
                last_error = err
                continue

        raise last_error

    def _parse_payload(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Extracts schedule items from GS SHOP JSON response or embedded JSON script objects.
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        stripped = raw_text.strip()

        # 1. Direct JSON object or array
        if (stripped.startswith("{") or stripped.startswith("[")):
            try:
                data = json.loads(stripped)
                return self._extract_from_json(data, date_str_iso)
            except Exception as e:
                raise ScraperParseException(f"JSONParseError: {str(e)}") from e

        # 2. JSONP or embedded JS variable assignment (e.g., var scheduleData = {...}; or __NEXT_DATA__)
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

        # 3. Regex extraction of product JSON objects inside script blocks
        inline_objects = re.findall(r"\{[^{}]*?(?:\"prdNm\"|\"goodsNm\"|\"prdCd\"|\"exposePrice\")[^{}]*?\}", raw_text)
        if (len(inline_objects) > 0):
            parsed_list = []
            for obj_str in inline_objects:
                try:
                    parsed_list.append(json.loads(obj_str))
                except Exception:
                    continue
            if (len(parsed_list) > 0):
                return self._extract_from_json(parsed_list, date_str_iso)

        raise ScraperParseException("JSONPayloadNotFound")

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
