from abc import ABC, abstractmethod
from datetime import date
from typing import List, Dict, Optional, Any
import asyncio
import json
import random
import logging
import aiohttp
from Domain.Models.Schedule import ScheduleItem

logger = logging.getLogger(__name__)

class ScraperParseException(Exception):
    """
    Custom exception raised when HTML/JSON parsing or selector extraction fails.
    """
    pass

class BaseChannelScraper(ABC):
    """
    Abstract base interface for 5 major home shopping channel scrapers.
    Provides common HTTP/JSON fetching with timeout, User-Agent rotation, and health/keyword filtering.
    """

    def __init__(self, channel_name: str, max_days_ahead: int = 6, timeout_seconds: int = 15) -> None:
        self.channel_name = channel_name
        self.max_days_ahead = max_days_ahead
        self.timeout_seconds = timeout_seconds
        self.user_agents = [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 13; SM-G998N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.99 Mobile Safari/537.36"
        ]
        self.health_keywords = [
            "건강식품", "건강기능", "건기식", "비타민", "멀티비타민", "유산균", "프로바이오틱스",
            "락토핏", "포스트바이오틱스", "홍삼", "산삼", "흑삼", "루테인", "지아잔틴",
            "아스타잔틴", "오메가", "알티지", "rTG", "콜라겐", "엘라스틴", "히알루론산",
            "콘드로이친", "글루코사민", "글루타치온", "밀크씨슬", "실리마린", "관절", "연골",
            "보스웰리아", "엠에스엠", "MSM", "초록입홍합", "칼슘", "마그네슘", "아연",
            "철분", "엽산", "비오틴", "셀레늄", "프로폴리스", "로얄젤리", "마누카꿀",
            "아르기닌", "효소", "파로효소", "카무트", "다이어트", "체지방", "가르시니아",
            "시서스", "콜레우스포스콜리", "락토페린", "혈행", "혈압", "혈당", "콜레스테롤",
            "코엔자임", "엽록소", "스피루리나", "클로렐라", "녹용", "침향", "공진단",
            "흑염소", "산양유", "초유", "단백질", "프로틴", "알부민", "아미노산",
            "쏘팔메토", "옥타코사놀", "폴리코사놀", "바나바잎", "여주", "구아검",
            "차전자피", "식이섬유", "레시틴", "포스파티딜세린", "은행잎", "기억력",
            "면역", "갱년기", "백수오", "석류", "크랜베리", "쏘팔메토", "케르세틴",
            "브로멜라인", "모로실", "베르가못", "정관장", "종근당건강", "뉴트리", "에버콜라겐",
            "에스더포뮬러", "여에스더", "프롬바이오", "덴프스", "트루바이타민", "일양약품",
            "GC녹십자", "동국제약", "대웅제약", "안국건강", "유한양행", "경남제약",
            "광동", "보령", "한미양행", "뉴트리원", "비에날씬", "센트룸", "오쏘몰",
            "솔가", "GNC", "세노비스", "듀오락", "드시모네", "셀렉스", "하이뮨"
        ]

    def get_rotated_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Builds HTTP headers with a randomly rotated mobile User-Agent.
        """
        selected_ua = random.choice(self.user_agents)
        headers = {}
        headers["User-Agent"] = selected_ua
        headers["Accept"] = "application/json, text/javascript, text/html, application/xhtml+xml, */*;q=0.9"
        headers["Accept-Language"] = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        headers["Connection"] = "keep-alive"

        if (extra_headers is not None):
            for key, value in extra_headers.items():
                headers[key] = value

        return headers

    async def fetch_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Executes a lightweight HTTP GET request using aiohttp with timeout and User-Agent rotation.
        Raises descriptive exceptions on HTTP or network errors.
        """
        headers = self.get_rotated_headers(extra_headers)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        try:
            async with session.get(url, params=params, headers=headers, timeout=timeout) as response:
                if (response.status != 200):
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"HTTPStatus_{response.status}"
                    )
                text_body = await response.text(errors="replace")
                return text_body
        except asyncio.TimeoutError as te:
            raise TimeoutError(f"RequestTimeout({self.timeout_seconds}s): {url}") from te
        except Exception as e:
            raise e

    async def fetch_post_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        form_data: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Executes a lightweight HTTP POST request using aiohttp with timeout, form data, and User-Agent rotation.
        Raises descriptive exceptions on HTTP or network errors.
        """
        headers = self.get_rotated_headers(extra_headers)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        try:
            async with session.post(url, data=form_data, params=params, headers=headers, timeout=timeout) as response:
                if (response.status != 200):
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"HTTPStatus_{response.status}"
                    )
                text_body = await response.text(errors="replace")
                return text_body
        except asyncio.TimeoutError as te:
            raise TimeoutError(f"RequestTimeout({self.timeout_seconds}s): {url}") from te
        except Exception as e:
            raise e

    async def fetch_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        method: str = "GET",
        params: Optional[Dict[str, str]] = None,
        form_data: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Any:
        """
        Executes an asynchronous HTTP request expecting JSON/XHR data.
        Supports GET and POST (form-encoded or JSON body) with X-Requested-With headers.
        """
        ajax_headers = {}
        ajax_headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
        ajax_headers["X-Requested-With"] = "XMLHttpRequest"
        if (extra_headers is not None):
            for key, value in extra_headers.items():
                ajax_headers[key] = value

        headers = self.get_rotated_headers(ajax_headers)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        try:
            if (method.upper() == "POST"):
                async with session.post(
                    url,
                    params=params,
                    data=form_data,
                    json=json_body,
                    headers=headers,
                    timeout=timeout
                ) as response:
                    if (response.status != 200):
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message=f"HTTPStatus_{response.status}"
                        )
                    raw_text = await response.text(errors="replace")
                    return json.loads(raw_text.strip())
            else:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout
                ) as response:
                    if (response.status != 200):
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=response.status,
                            message=f"HTTPStatus_{response.status}"
                        )
                    raw_text = await response.text(errors="replace")
                    return json.loads(raw_text.strip())
        except asyncio.TimeoutError as te:
            raise TimeoutError(f"RequestTimeout({self.timeout_seconds}s): {url}") from te
        except Exception as e:
            raise e

    def is_health_food_item(self, product_name: str, category_text: str = "") -> bool:
        """
        Determines whether an item belongs to the health functional food category.
        """
        combined = f"{category_text} {product_name}"
        for keyword in self.health_keywords:
            if (keyword in combined):
                return True
        return False

    @abstractmethod
    async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]:
        """
        Fetches broadcast schedules between start_date and end_date (inclusive)
        up to the channel's supported horizon.
        """
        pass
