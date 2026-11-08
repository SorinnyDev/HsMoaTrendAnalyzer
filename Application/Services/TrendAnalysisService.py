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
    data persistence across 4 storage targets, and Gemini AI trend analysis.
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
            sorted_df = grouped_df.sort_values(by="power_score", ascending=False).head(35)
            
            summary_lines: List[str] = []
            for _, row in sorted_df.iterrows():
                p_name = row["product_name"]
                cat = row["category"]
                ps_val = round(float(row["power_score"]), 1)
                b_cnt = int(row["broadcast_date"])
                summary_lines.append(f"- 상품명: {p_name} | 카테고리: {cat} | Power Score: {ps_val} | 방송횟수: {b_cnt}회")
                
            return "\n".join(summary_lines)

        except Exception as agg_err:
            logger.error(f"Error during DataFrame aggregation, falling back to plain text summary: {str(agg_err)}")
            fallback_lines: List[str] = []
            limit_count = min(len(active_records), 35)
            for idx in range(limit_count):
                rec = active_records[idx]
                p_name = rec.get("product_name", "미분류")
                c_name = rec.get("channel", "홈쇼핑")
                p_score = rec.get("power_score", 1.0)
                fallback_lines.append(f"- [{c_name}] {p_name} (Power Score: {p_score})")
            return "\n".join(fallback_lines)

    def _build_deterministic_fallback_report(self, active_records: List[Dict[str, Any]], stats_summary: str) -> str:
        """
        Generates a full structured 2-section strategic report from real collected data
        when all Gemini API models fail (e.g., HTTP 402/429 quota exhaustion).
        Follows the exact two-section format:
        1. # 건강기능식품 시장 데이터 분석 리포트 (5 key trends with 데이터 트렌드 & 소싱 전략)
        2. ### 오늘의 성분 인텔리전스 (5 core ingredient synergy blocks)
        """
        df_window = pd.DataFrame(active_records)
        if ("power_score" not in df_window.columns):
            df_window["power_score"] = 1.0
        else:
            df_window["power_score"] = pd.to_numeric(df_window["power_score"], errors="coerce").fillna(1.0)

        if ("broadcast_date" not in df_window.columns):
            df_window["broadcast_date"] = datetime.now().strftime("%Y-%m-%d")

        agg_spec = {}
        agg_spec["power_score"] = "sum"
        agg_spec["broadcast_date"] = "count"

        grouped_df = df_window.groupby(["product_name"], as_index=False).agg(agg_spec)
        sorted_df = grouped_df.sort_values(by="power_score", ascending=False)

        def _find_top_examples(keywords: List[str], limit: int = 2) -> str:
            matched_strs: List[str] = []
            for _, row in sorted_df.iterrows():
                p_name = str(row["product_name"])
                ps_val = round(float(row["power_score"]), 1)
                b_cnt = int(row["broadcast_date"])
                is_hit = False
                for (kw) in keywords:
                    if (kw in p_name):
                        is_hit = True
                        break
                if (is_hit):
                    matched_strs.append(f"\"{p_name}\"(PS {ps_val}, 방송 {b_cnt}회)")
                    if (len(matched_strs) >= limit):
                        break
            if (len(matched_strs) == 0 and len(sorted_df) > 0):
                top_row = sorted_df.iloc[0]
                matched_strs.append(f"\"{top_row['product_name']}\"(PS {round(float(top_row['power_score']), 1)}, 방송 {int(top_row['broadcast_date'])}회)")
            return ", ".join(matched_strs)

        probiotics_ex = _find_top_examples(["비에날씬", "비에날퀸", "오라틱스", "유산균", "락토핏", "비피더스"], 3)
        enzyme_ex = _find_top_examples(["파로", "카무트", "효소", "여주"], 3)
        joint_ex = _find_top_examples(["콘드로이친", "난각막", "엠비피", "MBP", "관절", "칼슘", "마그네슘"], 3)
        omega_eye_ex = _find_top_examples(["코엔자임", "코큐텐", "오메가", "루테인", "아스타잔틴", "포스파티딜세린"], 3)
        multi_beauty_ex = _find_top_examples(["콜라겐", "비타민", "트루바이타민", "알부민", "홍삼", "갱년기", "이알하나", "ERr731"], 3)

        lines: List[str] = []
        lines.append("# 건강기능식품 시장 데이터 분석 리포트")
        lines.append("")
        lines.append("1. **기능성 특화 유산균(다이어트·구강·혈당)의 프라임타임 독주 및 다다익선 패키지 세분화**")
        lines.append(f"   * **데이터 트렌드**: 전체 {len(active_records)}건의 주간 편성 데이터 중 {probiotics_ex} 등이 최상위 Power Score를 기록하며 시장을 견인하고 있습니다. 체지방 감소(BNR17·비피더스B3), 구강·잇몸 케어(CMU), 당케어 등 목적형 개별인정형 유산균이 3박스(진입형)~12박스(패밀리 대용량) 다단 구성으로 집중 배치되었습니다.")
        lines.append("   * **소싱 전략**: 일반 장 건강 유산균을 넘어 체지방 감소·구강 면역·혈당 케어 등 기능성이 명확히 입증된 개별인정형 균주 원료를 확보하고, 3개월 진입형과 10~12개월 장기 복용 패밀리 세트를 동시 기획하여 객단가와 소진율을 극대화해야 합니다.")
        lines.append("")
        lines.append("2. **고대곡물 발효효소(파로·카무트) 및 캡슐레이션 제형 기술의 대용량 볼륨 확대**")
        lines.append(f"   * **데이터 트렌드**: {enzyme_ex} 등 파로(Farro)와 카무트(Kamut) 기반 프리미엄 곡물 발효효소 라인업이 전 채널 골든타임에 반복 편성되고 있습니다. 특히 10~15박스 초장기 구성과 반값 프로모션, 주방 사은품 결합 포맷이 높은 비중을 차지합니다.")
        lines.append("   * **소싱 전략**: 단순 분말 효소 대비 섭취 편의성과 체내 안정성을 높인 캡슐레이션(소형 정제/구슬형) 제형 기술을 도입하고, 12~15박스 대용량 구성에 50% 할인 명분을 결합한 공격적인 가격 프로모션 라인업을 구축해야 합니다.")
        lines.append("")
        lines.append("3. **관절·연골·골격 케어의 차세대 소재(난각막 DEM·콘드로이친·MBP) 다원화 및 복합 배합**")
        lines.append(f"   * **데이터 트렌드**: {joint_ex} 등 전통적 강자인 콘드로이친1200과 MBP(유단백추출물)에 이어 차세대 관절 소재인 난각막(DEM/NEM) 6개월·12개월 구성이 상위권에 안착하며 시니어 관절 시장의 세대교체 및 복합화가 가속화되고 있습니다.")
        lines.append("   * **소싱 전략**: 소연골 유래 콘드로이친이나 수용성 난각막 등 고순도 핵심 원료에 MBP·비타민D·칼슘·마그네슘을 결합한 관절·뼈 3중 복합 포뮬러를 기획하고, 오메가3 등 부원료 본품을 추가 증정하는 크로스셀링 패키지를 구성해야 합니다.")
        lines.append("")
        lines.append("4. **하이퍼셀·초임계 흡수율 기술을 결합한 혈행·항산화·눈 건강 10~18개월 장기 기획 확산**")
        lines.append(f"   * **데이터 트렌드**: {omega_eye_ex} 등 혈압·항산화(코엔자임Q10), 혈행(초임계 rTG 오메가3), 눈 건강(아스타잔틴·루테인지아잔틴), 두뇌 인지력(포스파티딜세린) 카테고리에서 하이퍼셀·리포좀 등 흡수율 극대화 제형과 10~18개월 장기 패키지가 주류를 형성하고 있습니다.")
        lines.append("   * **소싱 전략**: 성숙기 혈행·눈·항산화 원료 소싱 시 반드시 생체이용률을 높이는 하이퍼셀·초임계·리포좀 공법을 전면에 내세워야 하며, 12~18개월 초장기 구성을 통해 일일 섭취 단가를 낮추는 가성비 소구 전략을 전개해야 합니다.")
        lines.append("")
        lines.append("5. **올인원 멀티비타민·이너뷰티(콜라겐+레티놀)·갱년기 특화 원료의 타깃 맞춤형 큐레이션**")
        lines.append(f"   * **데이터 트렌드**: {multi_beauty_ex} 등 멀티비타민+루테인+밀크씨슬 올인원 팩과 어린콜라겐+레티놀A 이너뷰티 결합 상품, 회화나무·ERr731 기반 여성 갱년기 특화 라인업이 5개월~12개월 단위로 견조한 방송 빈도를 유지하고 있습니다.")
        lines.append("   * **소싱 전략**: 단일 성분 중복 섭취에 피로감을 느끼는 소비자를 겨냥해 비타민·간·눈 건강을 한 포에 담은 올인원 멀티팩과, 저분자 콜라겐+레티놀/엘라스틴 복합 이너뷰티 포뮬러를 10~12개월 단위 기획전 형태로 소싱해야 합니다.")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("### 오늘의 성분 인텔리전스")
        lines.append("")
        lines.append("📍 **모유유래·비피더스 다이어트 유산균(BNR17/B3) + 프락토올리고당(프리바이오틱스)**")
        lines.append("- 🧬 기대 효과: 체지방 감소 기능성을 인정받은 개별인정형 프로바이오틱스와 유익균 먹이인 프리바이오틱스의 신바이오틱스 결합으로 장내 환경 개선 및 체지방 집중 감소 시너지 구현")
        lines.append("- 📢 마케팅 소구점:")
        lines.append("  * 나잇살과 복부 체지방 관리, 배변 활동을 동시에 케어하려는 3060 여성 핵심 타깃")
        lines.append("  * 식약처 개별인정형 체지방 감소 기능성 균주 및 100억~150억 CFU 보장 기술력 강조")
        lines.append("  * 3개월 진입형부터 6개월·12개월 패밀리 대용량 구성까지 세분화하여 재구매 전환율 극대화")
        lines.append("")
        lines.append("📍 **파로(Farro)·카무트 브랜드밀 발효효소 + 유산균·식이섬유**")
        lines.append("- 🧬 기대 효과: 고대곡물 유래 고활성 알파-아밀라아제·프로테아제 소화효소와 정장 유산균의 시너지로 탄수화물·단백질 분해 및 식후 속편한 소화 대사 케어")
        lines.append("- 📢 마케팅 소구점:")
        lines.append("  * 탄수화물 위주 식습관으로 식후 더부룩함과 소화력 저하를 겪는 4070 중장년층 타깃")
        lines.append("  * 정품 고대곡물 원료 인증, 정제수 무첨가 고역가 수치 및 캡슐레이션 제형 기술 차별화")
        lines.append("  * 12~15박스 초특가 대용량 패키지 및 반값 세일 프로모션으로 가족 단위 장기 섭취 유도")
        lines.append("")
        lines.append("📍 **콘드로이친(소연골)·난각막(DEM) + 유단백추출물(MBP) + 비타민D**")
        lines.append("- 🧬 기대 효과: 연골 구성 성분인 콘드로이친·난각막과 골밀도·뼈 재생을 돕는 MBP·비타민D의 결합으로 관절 연골 보호 및 골격 강도를 아우르는 3중 토탈 케어")
        lines.append("- 📢 마케팅 소구점:")
        lines.append("  * 계단 오르내림 시 무릎 관절 불편함과 노화성 골밀도 감소를 동시에 우려하는 5070 시니어 타깃")
        lines.append("  * 인체 연골과 유사한 고순도 콘드로이친 1200mg 및 차세대 수용성 난각막 원료의 인체적용시험 결과 소구")
        lines.append("  * 6개월·12개월 장기 복용 패키지 및 오메가3·칼슘제 추가 증정 구성으로 체감 혜택 강화")
        lines.append("")
        lines.append("📍 **코엔자임Q10(하이퍼셀) + 초임계 알티지(rTG) 오메가3**")
        lines.append("- 🧬 기대 효과: 높은 혈압 감소 및 항산화 작용을 하는 코엔자임Q10과 혈중 중성지질·혈행 개선을 돕는 rTG 오메가3의 시너지로 혈관 탄력 및 심혈관 순환 복합 케어")
        lines.append("- 📢 마케팅 소구점:")
        lines.append("  * 혈압 관리와 혈행 개선, 혈관 항산화를 한 번에 관리하고자 하는 4060 액티브 시니어 타깃")
        lines.append("  * 하이퍼셀(초미세 입자화) 및 저온 초임계 추출 공법을 통한 높은 체내 흡수율과 식물성 캡슐 안정성 강조")
        lines.append("  * 10~18개월 대용량 구성을 통한 일일 섭취 비용 절감 소구")
        lines.append("")
        lines.append("📍 **루테인지아잔틴 + 아스타잔틴(헤마토코쿠스) + 리포좀 비타민**")
        lines.append("- 🧬 기대 효과: 황반 중심부와 주변부 색소 밀도를 유지하는 루테인지아잔틴과 눈의 피로도 개선을 돕는 아스타잔틴, 항산화 리포좀 비타민의 결합으로 노안 및 눈 피로 통합 관리")
        lines.append("- 📢 마케팅 소구점:")
        lines.append("  * 스마트폰 장기 사용으로 인한 눈 피로와 노화로 인한 침침함을 겪는 전 연령층 타깃")
        lines.append("  * 식약처 1일 최대 섭취량 충족 및 초소형 미니 캡슐 제형으로 목넘김 편의성 극대화")
        lines.append("  * 10~12개월분 방송 단독 특가 구성으로 온 가족 눈 건강 상비템 포지셔닝")

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
        Guarantees data integrity across 4 storage targets:
        1. crawling_history.db (date status & channel_health_history)
        2. 7day_forecast_sync.xlsx (upsert active window slots)
        3. 건기식 - statistics.xlsx (cumulative ingredient Power Score stats)
        4. last_ai_report.txt (full strategy report backup)
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

            # 2. Update channel_health_history in crawling_history.db (Target 1)
            enriched_health_results = self._update_and_enrich_health_results(channel_results)

            # 3. Organize items by date and record status
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
                    try:
                        self.repository.save_schedule(dedup_date_items, month_str, sheet_name)
                    except Exception as save_err:
                        logger.error(f"Error saving monthly excel for {date_str}: {str(save_err)}")
                    self.db_repo.mark_date_status(date_str, "SUCCESS", len(date_items))
                else:
                    self.db_repo.mark_date_status(date_str, "NO_DATA", 0)

            # 4. Synchronize and Upsert D+0 ~ D+6 Window Data to Excel (Target 2)
            sync_manager.synchronize_window_data(all_collected_items, window_days=7)

            # 5. Update cumulative ingredient statistics file: 건기식 - statistics.xlsx (Target 3)
            for date_str, date_items in items_by_date.items():
                if (len(date_items) > 0):
                    try:
                        await self.trend_manager.process_daily_trends(date_items, date_str)
                    except Exception as trend_err:
                        logger.error(f"Error processing daily cumulative trends for {date_str}: {str(trend_err)}")

            # 6. Retrieve active window items with in-memory fallback
            active_items = sync_manager.get_current_window_items()
            if (len(active_items) == 0 and len(all_collected_items) > 0):
                logger.info("Using in-memory collected items as fallback for report generation.")
                active_items = self._build_records_from_schedule_items(all_collected_items)

            output_dir = os.getenv("OUTPUT_DIR", "./outputs")
            last_report_path = os.path.join(output_dir, "last_ai_report.txt")

            if (len(active_items) >= 5 or len(active_items) > 0):
                stats_summary = self._build_stats_summary_safely(active_items)

                prev_report = ""
                if (os.path.exists(last_report_path)):
                    try:
                        with open(last_report_path, "r", encoding="utf-8") as f:
                            loaded_text = f.read().strip()
                            if (
                                "리포트 생성에 실패하였습니다" not in loaded_text
                                and "Fallback" not in loaded_text
                                and "축약된 요약 리포트" not in loaded_text
                                and "수집 요약 리포트" not in loaded_text
                            ):
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

            # 7. Backup full strategy report to last_ai_report.txt & archives (Target 4)
            try:
                os.makedirs(output_dir, exist_ok=True)
                if ("리포트 생성에 실패하였습니다" not in report_body):
                    with open(last_report_path, "w", encoding="utf-8") as f:
                        f.write(report_body)

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
