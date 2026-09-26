import os
import logging
from google import genai
from google.genai import types
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class GeminiClient:
    """
    Infrastructure layer for interacting with Gemini AI.
    Handles product analysis and business report generation.
    """
    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if (not api_key):
            logger.error("GEMINI_API_KEY is not set in environment variables.")
        
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-flash-latest'

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
        Generates a concise, fact-driven business report without role-play boilerplate.
        """
        context_section = ""
        if (previous_report_text):
            context_section = f"\n[이전 분석 맥락]:\n{previous_report_text}\n"

        # 텔레그램 가독성을 고려한 프롬프트 고도화
        prompt = f"""
        건강기능식품 시장 데이터 분석 리포트를 작성하십시오.
        {context_section}
        [오늘의 신규 데이터 요약]:
        {stats_summary_text}

        [작성 규칙 - 엄격 준수]:
        1. 인사말, 본인의 역할 소개, 맺음말(예: "이상 보고를 마칩니다", "전략가 드림") 등 불필요한 수식어를 모두 생략하십시오.
        2. 오직 데이터 기반의 분석 결과와 전략 제언만 번호를 매겨 간결하게 작성하십시오.
        3. 변화 추이(이전 대비 증감), 신규/급증 원료, 구체적인 소싱 권고에 집중하십시오.
        4. 추상적인 문구보다는 데이터의 경향성을 나타내는 실질적인 정보를 제공하십시오.
        5. 리포트 하단에 반드시 '오늘의 성분 인텔리전스' 섹션을 추가하십시오.
        6. 추출할 성분은 리포트에서 비중이 높거나 급증하는 상위 5개를 선정하십시오.

        [성분 인텔리전스 출력 양식]:
        📍 [성분명] + [추천 조합 성분]
        - 🧬 기대 효과: [결합 시너지 효과 요약]
        - 📢 마케팅 소구점:
          * (타깃 페인포인트 및 구매 트리거 1)
          * (시장 희소성 및 경쟁 우위 강조 2)
          * (섭취 편의성 및 구성 전략 3)

        리포트 제목으로 시작하여 데이터 분석 결과와 성분 인텔리전스(5종)만 출력하십시오.
        """
        
        final_report = "리포트 생성에 실패하였습니다."
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            if (response.text):
                final_report = response.text.strip()
        except Exception as e:
            logger.error(f"Error generating AI report: {str(e)}")
            
        return final_report


