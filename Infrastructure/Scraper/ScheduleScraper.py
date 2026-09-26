import logging
import asyncio
from typing import List, Set
from Domain.Models.Schedule import ScheduleItem
from Infrastructure.Scraper.PlaywrightClient import PlaywrightClient

logger = logging.getLogger(__name__)

class ScheduleScraper(PlaywrightClient):
    """
    Scraper specialized in extracting schedule data from HsMoa.
    """

    def __init__(self) -> None:
        super().__init__()
        # Filtering keywords to avoid non-health products if needed
        # We can still keep this just in case, but checking category is more reliable
        self.TARGET_CATEGORY_KEYWORD = "건강식품"

    async def scrape_daily_schedule(self, date_str: str) -> tuple[List[ScheduleItem], int]:
        """
        Fetches the schedule data for the given date using the internal API.
        Date should be in 'YYYY-MM-DD' format.
        Returns a tuple of (filtered_items, total_items_count).
        """
        if (not self.page):
            logger.error("Page not initialized.")
            return [], 0

        # We start at hour 0, and ask for 24 hours of data
        api_url = f"https://datahub.hsmoa.com/next-api/schedule?date={date_str}&start_hour=0&hour_num=24"
        logger.info(f"Fetching API: {api_url}")
        
        filtered_items: List[ScheduleItem] = []
        total_items = 0
        
        try:
            resp = await self.page.request.get(api_url)
            if (resp.status != 200):
                logger.error(f"Failed to fetch data, API returned status {resp.status}")
                return [], 0
            
            data = await resp.json()
            
            # The structure contains "before_live", "live", and "after_live" keys.
            for section in ["before_live", "live", "after_live"]:
                if (section not in data):
                    continue
                
                for block in data[section]:
                    schedules = block.get("schedules", [])
                    for item_data in schedules:
                        total_items += 1
                        
                        cat1 = item_data.get("category1", "") or ""
                        cat2 = item_data.get("category2", "") or ""
                        cat3 = item_data.get("category3", "") or ""
                        
                        category_path = f"{cat1} > {cat2} > {cat3}".strip(" >")
                        
                        # We only want items related to Health Foods
                        if (self.TARGET_CATEGORY_KEYWORD in category_path):
                            start_dt_str = item_data.get("start_datetime", "")
                            # Parse out time portion if needed, e.g. "2026-04-18T10:00:00+09:00" -> "10:00"
                            time_str = ""
                            if (start_dt_str and "T" in start_dt_str):
                                time_str = start_dt_str.split("T")[1][:5]
                            
                            price = item_data.get("price")
                            price_str = str(price) if price is not None else ""
                            
                            item = ScheduleItem(
                                broadcast_date=date_str,
                                broadcast_time=time_str,
                                channel=item_data.get("tv_channel", "Unknown"),
                                category=category_path,
                                product_name=item_data.get("name", "Unknown"),
                                price=price_str,
                                link_url=item_data.get("url", "")
                            )
                            filtered_items.append(item)
                            
        except Exception as e:
            logger.error(f"Error while fetching API data: {str(e)}")
            
        logger.info(f"Total schedules found: {total_items}, Health food items: {len(filtered_items)}")
        return filtered_items, total_items
