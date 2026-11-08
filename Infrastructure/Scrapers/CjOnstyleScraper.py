from datetime import date, datetime, timedelta, timezone
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

class CjOnstyleScraper(BaseChannelScraper):
    """
    CJ온스타일 mobile public schedule scraper (D+0 ~ D+6).
    Uses lightweight aiohttp requests without headless browsers.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="CJ온스타일", max_days_ahead=6, timeout_seconds=15)
        self.base_url = "https://display.cjonstyle.com/c/rest/tv/tvSchedule"
        self.fallback_url = "https://display.cjonstyle.com/p/tv/tvSchedule"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches CJ온스타일 schedule from start_date to end_date (capped at D+0 ~ D+6).
        """
        collected_items: List[ScheduleItem] = []
        max_allowed_date = start_date + timedelta(days=self.max_days_ahead)

        target_end_date = end_date
        if (target_end_date > max_allowed_date):
            target_end_date = max_allowed_date

        async with self.create_stealth_session() as session:
            await self.warm_up_session(session, self.fallback_url)
            current_date = start_date
            while (current_date <= target_end_date):
                date_str_compact = current_date.strftime("%Y%m%d")
                date_str_iso = current_date.strftime("%Y-%m-%d")

                params = {}
                params["bdDt"] = date_str_compact
                params["searchDate"] = date_str_compact

                extra_headers = {}
                extra_headers["Referer"] = "https://display.cjonstyle.com/p/tv/tvSchedule"

                try:
                    raw_text = await self.fetch_text(session, self.base_url, params=params, extra_headers=extra_headers)
                except Exception:
                    raw_text = await self.fetch_text(session, self.fallback_url, params=params, extra_headers=extra_headers)

                daily_items = self._parse_response(raw_text, date_str_iso)
                for (item) in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await self.sleep_with_jitter(0.65, 1.55)

        return collected_items

    def _parse_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses CJ온스타일 schedule response (JSON or HTML).
        Raises ScraperParseException on failure.
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

        soup = BeautifulSoup(raw_text, "html.parser")
        product_elements = soup.select(
            "li.schedule-item, div.schedule-item, li.prd-item, div.item-box, ul.list-schedule > li"
        )

        if (len(product_elements) == 0):
            script_node = soup.select_one("script#__NEXT_DATA__")
            if (script_node is not None and script_node.string):
                try:
                    json_data = json.loads(script_node.string)
                    return self._extract_from_json(json_data, date_str_iso)
                except Exception as e:
                    raise ScraperParseException(f"NextDataParseError: {str(e)}") from e

            raise ScraperParseException("SelectorNotFound")

        items: List[ScheduleItem] = []
        current_time_str = "00:00"

        for elem in product_elements:
            time_node = elem.select_one(".time, .broad-time, .txt-time, strong.hour")
            if (time_node is not None):
                time_match = re.search(r"(\d{1,2}:\d{2})", time_node.get_text(strip=True))
                if (time_match is not None):
                    current_time_str = time_match.group(1)

            name_node = elem.select_one(".prd-name, .item-name, .tit, .title, a.link-prd")
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
            price_node = elem.select_one(".price, .num, .sale-price")
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
                    link_url = f"https://display.cjonstyle.com{href_val}"

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

    def _format_cj_time(self, raw_val: Any, fallback_time: str = "00:00") -> str:
        """
        Formats CJ온스타일 timestamp (epoch ms int/str or HH:MM / YYYYMMDDHHMMSS) into HH:MM (KST).
        """
        if (raw_val is None):
            return fallback_time

        raw_str = str(raw_val).strip()
        if (not raw_str):
            return fallback_time

        time_match = re.search(r"(\d{1,2}:\d{2})", raw_str)
        if (time_match is not None):
            parts = time_match.group(1).split(":")
            return f"{int(parts[0]):02d}:{parts[1]}"

        if (raw_str.isdigit()):
            if (len(raw_str) == 13):
                try:
                    kst_tz = timezone(timedelta(hours=9))
                    dt_obj = datetime.fromtimestamp(int(raw_str) / 1000.0, tz=kst_tz)
                    return dt_obj.strftime("%H:%M")
                except Exception:
                    return fallback_time
            if (len(raw_str) == 14 and raw_str.startswith("20")):
                return f"{raw_str[8:10]}:{raw_str[10:12]}"

        return fallback_time

    def _extract_from_json(self, data: Any, date_str_iso: str) -> List[ScheduleItem]:
        """
        Extracts ScheduleItem objects from CJ온스타일 JSON structure.
        Propagates parent program broadcast time (bdStrDtm) down to child itemList entries.
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any, inherited_time: str = "00:00") -> None:
            if (isinstance(node, dict)):
                current_time = inherited_time
                for (t_key) in ["bdStrDtm", "bdStrtDtm", "startTime", "broadTime"]:
                    if (t_key in node and node[t_key] is not None):
                        current_time = self._format_cj_time(node[t_key], current_time)
                        break

                has_child_items = False
                if ("itemList" in node and isinstance(node["itemList"], list) and len(node["itemList"]) > 0):
                    has_child_items = True

                if (not has_child_items):
                    if ("itemNm" in node or "prdNm" in node or "goodsNm" in node or "pgmNm" in node):
                        enriched = {}
                        for (k, v) in node.items():
                            enriched[k] = v
                        enriched["_resolved_time"] = current_time
                        candidates.append(enriched)

                for (val) in node.values():
                    _walk(val, current_time)
            elif (isinstance(node, list)):
                for (sub) in node:
                    _walk(sub, inherited_time)

        _walk(data, "00:00")
        if (len(candidates) == 0):
            raise ScraperParseException("SelectorNotFound")

        for (item_dict) in candidates:
            product_name = str(
                item_dict.get("itemNm")
                or item_dict.get("prdNm")
                or item_dict.get("goodsNm")
                or item_dict.get("pgmNm")
                or ""
            ).strip()

            if (not product_name):
                continue

            category_text = str(item_dict.get("catNm") or item_dict.get("ctgNm") or item_dict.get("category") or "")
            if (not self.is_health_food_item(product_name, category_text)):
                continue

            time_str = str(item_dict.get("_resolved_time") or "00:00")

            raw_price = str(item_dict.get("slPrc") or item_dict.get("salePrice") or item_dict.get("price") or "")
            price_str = re.sub(r"[^\d]", "", raw_price)

            item_cd = str(item_dict.get("itemCd") or item_dict.get("prdCd") or "")
            link_url = str(item_dict.get("linkUrl") or "")
            if (not link_url and item_cd):
                link_url = f"https://display.cjonstyle.com/p/item/{item_cd}"

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

