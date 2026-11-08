from datetime import date, timedelta
from typing import List, Dict, Any
import asyncio
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
    Uses verified GET endpoint /main/broadSchedule/{YYYYMMDD}?broadType=ALL&startDate={YYYYMMDD}.
    """

    def __init__(self) -> None:
        super().__init__(channel_name="GS SHOP", max_days_ahead=6, timeout_seconds=15)
        self.base_schedule_url = "https://m.gsshop.com/main/broadSchedule"

    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches GS SHOP schedule from start_date to end_date (capped at D+0 ~ D+6).
        """
        collected_items: List[ScheduleItem] = []
        max_allowed_date = start_date + timedelta(days=self.max_days_ahead)

        target_end_date = end_date
        if (target_end_date > max_allowed_date):
            target_end_date = max_allowed_date

        async with self.create_stealth_session() as session:
            await self.warm_up_session(session, self.base_schedule_url)
            current_date = start_date
            while (current_date <= target_end_date):
                date_str_compact = current_date.strftime("%Y%m%d")
                date_str_iso = current_date.strftime("%Y-%m-%d")

                daily_items = await self._fetch_daily_schedule(session, date_str_compact, date_str_iso)
                for (item) in daily_items:
                    collected_items.append(item)

                current_date = current_date + timedelta(days=1)
                await self.sleep_with_jitter(0.65, 1.55)

        return collected_items

    async def _fetch_daily_schedule(
        self,
        session: aiohttp.ClientSession,
        date_str_compact: str,
        date_str_iso: str
    ) -> List[ScheduleItem]:
        """
        Queries GS SHOP mobile schedule endpoint for a specific date (YYYYMMDD).
        """
        url = f"{self.base_schedule_url}/{date_str_compact}"
        params = {}
        params["broadType"] = "ALL"
        params["startDate"] = date_str_compact

        extra_headers = {}
        extra_headers["Referer"] = "https://m.gsshop.com/main/broadSchedule"
        extra_headers["X-Requested-With"] = "XMLHttpRequest"

        raw_html = await self.fetch_text(session, url, params=params, extra_headers=extra_headers)
        return self._parse_html_dom(raw_html, date_str_iso)

    def _parse_html_dom(self, html_text: str, date_str_iso: str) -> List[ScheduleItem]:
        """
        Parses GS SHOP /main/broadSchedule HTML for <article class="prd-item"> blocks.
        """
        if (not html_text or len(html_text.strip()) == 0):
            raise ScraperParseException("EmptyResponse")

        soup = BeautifulSoup(html_text, "html.parser")
        article_elements = soup.select("article.prd-item")
        if (len(article_elements) == 0):
            raise ScraperParseException("SelectorNotFound")

        items: List[ScheduleItem] = []

        for (elem) in article_elements:
            time_str = self._extract_broadcast_time(elem)

            brand_text = ""
            brand_node = elem.select_one("em.brand-name")
            if (brand_node is not None):
                brand_text = brand_node.get_text(strip=True)

            name_text = ""
            name_node = elem.select_one("span.prd-name, dt.prd-copy")
            if (name_node is not None):
                name_text = name_node.get_text(strip=True)

            if (not name_text):
                continue

            if (brand_text and brand_text not in name_text):
                product_name = f"[{brand_text}] {name_text}"
            else:
                product_name = name_text

            if (not self.is_health_food_item(product_name, "")):
                continue

            price_str = ""
            price_node = elem.select_one("span.set-price strong, dd.prd-price strong")
            if (price_node is not None):
                digits = re.sub(r"[^\d]", "", price_node.get_text(strip=True))
                if (digits):
                    price_str = digits

            prd_cd = str(elem.get("data-prdcd") or "").strip()
            link_url = ""
            link_node = elem.select_one("a.prd-link[href], a[href*='prdid=']")
            if (link_node is not None):
                href_val = str(link_node.get("href") or "").strip()
                if (href_val.startswith("http")):
                    link_url = href_val
                elif (href_val.startswith("/")):
                    link_url = f"https://m.gsshop.com{href_val}"

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
            items.append(schedule_item)

        return items

    def _extract_broadcast_time(self, article_elem: Any) -> str:
        """
        Extracts HH:MM broadcast start time from parent prd-group attributes or header text.
        """
        parent_a = article_elem.find_parent("div", attrs={"data-broadstartdate": True})
        if (parent_a is None):
            parent_a = article_elem.find_parent("div", attrs={"data-broadStartDate": True})
        if (parent_a is not None):
            raw_start = str(parent_a.get("data-broadstartdate") or parent_a.get("data-broadStartDate") or "")
            if (len(raw_start) >= 12 and raw_start[:12].isdigit()):
                return f"{raw_start[8:10]}:{raw_start[10:12]}"

        parent_b = article_elem.find_parent("div", attrs={"data-pgminfo": True})
        if (parent_b is not None):
            raw_pgm = str(parent_b.get("data-pgminfo") or "")
            if (len(raw_pgm) >= 12 and raw_pgm[:12].isdigit()):
                return f"{raw_pgm[8:10]}:{raw_pgm[10:12]}"

            ttl_node = parent_b.select_one("header.prd-group-head h3.ttl, .ttl")
            if (ttl_node is not None):
                time_match = re.search(r"(\d{1,2}:\d{2})", ttl_node.get_text(strip=True))
                if (time_match is not None):
                    parts = time_match.group(1).split(":")
                    return f"{int(parts[0]):02d}:{parts[1]}"

        return "00:00"
