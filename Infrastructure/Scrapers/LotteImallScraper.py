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
    Targets mobile TV schedule endpoints (viewTvSchedule.lotte and JSON/AJAX schedule APIs).
    """

    def __init__(self) -> None:
        super().__init__(channel_name="롯데홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.primary_api_url = "https://m.lotteimall.com/display/viewTvSchedule.lotte"
        self.ajax_api_url = "https://m.lotteimall.com/display/selectTvScheduleListAjax.lotte"
        self.pc_ajax_url = "https://www.lotteimall.com/display/selectTvScheduleListAjax.lotte"
        self.pc_schedule_url = "https://www.lotteimall.com/display/viewTvSchedule.lotte"

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
        Attempts to fetch 롯데홈쇼핑 schedule via mobile viewTvSchedule.lotte and AJAX endpoints.
        """
        params = {}
        params["bd_date"] = date_str_compact
        params["searchDate"] = date_str_compact
        params["broad_date"] = date_str_compact
        params["chnl_gb"] = "01"

        extra_headers = {}
        extra_headers["Referer"] = "https://m.lotteimall.com/display/viewTvSchedule.lotte"
        extra_headers["X-Requested-With"] = "XMLHttpRequest"

        endpoints = []
        endpoints.append(self.primary_api_url)
        endpoints.append(self.ajax_api_url)
        endpoints.append(self.pc_ajax_url)
        endpoints.append(self.pc_schedule_url)

        last_error: Exception = ScraperParseException("AllEndpointsFailed")

        for endpoint_url in endpoints:
            try:
                raw_text = await self.fetch_text(
                    session,
                    endpoint_url,
                    params=params,
                    extra_headers=extra_headers
                )
                parsed_items = self._parse_response(raw_text, date_str_iso)
                return parsed_items
            except Exception as err:
                last_error = err
                continue

        raise last_error

    def _parse_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses 롯데홈쇼핑 schedule response (JSON payload, embedded script JSON, or HTML fragment).
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

        # Try extracting JSON arrays/objects embedded inside <script> tags
        script_json_matches = re.findall(
            r"(?:scheduleList|tvScheduleList|broadList|goodsList| INITIAL_STATE|__NEXT_DATA__)\s*[=:]\s*(\[.*?\]|\{.*?\})\s*[;<\n]",
            raw_text,
            re.DOTALL
        )
        for match_str in script_json_matches:
            try:
                json_obj = json.loads(match_str)
                return self._extract_from_json(json_obj, date_str_iso)
            except Exception:
                continue

        soup = BeautifulSoup(raw_text, "html.parser")
        next_script = soup.select_one("script#__NEXT_DATA__")
        if (next_script is not None and next_script.string):
            try:
                json_data = json.loads(next_script.string)
                return self._extract_from_json(json_data, date_str_iso)
            except Exception as e:
                raise ScraperParseException(f"NextDataParseError: {str(e)}") from e

        product_elements = soup.select(
            "li.broad-item, div.broad-item, li.schedule-item, div.prd-item, ul.list_tv > li, "
            "div.tv_schedule_list li, div.schedule_list li, div.box_prd, li[data-goods-no]"
        )

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

            name_node = elem.select_one(".prd_name, .tit_prd, .name, .title, .txt_tit, p.tit, a.link")
            if (name_node is None):
                continue

            product_name = name_node.get_text(strip=True)
            if (not product_name):
                continue

            cat_node = elem.select_one(".cate, .category")
            category_text = "건강식품"
            if (cat_node is not None):
                category_text = cat_node.get_text(strip=True)

            if (not self.is_health_food_item(product_name, category_text)):
                continue

            price_str = ""
            price_node = elem.select_one(".price, .txt_price, .num, strong.price")
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
            raise ScraperParseException("SelectorNotFound")

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
