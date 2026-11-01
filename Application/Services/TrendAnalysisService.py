import os
import logging
import pandas as pd
from datetime import datetime, date
from typing import List
from Infrastructure.Scrapers.MultiChannelAggregator import MultiChannelAggregator, ChannelCrawlResult
from Infrastructure.Persistence.ExcelRepository import ExcelRepository
from Infrastructure.Persistence.SqliteRepository import SqliteRepository
from Infrastructure.Persistence.SyncManager import SyncManager
from Application.Service.TrendManager import TrendManager
from Application.Service.NotificationService import NotificationService
from Domain.HomeShopping.Service.WindowService import WindowService
from Domain.Models.Schedule import ScheduleItem

logger = logging.getLogger(__name__)

class TrendAnalysisService:
    """
    Application service that coordinates multi-channel scraping, health tracking,
    data persistence, and Gemini AI trend analysis.
    """

    def __init__(self) -> None:
        self.aggregator = MultiChannelAggregator()
        self.repository = ExcelRepository()
        self.db_repo = SqliteRepository()
        self.trend_manager = TrendManager()
        self.last_health_results: List[ChannelCrawlResult] = []

    def deduplicate_items(self, items: List[ScheduleItem]) -> List[ScheduleItem]:
        """
        Deduplicates a list of schedule items based on channel and exact product name.
        """
        seen_keys = set()
        dedup_items: List[ScheduleItem] = []
        for item in items:
            unique_key = f"{item.channel}_{item.product_name}"
            if (unique_key not in seen_keys):
                seen_keys.add(unique_key)
                dedup_items.append(item)
        return dedup_items

    def _update_and_enrich_health_results(self, channel_results: List[ChannelCrawlResult]) -> List[ChannelCrawlResult]:
        """
        Persists each channel's crawl status into SQLite channel_health_history
        and updates consecutive_failures on each ChannelCrawlResult object.
        """
        enriched_results: List[ChannelCrawlResult] = []
        for result in channel_results:
            consecutive_count = self.db_repo.update_channel_health(
                channel_name=result.channel_name,
                is_success=result.is_success,
                error_message=result.error_message
            )
            result.consecutive_failures = consecutive_count
            enriched_results.append(result)

        self.last_health_results = enriched_results
        return enriched_results

    async def run_daily_collection(self, target_date_str: str = None) -> None:
        """
        Executes parallel multi-channel collection for a single day.
        """
        if (not target_date_str):
            target_date_str = datetime.now().strftime("%Y-%m-%d")

        target_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        month_str = target_dt.strftime("%m월")
        short_date_str = target_dt.strftime("%m-%d")

        logger.info(f"Starting multi-channel collection for {target_date_str}...")
        try:
            raw_items, channel_results = await self.aggregator.collect_all(target_dt, target_dt)
            self._update_and_enrich_health_results(channel_results)

            if (len(raw_items) > 0):
                dedup_items = self.deduplicate_items(raw_items)
                logger.info(f"Raw items: {len(raw_items)}, Deduplicated items: {len(dedup_items)}")

                file_path = self.repository.save_schedule(dedup_items, month_str, short_date_str)
                logger.info(f"Successfully saved deduplicated schedule data to {file_path}")

                await self.trend_manager.process_daily_trends(raw_items, target_date_str)
                self.db_repo.mark_date_status(target_date_str, "SUCCESS", len(raw_items))
            else:
                logger.warning(f"No health food items collected for {target_date_str}.")
                self.db_repo.mark_date_status(target_date_str, "NO_DATA", 0)

        except Exception as e:
            logger.error(f"Unexpected error in single day multi-channel collection: {str(e)}")
            self.db_repo.mark_date_status(target_date_str, "FAILED", 0)

    async def run_sliding_window_sync(self) -> None:
        """
        Coordinates the 7-day sliding window (D+0 ~ D+6) parallel synchronization process across 5 channels.
        Includes fault-tolerant scraping, health-check DB persistence, Excel upserting, and Telegram reporting.
        """
        window_service = WindowService(window_size=7)
        sync_manager = SyncManager()
        notifier = NotificationService()

        target_dates = window_service.get_sliding_window_dates()
        start_dt = datetime.strptime(target_dates[0], "%Y-%m-%d").date()
        end_dt = datetime.strptime(target_dates[-1], "%Y-%m-%d").date()

        logger.info(f"Starting 7-day (D+0 ~ D+6) multi-channel sliding window sync: {start_dt} ~ {end_dt}")

        try:
            # 1. Parallel Fault-Tolerant Collection across 5 Home Shopping Channels
            all_collected_items, channel_results = await self.aggregator.collect_all(start_dt, end_dt)

            # 2. Update channel_health_history in crawling_history.db
            enriched_health_results = self._update_and_enrich_health_results(channel_results)

            # 3. Record per-date crawling history and monthly Excel sheets
            items_by_date = {}
            for date_str in target_dates:
                items_by_date[date_str] = []

            for item in all_collected_items:
                if (item.broadcast_date in items_by_date):
                    items_by_date[item.broadcast_date].append(item)
                else:
                    items_by_date[item.broadcast_date] = [item]

            for date_str, date_items in items_by_date.items():
                if (len(date_items) > 0):
                    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    month_str = dt_obj.strftime("%m월")
                    sheet_name = dt_obj.strftime("%m-%d")
                    dedup_date_items = self.deduplicate_items(date_items)
                    self.repository.save_schedule(dedup_date_items, month_str, sheet_name)
                    self.db_repo.mark_date_status(date_str, "SUCCESS", len(date_items))
                else:
                    self.db_repo.mark_date_status(date_str, "NO_DATA", 0)

            # 4. Synchronize and Upsert D+0 ~ D+6 Window Data to Excel
            sync_manager.synchronize_window_data(all_collected_items, window_days=7)

            # 5. Generate Analysis Summary for Gemini & Send Telegram Report with Health Check
            active_items = sync_manager.get_current_window_items()
            output_dir = os.getenv("OUTPUT_DIR", "./outputs")
            last_report_path = os.path.join(output_dir, "last_ai_report.txt")

            if (len(active_items) > 0):
                df_window = pd.DataFrame(active_items)

                agg_spec = {}
                agg_spec["power_score"] = "sum"
                agg_spec["broadcast_date"] = "count"

                stats_summary = df_window.groupby(["product_name", "category"]).agg(
                    agg_spec
                ).sort_values(by="power_score", ascending=False).head(30).to_string()

                prev_report = ""
                if (os.path.exists(last_report_path)):
                    try:
                        with open(last_report_path, "r", encoding="utf-8") as f:
                            prev_report = f.read()
                            logger.info("Previous analysis context loaded successfully.")
                    except Exception as load_err:
                        logger.error(f"Failed to load previous report: {str(load_err)}")

                logger.info("Requesting 7-day strategic report from Gemini (with context)...")
                report_body = self.trend_manager.gemini_client.generate_daily_report(stats_summary, prev_report)
            else:
                logger.warning("No active items found in the window. Sending health status report.")
                report_body = "현재 윈도우(D+0 ~ D+6) 내 수집된 건강기능식품 편성 데이터가 없습니다."

            full_report_text = notifier.build_full_message(report_body, enriched_health_results)

            try:
                os.makedirs(output_dir, exist_ok=True)
                with open(last_report_path, "w", encoding="utf-8") as f:
                    f.write(full_report_text)

                archive_dir = os.path.join(output_dir, "ai_reports")
                os.makedirs(archive_dir, exist_ok=True)
                archive_path = os.path.join(archive_dir, f"report_{datetime.now().strftime('%Y%m%d')}.txt")
                with open(archive_path, "w", encoding="utf-8") as f:
                    f.write(full_report_text)
                logger.info(f"Today's report archived at {archive_path}")
            except Exception as save_err:
                logger.error(f"Failed to save AI report persistence: {str(save_err)}")

            notifier.send_report(report_body, enriched_health_results)
            logger.info("Sliding window report delivery complete.")

        except Exception as e:
            logger.error(f"Unexpected system error during sliding window sync: {str(e)}")
        finally:
            logger.info("Sliding window synchronization process terminated.")

    async def generate_and_send_report(self) -> None:
        """
        Standalone report generator using current cumulative statistics and latest channel health status.
        """
        notifier = NotificationService()
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

            notifier.send_report(report, self.last_health_results)

        except Exception as e:
            logger.error(f"Fail to execute standalone report: {str(e)}")
