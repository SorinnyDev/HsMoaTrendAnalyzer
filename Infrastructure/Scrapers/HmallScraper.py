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
    Targets Hmall mobile TV schedule REST API (/api/hf/dp/v1/main-tv-new/tv-list).
    """

    def __init__(self) -> None:
        super().__init__(channel_name="현대홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.tv_list_api_url = "https://www.hmall.com/api/hf/dp/v1/main-tv-new/tv-list"
        self.tv_schedule_page_url = "https://www.hmall.com/md/dpl/index?mainDispSeq=2&brodType=all"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches 현대홈쇼핑 schedule from start_date to end_date (capped at D+0 ~ D+3).
        """
        collected_items: List[ScheduleItem] = []
        max_allowed_date = start_date + timedelta(days=self.max_days_ahead)

        target_end_date = end_date
        if (target_end_date > max_allowed_date):
            target_end_date = max_allowed_date

        async with self.create_stealth_session() as session:
            await self.warm_up_session(session, self.tv_schedule_page_url)
            current_date = start_date
            while (current_date <= target_end_date):
                date_str_compact = current_date.strftime("%Y%m%d")
                date_str_iso = current_date.strftime("%Y-%m-%d")

                daily_items = await self._fetch_single_date(session, date_str_compact, date_str_iso)
                for (item) in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await self.sleep_with_jitter(0.65, 1.45)

        return collected_items

    async def _fetch_single_date(
        self,
        session: aiohttp.ClientSession,
        date_str_compact: str,
        date_str_iso: str
    ) -> List[ScheduleItem]:
        """
        Queries Hmall TV schedule REST API (/api/hf/dp/v1/main-tv-new/tv-list) across time pages 1..6.
        """
        extra_headers = {}
        extra_headers["Referer"] = self.tv_schedule_page_url
        extra_headers["Accept"] = "application/json, text/plain, */*"

        day_items: List[ScheduleItem] = []
        seen_keys: Dict[str, bool] = {}
        successful_pages = 0
        last_error: Exception = ScraperParseException("HmallEndpointFailed")

        query_modes = []
        query_modes.append("001,002")
        query_modes.append("")

        for (category_filter) in query_modes:
            for (page_no) in range(1, 7):
                params = {}
                params["brodDt"] = date_str_compact
                params["brodPrrgPage"] = str(page_no)
                if (len(category_filter) > 0):
                    params["categoryList"] = category_filter

                try:
                    raw_text = await self.fetch_text(
                        session,
                        self.tv_list_api_url,
                        params=params,
                        extra_headers=extra_headers
                    )
                    successful_pages = successful_pages + 1
                    page_items = self._parse_response(raw_text, date_str_iso)
                    for (item) in page_items:
                        dedup_key = f"{item.broadcast_date}_{item.broadcast_time}_{item.product_name}"
                        if (dedup_key not in seen_keys):
                            seen_keys[dedup_key] = True
                            day_items.append(item)
                    await self.sleep_with_jitter(0.18, 0.45)
                except Exception as err:
                    last_error = err
                    continue

            if (len(day_items) > 0):
                break

        if (successful_pages == 0):
            raise last_error

        return day_items

    def _parse_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses 현대홈쇼핑 schedule response (JSON payload from /api/hf/dp/v1/main-tv-new/tv-list).
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        stripped = raw_text.strip()
        if (stripped.startswith("{") or stripped.startswith("[")):
            try:
                data = json.loads(stripped)
                return self._extract_from_json(data, date_str_iso)
            except ScraperParseException:
                raise
            except Exception as e:
                raise ScraperParseException(f"JSONParseError: {str(e)}") from e

        soup = BeautifulSoup(raw_text, "html.parser")
        script_node = soup.select_one("script#__NEXT_DATA__")
        if (script_node is not None and script_node.string):
            try:
                json_data = json.loads(script_node.string)
                return self._extract_from_json(json_data, date_str_iso)
            except Exception as e:
                raise ScraperParseException(f"NextDataParseError: {str(e)}") from e

        raise ScraperParseException("SelectorNotFound")

    def _extract_from_json(self, data: Any, date_str_iso: str) -> List[ScheduleItem]:
        """
        Extracts ScheduleItem objects from 현대홈쇼핑 JSON structure (respData.broadItemList & withItemList).
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        if (isinstance(data, dict)):
            resp_data = data.get("respData")
            if (isinstance(resp_data, dict)):
                broad_list = resp_data.get("broadItemList")
                if (isinstance(broad_list, list)):
                    for (main_item) in broad_list:
                        if (isinstance(main_item, dict)):
                            candidates.append(main_item)
                            with_items = main_item.get("withItemList")
                            if (isinstance(with_items, list)):
                                for (sub_item) in with_items:
                                    if (isinstance(sub_item, dict)):
                                        candidates.append(sub_item)
                    if (len(broad_list) == 0):
                        return results

        if (len(candidates) == 0):
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
                    for (val) in node.values():
                        _walk(val)
                elif (isinstance(node, list)):
                    for (sub) in node:
                        _walk(sub)

            _walk(data)

        for (item_dict) in candidates:
            product_name = str(
                item_dict.get("slitmNm")
                or item_dict.get("convertedSlitmNm")
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

            raw_brod_dt = str(item_dict.get("newBrodDt") or item_dict.get("brodDt") or "").strip()
            item_date_iso = date_str_iso
            if (len(raw_brod_dt) == 8 and raw_brod_dt.isdigit()):
                item_date_iso = f"{raw_brod_dt[0:4]}-{raw_brod_dt[4:6]}-{raw_brod_dt[6:8]}"
                if (item_date_iso != date_str_iso):
                    continue

            raw_time = str(
                item_dict.get("brodStrtDtm")
                or item_dict.get("brodStrtDtmParam")
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
                item_dict.get("bbprc")
                or item_dict.get("sellPrc")
                or item_dict.get("salePrice")
                or item_dict.get("price")
                or ""
            )
            price_str = re.sub(r"[^\d]", "", raw_price)

            slitm_cd = str(item_dict.get("slitmCd") or "")
            link_url = str(item_dict.get("linkUrl") or "")
            if (not link_url and slitm_cd):
                link_url = f"https://www.hmall.com/md/pda/itemPtc?slitmCd={slitm_cd}"

            schedule_item = ScheduleItem(
                broadcast_date=item_date_iso,
                broadcast_time=time_str,
                channel=self.channel_name,
                category="건강식품",
                product_name=product_name,
                price=price_str,
                link_url=link_url
            )
            results.append(schedule_item)

        return results
