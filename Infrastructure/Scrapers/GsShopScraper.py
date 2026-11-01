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
    Uses lightweight aiohttp requests without headless browsers.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="GS SHOP", max_days_ahead=6, timeout_seconds=15)
        self.base_url = "https://m.gsshop.com/shop/tv/tvSchedule.gs"
        self.fallback_url = "https://www.gsshop.com/shop/tv/tvSchedule.gs"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches GS SHOP schedule from start_date to end_date (capped at D+0 ~ D+6).
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

                params = {}
                params["searchDate"] = date_str_compact

                extra_headers = {}
                extra_headers["Referer"] = "https://m.gsshop.com/"

                try:
                    raw_text = await self.fetch_text(session, self.base_url, params=params, extra_headers=extra_headers)
                except Exception:
                    raw_text = await self.fetch_text(session, self.fallback_url, params=params, extra_headers=extra_headers)

                daily_items = self._parse_response(raw_text, date_str_iso)
                for item in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await asyncio.sleep(0.3)

        return collected_items

    def _parse_response(self, raw_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses GS SHOP schedule response (supports JSON payload or HTML DOM).
        Raises ScraperParseException if no valid schedule structure is found.
        """
        if (not raw_text or len(raw_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        items: List[ScheduleItem] = []
        parsed_structure_found = False

        # 1. Try JSON parsing if response is JSON
        stripped = raw_text.strip()
        if (stripped.startswith("{") or stripped.startswith("[")):
            try:
                data = json.loads(stripped)
                json_items = self._extract_from_json(data, date_str_iso)
                parsed_structure_found = True
                for item in json_items:
                    items.append(item)
                return items
            except Exception as e:
                raise ScraperParseException(f"JSONParseError: {str(e)}") from e

        # 2. Parse HTML DOM via BeautifulSoup
        soup = BeautifulSoup(raw_text, "html.parser")
        product_Elements = soup.select(
            "li.prd-item, div.prd-item, dl.prd-info, div.schedule-item, article.tv-item, li[data-prd-cd], div.broad-item"
        )

        if (len(product_Elements) == 0):
            # Check embedded JSON script state (e.g., __NEXT_DATA__ or initial state)
            next_data_script = soup.select_one("script#__NEXT_DATA__")
            if (next_data_script is not None and next_data_script.string):
                try:
                    json_data = json.loads(next_data_script.string)
                    json_items = self._extract_from_json(json_data, date_str_iso)
                    return json_items
                except Exception as e:
                    raise ScraperParseException(f"NextDataParseError: {str(e)}") from e

            raise ScraperParseException("SelectorNotFound")

        parsed_structure_found = True
        current_time_str = "00:00"

        for elem in product_Elements:
            time_node = elem.select_one(".time, .broad-time, .txt-time, span.hour, strong.time")
            if (time_node is not None):
                extracted_time = time_node.get_text(strip=True)
                time_match = re.search(r"(\d{1,2}:\d{2})", extracted_time)
                if (time_match is not None):
                    current_time_str = time_match.group(1)

            name_node = elem.select_one(".prd-name, .tit, .name, dt, a.prd-link, p.title")
            if (name_node is None):
                continue

            product_name = name_node.get_text(strip=True)
            if (not product_name):
                continue

            cat_node = elem.select_one(".cate, .category, .badge")
            category_text = "건강식품"
            if (cat_node is not None):
                category_text = cat_node.get_text(strip=True)

            if (not self.is_health_food_item(product_name, category_text)):
                continue

            price_str = ""
            price_node = elem.select_one(".price, .set-price, .selling-price, strong.num")
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

        if (not parsed_structure_found):
            raise ScraperParseException("SelectorNotFound")

        return items

    def _extract_from_json(self, data: Any, date_str_iso: str) -> List[ScheduleItem]:
        """
        Recursively extracts schedule items from JSON payload structures.
        """
        results: List[ScheduleItem] = []
        candidates: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if (isinstance(node, dict)):
                has_name = False
                if ("prdNm" in node or "goodsNm" in node or "itemName" in node or "productName" in node or "title" in node):
                    has_name = True
                if (has_name):
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
                item_dict.get("prdNm")
                or item_dict.get("goodsNm")
                or item_dict.get("productName")
                or item_dict.get("itemName")
                or item_dict.get("title")
                or ""
            ).strip()

            if (not product_name):
                continue

            category_text = str(item_dict.get("catNm") or item_dict.get("category") or "")
            if (not self.is_health_food_item(product_name, category_text)):
                continue

            raw_time = str(item_dict.get("broadTime") or item_dict.get("startTime") or item_dict.get("pgmStartTime") or "00:00")
            time_match = re.search(r"(\d{1,2}:\d{2})", raw_time)
            if (time_match is not None):
                time_str = time_match.group(1)
            else:
                time_str = "00:00"

            raw_price = str(item_dict.get("salePrice") or item_dict.get("price") or item_dict.get("prmPrice") or "")
            price_str = re.sub(r"[^\d]", "", raw_price)

            link_url = str(item_dict.get("linkUrl") or item_dict.get("prdUrl") or "")

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

