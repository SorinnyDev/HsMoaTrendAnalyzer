import os
import re
import time
import logging
from google import genai
from google.genai import types
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

class GeminiClient:
    """
    Infrastructure layer for interacting with Gemini AI.
    Handles product analysis (with circuit-breaker & rule-based ingredient/brand extraction)
    and business report generation with retry and model fallback.
    """
    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if (not api_key):
            logger.error("GEMINI_API_KEY is not set in environment variables.")
        
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-2.5-flash"
        self.fallback_models: List[str] = [
            "gemini-2.5-flash",
            "gemini-flash-latest",
            "gemini-2.0-flash"
        ]
        self.quota_exhausted = False
        self.brand_patterns: List[str] = [
            "종근당건강", "여에스더", "에스더포뮬러", "뉴트리코어", "뉴트리원", "정관장",
            "동국제약", "대웅제약", "일양약품", "유한양행", "안국건강", "경남제약",
            "광동", "보령", "한미양행", "프롬바이오", "덴프스", "에이스바이옴",
            "비에날씬", "락토핏", "오라틱스", "골드카무트", "골드파로", "허브이오",
            "주영엔에스", "뼈엔", "관절엔", "닥터린", "닥터슈퍼칸", "에버콜라겐",
            "센트룸", "오쏘몰", "솔가", "세노비스", "듀오락", "드시모네", "셀렉스",
            "하이뮨", "콜롬비아나", "홀베리", "이알하나"
        ]
        self.ingredient_rules: List[Tuple[str, str]] = [
            ("비에날씬", "다이어트 유산균(BNR17)"),
            ("비에날퀸", "다이어트 유산균(BNR17)"),
            ("비피더스", "다이어트 유산균(비피더스B3)"),
            ("오라틱스", "구강유산균(CMU)"),
            ("잇몸유산균", "구강유산균(CMU)"),
            ("락토핏", "프로바이오틱스(유산균)"),
            ("유산균", "프로바이오틱스(유산균)"),
            ("프로바이오틱스", "프로바이오틱스(유산균)"),
            ("콘드로이친", "콘드로이친"),
            ("난각막", "난각막(NEM/DEM)"),
            ("엠비피", "유단백추출물(MBP)"),
            ("MBP", "유단백추출물(MBP)"),
            ("보스웰리아", "보스웰리아"),
            ("글루코사민", "글루코사민"),
            ("엠에스엠", "MSM(식이유황)"),
            ("MSM", "MSM(식이유황)"),
            ("파로효소", "파로발효효소"),
            ("파로 효소", "파로발효효소"),
            ("카무트", "카무트브랜드밀효소"),
            ("효소", "곡물발효효소"),
            ("포스파티딜세린", "포스파티딜세린(PS)"),
            ("닥터PS", "포스파티딜세린(PS)"),
            ("루테인지아잔틴", "루테인지아잔틴"),
            ("아스타잔틴", "루테인지아잔틴·아스타잔틴"),
            ("루테인", "루테인지아잔틴"),
            ("알티지 오메가", "초임계 rTG 오메가3"),
            ("알티지오메가", "초임계 rTG 오메가3"),
            ("오메가3", "rTG 오메가3"),
            ("오메가", "rTG 오메가3"),
            ("코엔자임", "코엔자임Q10"),
            ("코큐텐", "코엔자임Q10"),
            ("리포좀 비타민", "리포좀 비타민C"),
            ("멀티비타민", "멀티비타민·미네랄"),
            ("트루바이타민", "멀티비타민·미네랄"),
            ("비타민", "비타민"),
            ("콜라겐", "저분자 콜라겐 펩타이드"),
            ("글루타치온", "리포좀 글루타치온"),
            ("마그네슘", "마그네슘"),
            ("칼슘", "칼슘·마그네슘·비타민D"),
            ("알부민", "복합 알부민"),
            ("단백질", "단백질(프로틴)"),
            ("하이뮨", "산양유 단백질"),
            ("흑염소", "흑염소진액"),
            ("홍삼", "홍삼"),
            ("녹용", "녹용·침향"),
            ("침향", "녹용·침향"),
            ("밀크씨슬", "밀크씨슬(실리마린)"),
            ("쏘팔메토", "쏘팔메토·옥타코사놀"),
            ("갱년기", "갱년기 여성건강(회화나무/연백피/ERr731)"),
            ("이알하나", "갱년기 여성건강(ERr731)"),
            ("ERr731", "갱년기 여성건강(ERr731)"),
            ("다이어트", "체지방 감소 다이어트 소재"),
            ("레몬즙", "유기농 레몬즙"),
            ("올리브오일", "엑스트라버진 올리브오일")
        ]

    def _extract_rule_based_product_info(self, product_name: str) -> Dict[str, str]:
        """
        Deterministically extracts brand and core ingredient from product_name
        when Gemini API is unavailable or rate-limited.
        """
        result = {}
        result["brand"] = "홈쇼핑 기획"
        result["ingredient"] = "복합 건강기능식품"

        bracket_match = re.search(r"\[([^\]]+)\]", product_name)
        if (bracket_match is not None):
            candidate_brand = bracket_match.group(1).strip()
            if (
                "방송" not in candidate_brand
                and "세일" not in candidate_brand
                and "특가" not in candidate_brand
                and "혜택" not in candidate_brand
                and "단독" not in candidate_brand
                and "더블" not in candidate_brand
                and "마지막" not in candidate_brand
                and len(candidate_brand) <= 12
            ):
                result["brand"] = candidate_brand

        for (known_brand) in self.brand_patterns:
            if (known_brand in product_name):
                result["brand"] = known_brand
                break

        for (keyword, mapped_ingredient) in self.ingredient_rules:
            if (keyword in product_name):
                result["ingredient"] = mapped_ingredient
                break

        return result

    def analyze_product_name(self, product_name: str) -> Dict[str, str]:
        """
        Extracts brand and core ingredient from a product name.
        Uses rule-based fallback immediately if API quota/credits are exhausted.
        """
        fallback_info = self._extract_rule_based_product_info(product_name)
        if (self.quota_exhausted):
            return fallback_info

        prompt = f"""
        당신은 대한민국 건강기능식품 시장의 전문 분석가입니다.
        아래 상품명에서 '핵심 성분(원료)'과 '브랜드명'을 추출하십시오.

        [상품명]: {product_name}
        [규칙]: 브랜드|원료 형식으로 한 줄만 출력하십시오.
        """
        
        result = {}
        result["brand"] = fallback_info["brand"]
        result["ingredient"] = fallback_info["ingredient"]
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            if (response and response.text):
                text = response.text.strip()
                if ("|" in text):
                    parts = text.split("|")
                    if (len(parts) >= 2):
                        result["brand"] = parts[0].strip()
                        result["ingredient"] = parts[1].strip()
        except Exception as e:
            err_str = str(e)
            if ("402" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str):
                if (not self.quota_exhausted):
                    logger.warning(f"Gemini API quota/billing exhausted ({err_str}). Switching product analysis to rule-based engine.")
                self.quota_exhausted = True
            else:
                logger.error(f"Error in analysis: {err_str}")
            
        return result

    def generate_daily_report(self, stats_summary_text: str, previous_report_text: str = "") -> str:
        """
        Generates a deep strategic business report with 2 mandatory core sections:
        1. # 건강기능식품 시장 데이터 분석 리포트 (카테고리별 상위 5대 트렌드 - 데이터 트렌드, 소싱 전략)
        2. ### 오늘의 성분 인텔리전스 (핵심 시너지 성분 5종)
        """
        if (not stats_summary_text or len(stats_summary_text.strip()) == 0):
            logger.warning("Empty stats_summary_text passed to generate_daily_report.")
            return "현재 분석 가능한 건강기능식품 편성 요약 데이터가 없습니다."

        context_section = ""
        if (
            previous_report_text
            and "리포트 생성에 실패하였습니다" not in previous_report_text
            and "Fallback" not in previous_report_text
            and "축약된 요약 리포트" not in previous_report_text
            and "수집 요약 리포트" not in previous_report_text
        ):
            trimmed_prev = previous_report_text[:3000]
            context_section = f"\n[이전 분석 맥락]:\n{trimmed_prev}\n"

        prompt = f"""
        당신은 대한민국 홈쇼핑 건강기능식품 시장의 최고 수석 전략 분석가입니다.
        아래 편성 데이터 요약과 이전 분석 맥락을 심층 분석하여 전문가용 시장 분석 리포트를 작성하십시오.
        {context_section}
        [오늘의 신규 편성 데이터 요약 (상품명, 카테고리, Power Score, 방송 횟수)]:
        {stats_summary_text}

        [작성 가이드라인 및 형식 규칙 - 엄격 준수]:
        1. 인사말, 본인 소개, 맺음말(예: "이상 보고를 마칩니다", "전략가 드림") 등 불필요한 사족을 절대 작성하지 마십시오.
        2. 단순한 텍스트 나열이나 목록 요약이 아닌, 아래의 2개 핵심 섹션 양식을 엄격히 지켜 심층 비즈니스 인사이트로 작성하십시오.
        3. 섹션 1에서는 데이터상 Power Score와 방송 빈도가 높은 상위 5대 트렌드를 도출하고, 각 항목마다 반드시 [데이터 트렌드]와 [소싱 전략]을 세부 불릿으로 나누어 기술하십시오.
           - 데이터 트렌드: 실제 상품명, Power Score(PS), 방송 횟수, 패키지 구성(3개월/6개월 더블/12개월/18개월 등)을 구체적으로 명시.
           - 소싱 전략: 용량 패키징, 제형 기술(리포좀, 하이퍼셀, 퓨레 등), 프로모션 할인율 및 라인업 기획 대응 전략을 실행 가능하게 제시.
        4. 섹션 2에서는 시장성과 시너지가 높은 핵심 조합 성분 5종을 선정하여 아래 양식대로 작성하십시오.

        [출력 양식]:
        # 건강기능식품 시장 데이터 분석 리포트

        1. **[트렌드 1 제목 및 전략 요약]**
           * **데이터 트렌드**: [구체적 상품명, PS 점수, 방송 횟수, 패키징 분석]
           * **소싱 전략**: [용량, 제형 기술, 프로모션 및 소싱 실행 방안]

        2. **[트렌드 2 제목 및 전략 요약]**
           * **데이터 트렌드**: ...
           * **소싱 전략**: ...

        3. **[트렌드 3 제목 및 전략 요약]**
           * **데이터 트렌드**: ...
           * **소싱 전략**: ...

        4. **[트렌드 4 제목 및 전략 요약]**
           * **데이터 트렌드**: ...
           * **소싱 전략**: ...

        5. **[트렌드 5 제목 및 전략 요약]**
           * **데이터 트렌드**: ...
           * **소싱 전략**: ...

        ---

        ### 오늘의 성분 인텔리전스

        📍 **[원료 1] + [원료 2]**
        - 🧬 기대 효과: [상호작용 메커니즘 및 결합 시너지 효과 요약]
        - 📢 마케팅 소구점:
          * (타깃 연령층 및 구매 트리거)
          * (시장 차별점 및 제형 기술/원료 희소성 강조)
          * (섭취 편의성 및 패키지 구성 전략)

        📍 **[원료 3] + [원료 4]**
        - 🧬 기대 효과: ...
        - 📢 마케팅 소구점:
          * ...
          * ...
          * ...

        (총 5개 성분 인텔리전스 작성)
        """
        
        final_report = "리포트 생성에 실패하였습니다."
        
        for (candidate_model) in self.fallback_models:
            try:
                response = self.client.models.generate_content(
                    model=candidate_model,
                    contents=prompt
                )
                if (response and response.text and len(response.text.strip()) > 0):
                    final_report = response.text.strip()
                    return final_report
            except Exception as e:
                logger.error(f"Error generating AI report with model '{candidate_model}': {str(e)}")
                time.sleep(0.5)
                continue
            
        return final_report
