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

class HmallScraper(BaseChannelScraper):
    """
    현대홈쇼핑 mobile public schedule scraper (D+0 ~ D+3).
    Targets Hmall mobile TV schedule JSON/AJAX endpoints (/p/bmc/selectTvSchedule.do, etc.).
    """

    def __init__(self) -> None:
        super().__init__(channel_name="현대홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.select_schedule_url = "https://www.hmall.com/p/bmc/selectTvSchedule.do"
        self.mobile_schedule_url = "https://m.hmall.com/p/bmc/selectTvSchedule.do"
        self.brod_list_url = "https://www.hmall.com/p/bmc/brodPordList.do"
        self.tv_schedule_page_url = "https://www.hmall.com/p/dpl/TVSchedule"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches 현대홈쇼핑 schedule from start_date to end_date (capped at D+0 ~ D+3).
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

                daily_items = await self._fetch_single_date(session, date_str_compact, date_str_iso)
                for item in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await asyncio.sleep(0.3)

        return collected_items

    async def _fetch_single_date(
        self,
        session: aiohttp.ClientSession,
        date_str_compact: str,
        date_str_iso: str
    ) -> List[ScheduleItem]:
        """
        Queries Hmall TV schedule endpoints (/p/bmc/selectTvSchedule.do, etc.) and parses JSON/HTML.
        """
        params = {}
        params["brodDt"] = date_str_compact
        params["searchDate"] = date_str_compact
        params["broadDt"] = date_str_compact
        params["brodGb"] = "01"

        extra_headers = {}
        extra_headers["Referer"] = "https://www.hmall.com/p/dpl/TVSchedule"
        extra_headers["X-Requested-With"] = "XMLHttpRequest"
        extra_headers["Accept"] = "application/json, text/javascript, text/html, */*; q=0.01"

        endpoints = []
        endpoints.append(self.select_schedule_url)
        endpoints.append(self.mobile_schedule_url)
        endpoints.append(self.brod_list_url)
        endpoints.append(self.tv_schedule_page_url)

        last_error: Exception = ScraperParseException("HmallEndpointFailed")

        for endpoint_url in endpoints:
            try:
                raw_text = await self.fetch_text(
                    session,
                    endpoint_url,
                    params=params,
                    extra_headers=extra_headers
                )
                items = self._parse_response(raw_text, date_str_iso)
                return items
            except Exception as err:
                last_error = err
                continue

        raise last_error

    def _parse_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses 현대홈쇼핑 schedule response (JSON payload, embedded script JSON, or HTML DOM).
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        stripped = raw_text.strip()
        if (stripped.startswith("{") or stripped.startswith("[")):
            try:
                data = json.loads(stripped)
                return self._extract_from_json(data, date_str_iso)
            except Exception as e:
                raise ScraperParseException(f"JSONParseError: {str(e)}") from e

        # Check embedded JSON inside script blocks
        json_blocks = re.findall(
            r"(?:__NEXT_DATA__|tvScheduleList|brodList|scheduleList|brodPordList)\s*[=:]\s*(\{.*?\}|\[.*?\])\s*[;<\n]",
            raw_text,
            re.DOTALL
        )
        for block_str in json_blocks:
            try:
                parsed_json = json.loads(block_str)
                return self._extract_from_json(parsed_json, date_str_iso)
            except Exception:
                continue

        inline_objects = re.findall(r"\{[^{}]*?(?:\"slitmNm\"|\"brodTitl\"|\"slitmCd\"|\"brodStrtDtm\")[^{}]*?\}", raw_text)
        if (len(inline_objects) > 0):
            parsed_list = []
            for obj_str in inline_objects:
                try:
                    parsed_list.append(json.loads(obj_str))
                except Exception:
                    continue
            if (len(parsed_list) > 0):
                return self._extract_from_json(parsed_list, date_str_iso)

        soup = BeautifulSoup(raw_text, "html.parser")
        script_node = soup.select_one("script#__NEXT_DATA__")
        if (script_node is not None and script_node.string):
            try:
                json_data = json.loads(script_node.string)
                return self._extract_from_json(json_data, date_str_iso)
            except Exception as e:
                raise ScraperParseException(f"NextDataParseError: {str(e)}") from e

        product_elements = soup.select(
            "li.schedule-item, div.schedule-item, li.prd-item, div.brod-item, "
            "ul.brod-list > li, div.onair-item, li[data-slitm-cd]"
        )

        if (len(product_elements) == 0):
            raise ScraperParseException("SelectorNotFound")

        items: List[ScheduleItem] = []
        current_time_str = "00:00"

        for elem in product_elements:
            time_node = elem.select_one(".time, .brod-time, .txt-time, span.time, strong.time")
            if (time_node is not None):
                time_match = re.search(r"(\d{1,2}:\d{2})", time_node.get_text(strip=True))
                if (time_match is not None):
                    current_time_str = time_match.group(1)

            name_node = elem.select_one(".prd-name, .slitmNm, .tit, .title, p.name, .item-title")
            if (name_node is None):
                continue

            product_name = name_node.get_text(strip=True)
            if (not product_name):
                continue

            cat_node = elem.select_one(".cate, .category, .sect-name")
            category_text = "건강식품"
            if (cat_node is not None):
                category_text = cat_node.get_text(strip=True)

            if (not self.is_health_food_item(product_name, category_text)):
                continue

            price_str = ""
            price_node = elem.select_one(".price, .sellPrc, .num, strong.price")
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
                    link_url = f"https://www.hmall.com{href_val}"

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
        Extracts ScheduleItem objects from 현대홈쇼핑 JSON structure.
        Maps product name (slitmNm / brodTitl / itemNm) and broadcast time (brodStrtDtm / brodTime).
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if (isinstance(node, dict)):
                if (
                    "slitmNm" in node
                    or "brodTitl" in node
                    or "itemNm" in node
                    or "prdNm" in node
                    or "goodsNm" in node
                    or "dispTitl" in node
                ):
                    candidates.append(node)
                for val in node.values():
                    _walk(val)
            elif (isinstance(node, list)):
                for sub in node:
                    _walk(sub)

        _walk(data)
        if (len(candidates) == 0):
            raise ScraperParseException("SelectorNotFound")

        for item_dict in candidates:
            product_name = str(
                item_dict.get("slitmNm")
                or item_dict.get("brodTitl")
                or item_dict.get("dispTitl")
                or item_dict.get("itemNm")
                or item_dict.get("prdNm")
                or item_dict.get("goodsNm")
                or ""
            ).strip()

            if (not product_name):
                continue

            category_text = str(
                item_dict.get("sectNm")
                or item_dict.get("dispSectNm")
                or item_dict.get("catNm")
                or item_dict.get("ctgNm")
                or item_dict.get("category")
                or ""
            )
            if (not self.is_health_food_item(product_name, category_text)):
                continue

            raw_time = str(
                item_dict.get("brodStrtDtm")
                or item_dict.get("brodStrtTm")
                or item_dict.get("brodTime")
                or item_dict.get("startTime")
                or item_dict.get("strtTm")
                or "00:00"
            )
            time_match = re.search(r"(\d{1,2}:\d{2})", raw_time)
            if (time_match is not None):
                time_str = time_match.group(1)
            elif (len(raw_time) >= 12 and raw_time.isdigit()):
                time_str = f"{raw_time[8:10]}:{raw_time[10:12]}"
            elif (len(raw_time) == 4 and raw_time.isdigit()):
                time_str = f"{raw_time[0:2]}:{raw_time[2:4]}"
            elif (len(raw_time) == 6 and raw_time.isdigit()):
                time_str = f"{raw_time[0:2]}:{raw_time[2:4]}"
            else:
                time_str = "00:00"

            raw_price = str(
                item_dict.get("sellPrc")
                or item_dict.get("bbprc")
                or item_dict.get("salePrice")
                or item_dict.get("price")
                or ""
            )
            price_str = re.sub(r"[^\d]", "", raw_price)

            slitm_cd = str(item_dict.get("slitmCd") or "")
            link_url = str(item_dict.get("linkUrl") or "")
            if (not link_url and slitm_cd):
                link_url = f"https://www.hmall.com/p/pda/itemPtc.do?slitmCd={slitm_cd}"

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
