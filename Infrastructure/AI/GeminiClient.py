import os
import time
import logging
from google import genai
from google.genai import types
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

class GeminiClient:
    """
    Infrastructure layer for interacting with Gemini AI.
    Handles product analysis and business report generation with retry and model fallback.
    """
    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if (not api_key):
            logger.error("GEMINI_API_KEY is not set in environment variables.")
        
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-flash-latest"
        self.fallback_models: List[str] = [
            "gemini-flash-latest",
            "gemini-2.0-flash",
            "gemini-1.5-flash"
        ]

    def analyze_product_name(self, product_name: str) -> Dict[str, str]:
        """
        Extracts brand and core ingredient from a product name.
        """
        prompt = f"""
        당신은 대한민국 건강기능식품 시장의 전문 분석가입니다.
        아래 상품명에서 '핵심 성분(원료)'과 '브랜드명'을 추출하십시오.

        [상품명]: {product_name}
        [규칙]: 브랜드|원료 형식으로 한 줄만 출력하십시오.
        """
        
        result = {}
        result["brand"] = "미분류"
        result["ingredient"] = "미분류"
        
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
            logger.error(f"Error in analysis: {str(e)}")
            
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
        if (previous_report_text and "리포트 생성에 실패하였습니다" not in previous_report_text and "Fallback" not in previous_report_text):
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
        
        for candidate_model in self.fallback_models:
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
                time.sleep(1)
                continue
            
        return final_report
