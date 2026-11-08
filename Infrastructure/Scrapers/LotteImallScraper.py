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

class LotteImallScraper(BaseChannelScraper):
    """
    롯데홈쇼핑 mobile public schedule scraper (D+0 ~ D+3).
    Queries verified mobile JSON endpoints (/schedule/scheduleLive.lotte and /schedule/scheduleOne.lotte).
    """

    def __init__(self) -> None:
        super().__init__(channel_name="롯데홈쇼핑", max_days_ahead=3, timeout_seconds=15)
        self.live_schedule_url = "https://m.lotteimall.com/schedule/scheduleLive.lotte"
        self.one_schedule_url = "https://m.lotteimall.com/schedule/scheduleOne.lotte"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches 롯데홈쇼핑 schedule from start_date to end_date (capped at D+0 ~ D+3).
        """
        collected_items: List[ScheduleItem] = []
        max_allowed_date = start_date + timedelta(days=self.max_days_ahead)

        target_end_date = end_date
        if (target_end_date > max_allowed_date):
            target_end_date = max_allowed_date

        async with self.create_stealth_session() as session:
            await self.warm_up_session(session, "https://m.lotteimall.com/main/tvschedule.lotte?direct=L&isAlone=T")
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
        Fetches both Live TV and One TV schedule JSONs from m.lotteimall.com for a given date.
        """
        params = {}
        params["bdDate"] = date_str_compact

        extra_headers = {}
        extra_headers["Accept"] = "application/json, text/plain, */*"
        extra_headers["X-Requested-With"] = "XMLHttpRequest"
        extra_headers["Referer"] = "https://m.lotteimall.com/main/tvschedule.lotte?direct=L&isAlone=T"

        combined_items: List[ScheduleItem] = []

        live_text = await self.fetch_text(session, self.live_schedule_url, params=params, extra_headers=extra_headers)
        live_items = self._parse_json_response(live_text, date_str_iso)
        for (item) in live_items:
            combined_items.append(item)

        try:
            one_text = await self.fetch_text(session, self.one_schedule_url, params=params, extra_headers=extra_headers)
            one_items = self._parse_json_response(one_text, date_str_iso)
            for (item) in one_items:
                combined_items.append(item)
        except Exception as e:
            logger.debug(f"[{self.channel_name}] OneTV auxiliary fetch skipped: {e}")

        return combined_items

    def _parse_json_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses Lotte scheduleLive.lotte / scheduleOne.lotte JSON payload (body.prod).
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        try:
            payload = json.loads(raw_text.strip())
        except Exception as e:
            raise ScraperParseException(f"JSONDecodeError: {str(e)}") from e

        body_node = payload.get("body")
        if (not isinstance(body_node, dict)):
            raise ScraperParseException("MissingBodyNode")

        prod_list = body_node.get("prod")
        if (not isinstance(prod_list, list)):
            raise ScraperParseException("MissingProdList")

        items: List[ScheduleItem] = []

        for (prod_entry) in prod_list:
            if (not isinstance(prod_entry, dict)):
                continue

            raw_stime = str(prod_entry.get("stime") or "00:00").strip()
            time_match = re.search(r"(\d{1,2}:\d{2})", raw_stime)
            if (time_match is not None):
                parts = time_match.group(1).split(":")
                time_str = f"{int(parts[0]):02d}:{parts[1]}"
            else:
                time_str = "00:00"

            candidates: List[Dict[str, Any]] = []
            candidates.append(prod_entry)

            rel_list = prod_entry.get("related")
            if (isinstance(rel_list, list)):
                for (rel_obj) in rel_list:
                    if (isinstance(rel_obj, dict)):
                        candidates.append(rel_obj)

            rel_add_list = prod_entry.get("relatedAdd")
            if (isinstance(rel_add_list, list)):
                for (rel_add_obj) in rel_add_list:
                    if (isinstance(rel_add_obj, dict)):
                        candidates.append(rel_add_obj)

            for (cand) in candidates:
                raw_name = str(cand.get("name") or "").strip()
                if (not raw_name):
                    continue

                brand_nm = str(cand.get("brand") or "").strip()
                if (brand_nm and brand_nm not in raw_name):
                    product_name = f"[{brand_nm}] {raw_name}"
                else:
                    product_name = raw_name

                if (not self.is_health_food_item(product_name, "")):
                    continue

                raw_price = str(cand.get("price_disc") or cand.get("price_orig") or "")
                price_str = re.sub(r"[^\d]", "", raw_price)
                if (price_str == "0"):
                    price_str = ""

                goods_no = str(cand.get("goodsNo") or "").strip()
                link_info = str(cand.get("linkInfo") or "").strip()
                link_url = ""
                if (link_info.startswith("http")):
                    link_url = link_info
                elif (link_info.startswith("/")):
                    link_url = f"https://m.lotteimall.com{link_info}"
                elif (goods_no):
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
                items.append(schedule_item)

        return items
