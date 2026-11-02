import os
import logging
import pandas as pd
from datetime import datetime, date
from typing import List, Dict, Any
from Infrastructure.Scrapers.MultiChannelAggregator import MultiChannelAggregator, ChannelCrawlResult
from Infrastructure.Persistence.ExcelRepository import ExcelRepository
from Infrastructure.Persistence.SqliteRepository import SqliteRepository
from Infrastructure.Persistence.SyncManager import SyncManager
from Application.Service.TrendManager import TrendManager
from Application.Service.WeightCalculator import WeightCalculator
from Application.Service.NotificationService import NotificationService
from Domain.HomeShopping.Service.WindowService import WindowService
from Domain.Models.Schedule import ScheduleItem

logger = logging.getLogger(__name__)

class TrendAnalysisService:
    """
    Application service that coordinates multi-channel scraping, health tracking,
    data persistence, and Gemini AI trend analysis with defensive fallback handling.
    """

    def __init__(self) -> None:
        self.aggregator = MultiChannelAggregator()
        self.repository = ExcelRepository()
        self.db_repo = SqliteRepository()
        self.trend_manager = TrendManager()
        self.weight_calculator = WeightCalculator()
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

    def _build_records_from_schedule_items(self, items: List[ScheduleItem]) -> List[Dict[str, Any]]:
        """
        Converts in-memory ScheduleItem objects directly into analysis dictionary records
        so that report generation succeeds even if Excel I/O or some channels fail.
        """
        records: List[Dict[str, Any]] = []
        for item in items:
            weight_val = self.weight_calculator.get_weight(item.broadcast_time)
            category_val = item.category
            if (not category_val):
                category_val = "건강식품"

            row = {}
            row["broadcast_date"] = str(item.broadcast_date)
            row["broadcast_time"] = str(item.broadcast_time)
            row["channel"] = str(item.channel)
            row["product_name"] = str(item.product_name)
            row["category"] = str(category_val)
            row["price"] = str(item.price or "")
            row["power_score"] = float(weight_val)
            records.append(row)

        return records

    def _build_stats_summary_safely(self, active_records: List[Dict[str, Any]]) -> str:
        """
        Safely aggregates active schedule records into a summary string for Gemini.
        Never raises KeyError or ValueError even on partial or malformed records.
        """
        if (not active_records or len(active_records) == 0):
            return ""

        try:
            df_window = pd.DataFrame(active_records)
            if (df_window.empty):
                return ""

            if ("product_name" not in df_window.columns):
                return ""

            if ("category" not in df_window.columns):
                df_window["category"] = "건강식품"
            else:
                df_window["category"] = df_window["category"].fillna("건강식품")

            if ("power_score" not in df_window.columns):
                df_window["power_score"] = 1.0
            else:
                df_window["power_score"] = pd.to_numeric(df_window["power_score"], errors="coerce").fillna(1.0)

            if ("broadcast_date" not in df_window.columns):
                df_window["broadcast_date"] = datetime.now().strftime("%Y-%m-%d")

            agg_spec = {}
            agg_spec["power_score"] = "sum"
            agg_spec["broadcast_date"] = "count"

            grouped_df = df_window.groupby(["product_name", "category"], as_index=False).agg(agg_spec)
            sorted_df = grouped_df.sort_values(by="power_score", ascending=False).head(30)
            return sorted_df.to_string(index=False)

        except Exception as agg_err:
            logger.error(f"Error during DataFrame aggregation, falling back to plain text summary: {str(agg_err)}")
            fallback_lines: List[str] = []
            limit_count = min(len(active_records), 30)
            for idx in range(limit_count):
                rec = active_records[idx]
                p_name = rec.get("product_name", "미분류")
                c_name = rec.get("channel", "홈쇼핑")
                p_score = rec.get("power_score", 1.0)
                fallback_lines.append(f"- [{c_name}] {p_name} (Power Score: {p_score})")
            return "\n".join(fallback_lines)

    def _build_deterministic_fallback_report(self, active_records: List[Dict[str, Any]], stats_summary: str) -> str:
        """
        Generates a structured fallback report when Gemini API fails or returns an error string,
        ensuring valid collected channel data (e.g., CJ온스타일 95 items) is still reported cleanly.
        """
        lines: List[str] = []
        lines.append("📊 [주간 건강기능식품 편성 데이터 요약 리포트 (Fallback)]")
        lines.append(f"• 분석 대상 편성 건수: 총 {len(active_records)}건")
        lines.append("")
        lines.append("[상위 Power Score 핵심 편성 상품 요약]")
        if (stats_summary):
            lines.append(stats_summary)
        else:
            lines.append("집계 가능한 상세 편성 내역이 없습니다.")
        return "\n".join(lines)

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
        Even if several channels fail, normal channels' items are guaranteed to generate a Gemini report.
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

            # 3. Record per-date crawling history and monthly Excel sheets (isolated try-except)
            try:
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
            except Exception as excel_err:
                logger.error(f"Non-fatal error saving daily Excel sheets: {str(excel_err)}")

            # 4. Synchronize and Upsert D+0 ~ D+6 Window Data to Excel
            sync_manager.synchronize_window_data(all_collected_items, window_days=7)

            # 5. Retrieve active window items with in-memory fallback if Excel read returns empty
            active_items = sync_manager.get_current_window_items()
            if (len(active_items) == 0 and len(all_collected_items) > 0):
                logger.info("Using in-memory collected items as fallback for report generation.")
                active_items = self._build_records_from_schedule_items(all_collected_items)

            output_dir = os.getenv("OUTPUT_DIR", "./outputs")
            last_report_path = os.path.join(output_dir, "last_ai_report.txt")

            if (len(active_items) > 0):
                stats_summary = self._build_stats_summary_safely(active_items)

                prev_report = ""
                if (os.path.exists(last_report_path)):
                    try:
                        with open(last_report_path, "r", encoding="utf-8") as f:
                            loaded_text = f.read().strip()
                            if ("리포트 생성에 실패하였습니다" not in loaded_text):
                                prev_report = loaded_text
                                logger.info("Previous analysis context loaded successfully.")
                    except Exception as load_err:
                        logger.error(f"Failed to load previous report: {str(load_err)}")

                logger.info(f"Requesting 7-day strategic report from Gemini for {len(active_items)} active items...")
                report_body = self.trend_manager.gemini_client.generate_daily_report(stats_summary, prev_report)

                if (not report_body or "리포트 생성에 실패하였습니다" in report_body):
                    logger.warning("Gemini report returned failure message. Building deterministic fallback report.")
                    report_body = self._build_deterministic_fallback_report(active_items, stats_summary)
            else:
                logger.warning("No active items found in the window across all channels.")
                report_body = "현재 윈도우(D+0 ~ D+6) 내 수집된 건강기능식품 편성 데이터가 없습니다."

            full_report_text = notifier.build_full_message(report_body, enriched_health_results)

            try:
                os.makedirs(output_dir, exist_ok=True)
                if ("리포트 생성에 실패하였습니다" not in report_body):
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
            if (df.empty):
                logger.warning("Statistics file is empty. Skipping standalone report.")
                return

            recent_context = df.tail(50).to_string(index=False)

            logger.info("Generating Standalone AI Report...")
            report = self.trend_manager.gemini_client.generate_daily_report(recent_context)
            if (not report or "리포트 생성에 실패하였습니다" in report):
                report = f"📊 [누적 통계 요약 리포트 (Fallback)]\n{recent_context}"

            notifier.send_report(report, self.last_health_results)

        except Exception as e:
            logger.error(f"Fail to execute standalone report: {str(e)}")
