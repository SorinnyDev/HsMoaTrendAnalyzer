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
    Provides anti-bot stealth HTTP/JSON fetching:
    - Coherent mobile browser fingerprint profiles (UA + Sec-CH-UA + Sec-Fetch-* headers)
    - Session cookie warm-up (visiting landing/Referer page before hitting XHR/JSON endpoints)
    - Human-like randomized jitter delays between requests and exponential backoff on 429/5xx
    - Health functional food keyword & negative keyword (cosmetics/devices) filtering
    """

    def __init__(self, channel_name: str, max_days_ahead: int = 6, timeout_seconds: int = 15) -> None:
        self.channel_name = channel_name
        self.max_days_ahead = max_days_ahead
        self.timeout_seconds = timeout_seconds
        self.browser_profiles: List[Dict[str, str]] = self._build_stealth_profiles()
        self.active_profile: Dict[str, str] = random.choice(self.browser_profiles)
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
        self.negative_keywords = [
            "크림", "화장품", "에센스", "세럼", "로션", "마스크팩", "마스크", "리프트앰플",
            "앰플", "센텔리안", "마데카", "스킨", "토너", "미스트", "클렌징", "클렌저",
            "선크림", "썬크림", "선스틱", "선케어", "팩트", "쿠션", "파운데이션", "립스틱",
            "틴트", "아이패치", "기미패치", "염색", "샴푸", "트리트먼트", "린스", "헤어",
            "바디워시", "바디로션", "핸드크림", "치약", "칫솔", "가글", "기기", "마사지기",
            "안마기", "안마의자", "이미용", "제모", "고데기", "드라이기", "혈압계", "측정기",
            "의료기기", "보청기", "찜질기", "저주파", "세라젬", "비비", "파데", "토닝",
            "글로우", "메이크업", "아이라이너", "마스카라", "네일", "향수"
        ]

    def _build_stealth_profiles(self) -> List[Dict[str, str]]:
        """
        Builds coherent mobile browser fingerprint profiles (iOS Safari & Android Chrome)
        so User-Agent and Client Hints (Sec-CH-UA*) never contradict each other.
        """
        profiles: List[Dict[str, str]] = []

        p1 = {}
        p1["User-Agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        p1["Accept-Language"] = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        p1["Sec-Fetch-Site"] = "same-origin"
        p1["Sec-Fetch-Mode"] = "cors"
        p1["Sec-Fetch-Dest"] = "empty"
        profiles.append(p1)

        p2 = {}
        p2["User-Agent"] = "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36"
        p2["Accept-Language"] = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        p2["Sec-Ch-Ua"] = "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\", \"Google Chrome\";v=\"126\""
        p2["Sec-Ch-Ua-Mobile"] = "?1"
        p2["Sec-Ch-Ua-Platform"] = "\"Android\""
        p2["Sec-Fetch-Site"] = "same-origin"
        p2["Sec-Fetch-Mode"] = "cors"
        p2["Sec-Fetch-Dest"] = "empty"
        profiles.append(p2)

        p3 = {}
        p3["User-Agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        p3["Accept-Language"] = "ko-KR,ko;q=0.9"
        p3["Sec-Fetch-Site"] = "same-origin"
        p3["Sec-Fetch-Mode"] = "cors"
        p3["Sec-Fetch-Dest"] = "empty"
        profiles.append(p3)

        p4 = {}
        p4["User-Agent"] = "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36"
        p4["Accept-Language"] = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        p4["Sec-Ch-Ua"] = "\"Google Chrome\";v=\"125\", \"Chromium\";v=\"125\", \"Not.A/Brand\";v=\"24\""
        p4["Sec-Ch-Ua-Mobile"] = "?1"
        p4["Sec-Ch-Ua-Platform"] = "\"Android\""
        p4["Sec-Fetch-Site"] = "same-origin"
        p4["Sec-Fetch-Mode"] = "cors"
        p4["Sec-Fetch-Dest"] = "empty"
        profiles.append(p4)

        return profiles

    def create_stealth_session(self) -> aiohttp.ClientSession:
        """
        Creates an aiohttp ClientSession with an active browser profile and unsafe CookieJar
        so cookies are persisted across domain/subdomain boundaries during a scraping session.
        """
        self.active_profile = random.choice(self.browser_profiles)
        cookie_jar = aiohttp.CookieJar(unsafe=True)
        connector = aiohttp.TCPConnector(limit=6, ttl_dns_cache=300)
        return aiohttp.ClientSession(cookie_jar=cookie_jar, connector=connector)

    async def warm_up_session(self, session: aiohttp.ClientSession, landing_url: str) -> None:
        """
        Performs a human-like initial page visit to acquire session cookies (JSESSIONID, WAF tokens, etc.)
        before calling internal AJAX/REST endpoints.
        """
        nav_headers = {}
        for (k, v) in self.active_profile.items():
            nav_headers[k] = v
        nav_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        nav_headers["Sec-Fetch-Site"] = "none"
        nav_headers["Sec-Fetch-Mode"] = "navigate"
        nav_headers["Sec-Fetch-Dest"] = "document"
        nav_headers["Upgrade-Insecure-Requests"] = "1"

        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with session.get(landing_url, headers=nav_headers, timeout=timeout, allow_redirects=True) as resp:
                await resp.read()
            await self.sleep_with_jitter(0.35, 0.85)
        except Exception as e:
            logger.debug(f"[{self.channel_name}] Warm-up visit skipped ({landing_url}): {e}")

    async def sleep_with_jitter(self, min_seconds: float = 0.6, max_seconds: float = 1.6) -> None:
        """
        Sleeps for a randomized human-like duration to avoid deterministic bot rate signatures.
        """
        delay = random.uniform(min_seconds, max_seconds)
        await asyncio.sleep(delay)

    def get_rotated_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Builds HTTP headers from the active stealth browser profile so all requests within
        a session share a consistent fingerprint.
        """
        headers = {}
        for (k, v) in self.active_profile.items():
            headers[k] = v
        headers["Accept"] = "application/json, text/javascript, text/html, application/xhtml+xml, */*;q=0.9"
        headers["Connection"] = "keep-alive"
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"

        if (extra_headers is not None):
            for (key, value) in extra_headers.items():
                headers[key] = value

            if ("Origin" in extra_headers):
                headers["Sec-Fetch-Site"] = "same-site"

        return headers

    async def fetch_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Executes a stealth HTTP GET request using aiohttp with exponential backoff + jitter
        when rate-limited (429/403/503).
        """
        headers = self.get_rotated_headers(extra_headers)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        max_attempts = 3
        last_error: Optional[Exception] = None

        for (attempt) in range(1, max_attempts + 1):
            try:
                async with session.get(url, params=params, headers=headers, timeout=timeout) as response:
                    if (response.status == 200):
                        text_body = await response.text(errors="replace")
                        return text_body

                    if (response.status in [429, 403, 502, 503, 504] and attempt < max_attempts):
                        backoff = (1.5 ** attempt) + random.uniform(0.4, 1.2)
                        logger.warning(f"[{self.channel_name}] HTTP {response.status} on {url}, retrying in {backoff:.2f}s (attempt {attempt}/{max_attempts})")
                        await asyncio.sleep(backoff)
                        self.active_profile = random.choice(self.browser_profiles)
                        headers = self.get_rotated_headers(extra_headers)
                        continue

                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"HTTPStatus_{response.status}"
                    )
            except asyncio.TimeoutError as te:
                last_error = TimeoutError(f"RequestTimeout({self.timeout_seconds}s): {url}")
                if (attempt < max_attempts):
                    await self.sleep_with_jitter(0.8, 1.8)
                    continue
                raise last_error from te
            except Exception as e:
                last_error = e
                if (attempt < max_attempts and not isinstance(e, aiohttp.ClientResponseError)):
                    await self.sleep_with_jitter(0.6, 1.4)
                    continue
                raise e

        if (last_error is not None):
            raise last_error
        raise RuntimeError(f"Failed to fetch {url}")

    async def fetch_post_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        form_data: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Executes a stealth HTTP POST request using aiohttp with timeout, form data, and backoff retry.
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
            for (key, value) in extra_headers.items():
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
        Excludes cosmetics, beauty devices, and daily necessities via negative keywords first.
        """
        combined = f"{category_text} {product_name}"
        for (neg_word) in self.negative_keywords:
            if (neg_word in combined):
                return False

        for (keyword) in self.health_keywords:
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
