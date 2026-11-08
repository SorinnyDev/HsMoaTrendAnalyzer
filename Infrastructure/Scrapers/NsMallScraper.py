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

class NsMallScraper(BaseChannelScraper):
    """
    NS홈쇼핑 mobile public schedule scraper (D+0 ~ D+3).
    Queries verified backend REST JSON API https://mapi.nsmall.com/md/api/v1/display/media/schedule/all/{YYYYMMDD}.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="NS홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.all_schedule_api_url = "https://mapi.nsmall.com/md/api/v1/display/media/schedule/all"
        self.tv_schedule_api_url = "https://mapi.nsmall.com/md/api/v1/display/media/schedule/tv"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches NS홈쇼핑 schedule from start_date to end_date (capped at D+0 ~ D+3).
        """
        collected_items: List[ScheduleItem] = []
        max_allowed_date = start_date + timedelta(days=self.max_days_ahead)

        target_end_date = end_date
        if (target_end_date > max_allowed_date):
            target_end_date = max_allowed_date

        async with self.create_stealth_session() as session:
            first_date_compact = start_date.strftime("%Y%m%d")
            await self.warm_up_session(session, f"https://m.nsmall.com/store/fixed/schedule/tv/{first_date_compact}")
            current_date = start_date
            while (current_date <= target_end_date):
                date_str_compact = current_date.strftime("%Y%m%d")
                date_str_iso = current_date.strftime("%Y-%m-%d")

                daily_items = await self._fetch_daily_schedule(session, date_str_compact, date_str_iso)
                for (item) in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await self.sleep_with_jitter(0.75, 1.65)

        return collected_items

    async def _fetch_daily_schedule(
        self,
        session: aiohttp.ClientSession,
        date_str_compact: str,
        date_str_iso: str
    ) -> List[ScheduleItem]:
        """
        Calls NS홈쇼핑 mapi.nsmall.com REST endpoint for a single date (YYYYMMDD).
        """
        params = {}
        params["formDate"] = date_str_compact

        extra_headers = {}
        extra_headers["Accept"] = "application/json, text/plain, */*"
        extra_headers["Origin"] = "https://m.nsmall.com"
        extra_headers["Referer"] = f"https://m.nsmall.com/store/fixed/schedule/tv/{date_str_compact}"

        primary_url = f"{self.all_schedule_api_url}/{date_str_compact}"
        fallback_url = f"{self.tv_schedule_api_url}/{date_str_compact}"

        try:
            raw_text = await self.fetch_text(session, primary_url, params=params, extra_headers=extra_headers)
        except Exception:
            raw_text = await self.fetch_text(session, fallback_url, params=params, extra_headers=extra_headers)

        return self._parse_json_response(raw_text, date_str_iso)

    def _parse_json_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses NS홈쇼핑 mapi JSON response (data.resultData.totalOrgan).
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        try:
            payload = json.loads(raw_text.strip())
        except Exception as e:
            raise ScraperParseException(f"JSONDecodeError: {str(e)}") from e

        data_node = payload.get("data")
        if (not isinstance(data_node, dict)):
            raise ScraperParseException("MissingDataNode")

        result_data = data_node.get("resultData")
        if (not isinstance(result_data, dict)):
            raise ScraperParseException("MissingResultDataNode")

        total_organ = result_data.get("totalOrgan")
        if (not isinstance(total_organ, list)):
            raise ScraperParseException("MissingTotalOrganList")

        items: List[ScheduleItem] = []

        for (organ) in total_organ:
            if (not isinstance(organ, dict)):
                continue

            raw_start = str(organ.get("startDtm") or "")
            time_str = "00:00"
            if (len(raw_start) >= 12 and raw_start[:12].isdigit()):
                time_str = f"{raw_start[8:10]}:{raw_start[10:12]}"

            goods_candidates: List[Dict[str, Any]] = []
            main_goods = organ.get("goods")
            if (isinstance(main_goods, dict)):
                goods_candidates.append(main_goods)

            rel_list = organ.get("relTotalOrgan")
            if (isinstance(rel_list, list)):
                for (rel_item) in rel_list:
                    if (isinstance(rel_item, dict)):
                        rel_goods = rel_item.get("goods")
                        if (isinstance(rel_goods, dict)):
                            goods_candidates.append(rel_goods)

            for (goods_obj) in goods_candidates:
                brand_nm = str(goods_obj.get("brandNm") or "").strip()
                product_nm = str(goods_obj.get("productNm") or "").strip()
                if (not product_nm):
                    continue

                if (brand_nm and brand_nm not in product_nm):
                    full_name = f"[{brand_nm}] {product_nm}"
                else:
                    full_name = product_nm

                head_copy = str(goods_obj.get("headCopy") or "").strip()
                if (not self.is_health_food_item(full_name, head_copy)):
                    continue

                raw_price = str(
                    goods_obj.get("applyPrice")
                    or goods_obj.get("dcPrice")
                    or goods_obj.get("salePrice")
                    or ""
                )
                price_str = re.sub(r"[^\d]", "", raw_price)

                goods_cd = str(goods_obj.get("goodsCd") or "").strip()
                link_url = ""
                if (goods_cd):
                    link_url = f"https://m.nsmall.com/goods/{goods_cd}"

                schedule_item = ScheduleItem(
                    broadcast_date=date_str_iso,
                    broadcast_time=time_str,
                    channel=self.channel_name,
                    category="건강식품",
                    product_name=full_name,
                    price=price_str,
                    link_url=link_url
                )
                items.append(schedule_item)

        return items
