import os
import asyncio
import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import List
from Infrastructure.Scraper.ScheduleScraper import ScheduleScraper
from Infrastructure.Persistence.ExcelRepository import ExcelRepository
from Infrastructure.Persistence.SqliteRepository import SqliteRepository
from Application.Service.TrendManager import TrendManager
from Domain.Models.Schedule import ScheduleItem

logger = logging.getLogger(__name__)

class TrendAnalysisService:
    """
    Application service that coordinates scraping, data persistence, and trend analysis.
    """

    def __init__(self) -> None:
        self.scraper = ScheduleScraper()
        self.repository = ExcelRepository()
        self.db_repo = SqliteRepository()
        self.trend_manager = TrendManager()

    def deduplicate_items(self, items: List[ScheduleItem]) -> List[ScheduleItem]:
        """
        Deduplicates a list of schedule items based on exact product name.
        """
        seen_names = set()
        dedup_items = []
        for item in items:
            # We can use the product name as the unique key for "exactly the same product"
            if item.product_name not in seen_names:
                seen_names.add(item.product_name)
                dedup_items.append(item)
        return dedup_items

    async def _crawl_and_save_date(self, target_date_str: str) -> bool:
        """
        Core logic to crawl and save a single date.
        Returns True if schedule data exists (even if no health food), False if totally no schedule.
        """
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        month_str = target_date.strftime("%m월")
        short_date_str = target_date.strftime("%m-%d")

        logger.info(f"Starting collection for {target_date_str}...")
        raw_items, total_items = await self.scraper.scrape_daily_schedule(target_date_str)
        
        if total_items == 0:
            logger.info(f"No schedule found for {target_date_str} (Schedule completely empty).")
            # We don't mark NO_DATA here if it's in the future because it might be uploaded tomorrow.
            return False
            
        if (len(raw_items) > 0):
            dedup_items = self.deduplicate_items(raw_items)
            logger.info(f"Raw items: {len(raw_items)}, Deduplicated items: {len(dedup_items)}")

            sheet_name = short_date_str
            file_path = self.repository.save_schedule(dedup_items, month_str, sheet_name)
            logger.info(f"Successfully saved deduplicated schedule data to {file_path}")
            
            # 5. Analyze Trends with Gemini and Update Statistics
            await self.trend_manager.process_daily_trends(raw_items, target_date_str)
        else:
            logger.warning("No health food items found for today.")

        self.db_repo.mark_date_status(target_date_str, 'SUCCESS', len(raw_items))
        return True

    async def run_daily_collection(self, target_date_str: str = None) -> None:
        """
        Executes the collection process for a single day.
        """
        if not target_date_str:
            target_date_str = datetime.now().strftime("%Y-%m-%d")
            
        await self.scraper.initialize(headless=True)
        try:
            if not await self.scraper.login("https://datahub.hsmoa.com/login"):
                logger.error("Failed to login. Aborting collection.")
                return

            await self._crawl_and_save_date(target_date_str)

        except Exception as e:
            logger.error(f"Unexpected error in single collection: {str(e)}")
        finally:
            await self.scraper.close()

    async def run_sliding_window_sync(self) -> None:
        """
        Coordinates the 14-day sliding window synchronization process (Today + 13 days).
        Includes crawling, upserting to persistence, and AI reporting.
        """
        from Domain.HomeShopping.Service.WindowService import WindowService
        from Infrastructure.Persistence.SyncManager import SyncManager
        from Application.Service.NotificationService import NotificationService
        
        window_service = WindowService(window_size=14)
        sync_manager = SyncManager()
        
        target_dates = window_service.get_sliding_window_dates()
        all_collected_items: List[ScheduleItem] = []
        
        logger.info(f"Starting 14-day sliding window sync for: {target_dates[0]} ~ {target_dates[-1]}")
        
        await self.scraper.initialize(headless=True)
        try:
            # Explicit if with parentheses
            if (not await self.scraper.login("https://datahub.hsmoa.com/login")):
                logger.error("Authentication failed. Aborting sync.")
                return

            for date_str in target_dates:
                # Explicit try-except for each date to ensure one failure doesn't stop the loop
                try:
                    logger.info(f"Crawling window date: {date_str}")
                    raw_items, total_count = await self.scraper.scrape_daily_schedule(date_str)
                    
                    if (total_count > 0):
                        all_collected_items.extend(raw_items)
                        logger.info(f"Successfully collected {len(raw_items)} items for {date_str}.")
                    else:
                        logger.warning(f"No schedule data found for {date_str} (might not be uploaded yet).")
                    
                    # Polite delay between dates
                    await asyncio.sleep(1)
                    
                except Exception as date_error:
                    logger.error(f"Critical error while processing {date_str}: {str(date_error)}")

            # 2. Synchronize and Upsert Data
            sync_manager.synchronize_window_data(all_collected_items)
            
            # 3. Generate Analysis Summary for Gemini
            active_items = sync_manager.get_current_window_items()
            if (len(active_items) > 0):
                df_window = pd.DataFrame(active_items)
                
                # Group by product and ingredient for business insights
                # (Simple aggregation for Gemini prompt context)
                stats_summary = df_window.groupby(["product_name", "category"]).agg({
                    "power_score": "sum",
                    "broadcast_date": "count"
                }).sort_values(by="power_score", ascending=False).head(30).to_string()

                # 4. Load Previous Analysis Context (for continuity)
                output_dir = os.getenv("OUTPUT_DIR", "./outputs")
                last_report_path = os.path.join(output_dir, "last_ai_report.txt")
                prev_report = ""
                if (os.path.exists(last_report_path)):
                    try:
                        with open(last_report_path, "r", encoding="utf-8") as f:
                            prev_report = f.read()
                            logger.info("Previous analysis context loaded successfully.")
                    except Exception as load_err:
                        logger.error(f"Failed to load previous report: {str(load_err)}")

                # 5. Gemini Report Generation (with Context)
                logger.info("Requesting 14-day strategic report from Gemini (with context)...")
                report = self.trend_manager.gemini_client.generate_daily_report(stats_summary, prev_report)
                
                # 6. Save Current Report for Future Continuity
                try:
                    # Save as 'last' for next run
                    with open(last_report_path, "w", encoding="utf-8") as f:
                        f.write(report)
                    
                    # Archive dated version
                    archive_dir = os.path.join(output_dir, "ai_reports")
                    os.makedirs(archive_dir, exist_ok=True)
                    archive_path = os.path.join(archive_dir, f"report_{datetime.now().strftime('%Y%m%d')}.txt")
                    with open(archive_path, "w", encoding="utf-8") as f:
                        f.write(report)
                    logger.info(f"Today's report archived at {archive_path}")
                except Exception as save_err:
                    logger.error(f"Failed to save AI report persistence: {str(save_err)}")
                
                # 7. Telegram Notification
                notifier = NotificationService()
                notifier.send_report(report)
                logger.info("Sliding window report delivery complete.")

            else:
                logger.warning("No active items found in the window. Skipping report.")

        except Exception as e:
            logger.error(f"Unexpected system error during sliding window sync: {str(e)}")
        finally:
            await self.scraper.close()
            logger.info("Sliding window synchronization process terminated.")

    async def generate_and_send_report(self) -> None:
        """
        Standalone report generator using current cumulative statistics.
        """
        import pandas as pd
        from Application.Service.NotificationService import NotificationService
        
        output_dir = os.getenv("OUTPUT_DIR", "./outputs")
        stats_file = os.path.join(output_dir, "건기식 - statistics.xlsx")
        
        if (not os.path.exists(stats_file)):
            logger.error("Cannot find statistics file for reporting.")
            return
            
        try:
            df = pd.read_excel(stats_file)
            recent_context = df.tail(50).to_string(index=False)
            
            logger.info("Generating Standalone AI Report...")
            report = self.trend_manager.gemini_client.generate_daily_report(recent_context)
            
            notifier = NotificationService()
            notifier.send_report(report)
            
        except Exception as e:
            logger.error(f"Fail to execute standalone report: {str(e)}")




