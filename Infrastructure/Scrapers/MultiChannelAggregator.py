from dataclasses import dataclass
from datetime import date
from typing import List, Dict, Tuple
import asyncio
import logging
from Domain.Models.Schedule import ScheduleItem
from Infrastructure.Scrapers.BaseChannelScraper import BaseChannelScraper
from Infrastructure.Scrapers.GsShopScraper import GsShopScraper
from Infrastructure.Scrapers.CjOnstyleScraper import CjOnstyleScraper
from Infrastructure.Scrapers.NsMallScraper import NsMallScraper
from Infrastructure.Scrapers.LotteImallScraper import LotteImallScraper
from Infrastructure.Scrapers.HmallScraper import HmallScraper

logger = logging.getLogger(__name__)

@dataclass
class ChannelCrawlResult:
    """
    Value object representing the crawl outcome of a single home shopping channel.
    """
    channel_name: str
    is_success: bool
    items: List[ScheduleItem]
    error_message: str = ""
    consecutive_failures: int = 0

class MultiChannelAggregator:
    """
    Orchestrates parallel schedule collection across the 5 major home shopping channels.
    Uses asyncio.gather(..., return_exceptions=True) for fault-tolerant execution.
    """

    def __init__(self, scrapers: List[BaseChannelScraper] = None) -> None:
        if (scrapers is not None):
            self.scrapers = scrapers
        else:
            default_scrapers: List[BaseChannelScraper] = []
            default_scrapers.append(GsShopScraper())
            default_scrapers.append(CjOnstyleScraper())
            default_scrapers.append(NsMallScraper())
            default_scrapers.append(LotteImallScraper())
            default_scrapers.append(HmallScraper())
            self.scrapers = default_scrapers

    async def collect_all(self, start_date: date, end_date: date) -> Tuple[List[ScheduleItem], List[ChannelCrawlResult]]:
        """
        Concurrently fetches schedules from all 5 channel scrapers.
        Even if one or more channels fail, normal channels' items are aggregated without interruption.
        """
        tasks = []
        for scraper in self.scrapers:
            tasks.append(scraper.fetch_schedule(start_date, end_date))

        logger.info(f"Starting parallel schedule collection across {len(self.scrapers)} channels ({start_date} ~ {end_date})...")
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        aggregated_items: List[ScheduleItem] = []
        channel_results: List[ChannelCrawlResult] = []

        for idx in range(len(self.scrapers)):
            scraper = self.scrapers[idx]
            outcome = raw_results[idx]
            channel_name = scraper.channel_name

            if (isinstance(outcome, Exception)):
                err_name = type(outcome).__name__
                err_detail = str(outcome).strip()
                if (err_detail):
                    formatted_error = err_detail
                else:
                    formatted_error = err_name

                logger.error(f"[{channel_name}] Collection failed: {formatted_error}")
                fail_result = ChannelCrawlResult(
                    channel_name=channel_name,
                    is_success=False,
                    items=[],
                    error_message=formatted_error,
                    consecutive_failures=1
                )
                channel_results.append(fail_result)
            else:
                channel_items: List[ScheduleItem] = outcome
                for item in channel_items:
                    aggregated_items.append(item)

                logger.info(f"[{channel_name}] Collection succeeded ({len(channel_items)} items).")
                success_result = ChannelCrawlResult(
                    channel_name=channel_name,
                    is_success=True,
                    items=channel_items,
                    error_message="",
                    consecutive_failures=0
                )
                channel_results.append(success_result)

        return aggregated_items, channel_results

