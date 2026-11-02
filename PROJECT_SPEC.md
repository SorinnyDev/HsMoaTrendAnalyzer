# PROJECT_SPEC: HsMoa Trend Analyzer (5대 홈쇼핑 건강기능식품 트렌드 분석기)

본 문서는 `HsMoaTrendAnalyzer` 프로젝트의 아키텍처, 실행 인터페이스, API/외부 연동, 데이터 스키마 및 설정을 정의하는 단일 진실 공급원(Single Source of Truth)입니다.

---

## 1. 프로젝트 개요 및 아키텍처 (Architecture)

### 1.1 프로젝트 목적
- **5대 주요 홈쇼핑 채널(GS SHOP, CJ온스타일, NS홈쇼핑, 롯데홈쇼핑, 현대홈쇼핑)**의 비로그인 모바일 공개 편성표 및 내부 JSON/AJAX 엔드포인트를 경량 비동기 HTTP(`aiohttp`) 어댑터로 병렬 수집하여 **건강식품(건강기능식품)** 카테고리의 방송 트렌드를 분석합니다.
- 채널별 수집 헬스체크 및 연속 실패 일수를 추적(`channel_health_history`)하여 특정 채널 장애 시에도 결함 허용(Fault-tolerant) 방식으로 동작합니다.
- 방송 시간대별 가중치(**Power Score**)를 산출하고, **Google Gemini AI**를 활용해 원료/브랜드 추출 및 전략적 시장 분석 리포트(성분 인텔리전스 포함)를 생성하여 채널 헬스체크 현황과 함께 **Telegram**으로 자동 발송합니다.

### 1.2 DDD(도메인 주도 설계) 레이어 구조
```text
HsMoaTrendAnalyzer/
├── main.py                                      # 프로젝트 루트 실행 엔트리포인트
├── Interfaces/
│   └── CLI/
│       └── main.py                              # CLI 인자 파싱 및 비동기 실행 제어
├── Application/
│   ├── Services/
│   │   └── TrendAnalysisService.py              # 멀티 채널 병렬 수집, D+7 슬라이딩 윈도우 동기화, 헬스체크 및 AI 리포트 방어/오케스트레이션
│   └── Service/
│       ├── TrendManager.py                      # 일별 상품 원료/브랜드 AI 분석 및 누적 통계 갱신
│       ├── WeightCalculator.py                  # 방송 시간대별 가중치(Power Score) 계산기
│       └── NotificationService.py               # 채널별 헬스체크 포맷팅 및 Telegram Bot 메시지 발송 서비스
├── Domain/
│   ├── Models/
│   │   └── Schedule.py                          # ScheduleItem 도메인 모델 (편성표 아이템)
│   └── HomeShopping/
│       └── Service/
│           └── WindowService.py                 # 7일 슬라이딩 윈도우(D+0 ~ D+6, Today + 6일) 날짜 계산 및 과거 데이터 필터링
└── Infrastructure/
    ├── Scrapers/                                # [Adapter Pattern] 5대 홈쇼핑 비로그인 공개 편성표 스크래퍼
    │   ├── BaseChannelScraper.py                # 추상 인터페이스 (fetch_schedule, fetch_json, 타임아웃, User-Agent 로테이션, 건강식품 필터링)
    │   ├── GsShopScraper.py                     # GS SHOP 모바일 JSON/AJAX 편성표 어댑터 (D+0 ~ D+6)
    │   ├── CjOnstyleScraper.py                  # CJ온스타일 모바일 REST/JSON 편성표 어댑터 (D+0 ~ D+6)
    │   ├── NsMallScraper.py                     # NS홈쇼핑 모바일 JSON/REST 편성표 어댑터 (D+0 ~ D+3)
    │   ├── LotteImallScraper.py                 # 롯데홈쇼핑 모바일 편성표 어댑터 (D+0 ~ D+3)
    │   ├── HmallScraper.py                      # 현대홈쇼핑 모바일 TV 편성표 JSON/AJAX 어댑터 (D+0 ~ D+3)
    │   └── MultiChannelAggregator.py            # asyncio.gather(..., return_exceptions=True) 기반 5개 채널 병렬 수집 조율기
    ├── AI/
    │   └── GeminiClient.py                      # Google GenAI (gemini-flash-latest + 모델 폴백) 연동 클라이언트
    └── Persistence/
        ├── SqliteRepository.py                  # SQLite 기반 일별 수집 이력(crawling_history) 및 채널 헬스체크(channel_health_history) 영속화
        ├── ExcelRepository.py                   # 월별 편성표 Excel 파일(시트별 일자 저장) 관리
        └── SyncManager.py                       # 7일(D+0 ~ D+6) 슬라이딩 윈도우 Excel Upsert 및 만료 데이터 정리
```

---

## 2. 실행 인터페이스 및 워크플로우 (Interfaces & Workflows)

본 시스템은 별도의 GUI 화면 없이 **CLI(Command Line Interface)**와 **Docker 데몬 스케줄러**, **Telegram 리포트 알림**을 사용자 인터페이스로 제공합니다.

### 2.1 CLI 실행 옵션 (`main.py` / `Interfaces/CLI/main.py`)
| 인자 (Argument) | 타입 / 선택지 | 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| `--mode` | `daily` \| `forecast` | `daily` | 실행 모드 선택 (`daily`: 단일 일자 5대 채널 수집, `forecast`: D+0 ~ D+6 7일 슬라이딩 윈도우 병렬 동기화 및 리포트 발송) |
| `--date` | `YYYY-MM-DD` | `None` (오늘) | `daily` 모드 실행 시 수집할 대상 날짜 지정 |
| `--auto` | Flag (`store_true`) | `False` | 자동 연속 수집 모드 (`run_sliding_window_sync()` 실행) |
| `--send-report` | Flag (`store_true`) | `False` | `daily` 모드 완료 후 누적 통계 기반 AI 요약 리포트 및 헬스체크 현황을 Telegram으로 발송 (`forecast` 모드는 기본 발송 포함) |

### 2.2 핵심 비즈니스 워크플로우
1. **7일 슬라이딩 윈도우 병렬 동기화 모드 (`--mode forecast` 또는 `--auto`)**
   - `WindowService`를 통해 오늘부터 7일간(`D+0 ~ D+6`)의 날짜 범위를 산출합니다.
   - `MultiChannelAggregator`가 `asyncio.gather(..., return_exceptions=True)`를 사용해 5개 채널(`GS SHOP`, `CJ온스타일`, `NS홈쇼핑`, `롯데홈쇼핑`, `현대홈쇼핑`)을 비동기 병렬 수집합니다.
   - 일부 채널에서 파싱 실패나 네트워크 오류가 발생해도 정상 수집된 채널(예: CJ온스타일 등)의 `ScheduleItem`은 중단 없이 합산(Fault-tolerant)됩니다.
   - `SqliteRepository.update_channel_health()`를 통해 각 채널의 성공/실패 여부와 연속 실패 일수(`consecutive_failures`)를 `channel_health_history` 테이블에 기록합니다.
   - `SyncManager`를 통해 `7day_forecast_sync.xlsx`에 데이터를 Upsert하고, 윈도우 범위(`D+0 ~ D+6`) 밖의 과거/만료 데이터를 정리합니다. (파일 I/O 실패 시에도 인메모리 수집 데이터 폴백을 통해 리포트 생성을 보장합니다.)
   - 직전 리포트(`last_ai_report.txt`) 맥락을 로드(실패 문구 오염 방지 필터 포함)하여 `GeminiClient`로 주간 전략 리포트 및 '오늘의 성분 인텔리전스(상위 5개 성분)'를 생성하며, Gemini API 일시 오류 시 다중 모델 폴백(`gemini-flash-latest` -> `gemini-2.0-flash` -> `gemini-1.5-flash`) 및 결정론적 통계 요약 폴백 리포트를 제공합니다.
   - `NotificationService`가 상단에 `[📡 채널별 편성표 수집 현황]` 블록을 결합하여 Telegram으로 발송하고 `outputs/ai_reports/report_YYYYMMDD.txt`에 아카이빙합니다.
2. **단일 일자 수집 모드 (`--mode daily --date YYYY-MM-DD`)**
   - 지정된 단일 날짜에 대해 5대 채널 병렬 수집 및 채널별 헬스체크 DB 갱신을 수행합니다.
   - 채널+상품명 기준 중복 제거(`deduplicate_items`) 후 `홈쇼핑모아 편성표 - MM월.xlsx` 내 `MM-DD` 시트에 저장합니다.
   - `TrendManager`를 통해 브랜드/원료를 Gemini로 추출하고 시간대별 가중치를 합산하여 `건기식 - statistics.xlsx`에 누적 저장합니다.

### 2.3 방송 시간대별 가중치 (`WeightCalculator`)
| 시간대 구분 | 해당 시간 (Hour 기준) | 가중치 (Power Score) |
| :--- | :--- | :--- |
| **Prime (골든타임)** | `09:00 ~ 11:59`, `20:00 ~ 22:59` | **1.5** |
| **Active (오후 주요)** | `13:00 ~ 16:59` | **1.2** |
| **Standard (일반)** | 그 외 시간대 (`00시, 06~08시, 12시, 17~19시, 23시`) | **1.0** |
| **Late Night (심야)** | `01:00 ~ 05:59` | **0.7** |

---

## 3. 외부 API 및 어댑터 연동 명세 (Scrapers & Integrations)

### 3.1 5대 홈쇼핑 모바일 공개 편성표 어댑터 (`Infrastructure/Scrapers/`)
- **공통 추상 클래스**: `BaseChannelScraper`
  - 핵심 메서드: `async def fetch_schedule(self, start_date: date, end_date: date) -> List[ScheduleItem]`
  - `aiohttp.ClientSession` 기반 경량 HTTP/JSON 요청 (`fetch_text`, `fetch_json`, `X-Requested-With: XMLHttpRequest` 헤더 포함), 타임아웃(기본 15초), 모바일 User-Agent 로테이션, 건강기능식품 키워드/카테고리 필터링 및 `ScraperParseException` 예외 전파.

| 채널명 | 어댑터 클래스 | 수집 구간 | 대상 모바일 JSON / AJAX 엔드포인트 (우선순위 순) |
| :--- | :--- | :--- | :--- |
| **GS SHOP** | `GsShopScraper` | `D+0 ~ D+6` | 1. `https://m.gsshop.com/shop/tv/tvScheduleAjax.gs` (`searchDate=YYYYMMDD`)<br>2. `https://m.gsshop.com/api/shop/tv/tvScheduleList.gs`<br>3. `https://m.gsshop.com/shop/tv/tvScheduleDetail.gs` |
| **CJ온스타일** | `CjOnstyleScraper` | `D+0 ~ D+6` | 1. `https://display.cjonstyle.com/c/rest/tv/tvSchedule` (`bdDt=YYYYMMDD`)<br>2. `https://display.cjonstyle.com/p/tv/tvSchedule` |
| **NS홈쇼핑** | `NsMallScraper` | `D+0 ~ D+3` | 1. `https://m.nsmall.com/tv/schedule/getTvScheduleList` (`selectDay=YYYYMMDD`)<br>2. `https://api.nsmall.com/api/v1/display/schedule/tv`<br>3. `https://m.nsmall.com/NSAjaxTVSchedule` |
| **롯데홈쇼핑** | `LotteImallScraper` | `D+0 ~ D+3` | 1. `https://m.lotteimall.com/display/viewTvSchedule.lotte` (`bd_date=YYYYMMDD`)<br>2. `https://m.lotteimall.com/display/selectTvScheduleListAjax.lotte`<br>3. `https://www.lotteimall.com/display/selectTvScheduleListAjax.lotte` |
| **현대홈쇼핑** | `HmallScraper` | `D+0 ~ D+3` | 1. `https://www.hmall.com/p/bmc/selectTvSchedule.do` (`brodDt=YYYYMMDD`)<br>2. `https://m.hmall.com/p/bmc/selectTvSchedule.do`<br>3. `https://www.hmall.com/p/bmc/brodPordList.do` |

### 3.2 Google Gemini API (`Infrastructure/AI/GeminiClient.py`)
- **SDK 및 모델**: `google-genai` 패키지 / 기본 `gemini-flash-latest` (폴백 모델: `gemini-2.0-flash`, `gemini-1.5-flash`)
- **주요 기능**:
  1. `analyze_product_name(product_name)`: 상품명에서 `브랜드|원료` 포맷으로 핵심 성분과 브랜드명 추출 (실패 시 `미분류|미분류` 반환)
  2. `generate_daily_report(stats_summary_text, previous_report_text)`: 이전 분석 맥락(실패 문구 오염 제외)과 신규 데이터 요약을 비교하여 변화 추이, 소싱 권고, 상위 5개 성분의 '성분 인텔리전스(추천 조합 성분, 기대 효과, 마케팅 소구점 3가지)' 리포트 생성

### 3.3 Telegram Bot API 및 메시지 포맷 (`Application/Service/NotificationService.py`)
- **엔드포인트**: `POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`
- **메시지 출력 규격**:
  ```text
  [📡 채널별 편성표 수집 현황]
  • GS SHOP: 정상 수집 (38건)
  • CJ온스타일: 정상 수집 (25건)
  • NS홈쇼핑: 정상 수집 (42건)
  • 롯데홈쇼핑: ⚠️ 실패 (연속 실패 3일 / Error: SelectorNotFound)
  • 현대홈쇼핑: 정상 수집 (19건)

  (기존 주간 성분 랭킹 및 Gemini 분석 리포트 내용)
  ```

---

## 4. 데이터베이스 및 파일 스키마, 환경 설정 (DB Schema & Config)

### 4.1 SQLite 데이터베이스 (`outputs/crawling_history.db`)
- **관리 클래스**: `SqliteRepository`

#### (1) `crawling_history` 테이블 (인덱스: `idx_target_date ON crawling_history(target_date)`)
| 컬럼명 | 데이터 타입 | 제약 조건 | 설명 |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | 고유 식별자 |
| `target_date` | `TEXT` | `UNIQUE NOT NULL` | 수집 대상 방송 일자 (`YYYY-MM-DD`) |
| `status` | `TEXT` | `NOT NULL` | 수집 상태 (`SUCCESS`, `NO_DATA`, `FAILED`) |
| `items_count` | `INTEGER` | `DEFAULT 0` | 수집된 건강식품 편성 아이템 수 |
| `crawled_at` | `TIMESTAMP` | `DEFAULT CURRENT_TIMESTAMP` | 수집 수행 일시 (`YYYY-MM-DD HH:MM:SS`) |

#### (2) `channel_health_history` 테이블 (채널별 헬스체크 및 연속 실패 추적)
| 컬럼명 | 데이터 타입 | 제약 조건 | 설명 |
| :--- | :--- | :--- | :--- |
| `channel_name` | `TEXT` | `PRIMARY KEY` | 홈쇼핑 채널명 (`GS SHOP`, `CJ온스타일`, `NS홈쇼핑`, `롯데홈쇼핑`, `현대홈쇼핑`) |
| `last_status` | `TEXT` | `NOT NULL` | 최근 수집 상태 (`SUCCESS` / `FAILURE`) |
| `consecutive_failures` | `INTEGER` | `DEFAULT 0` | 연속 실패 일수 (성공 시 `0` 초기화, 실패 시 `+1` 누적) |
| `last_error_message` | `TEXT` | `DEFAULT ''` | 최근 발생 예외/오류 메시지 (예: `SelectorNotFound`) |
| `last_updated_at` | `TIMESTAMP` | `DEFAULT CURRENT_TIMESTAMP` | 상태 최종 갱신 일시 (`YYYY-MM-DD HH:MM:SS`) |

### 4.2 도메인 모델 및 Excel/파일 저장소 (`outputs/`)
1. **도메인 모델 (`ScheduleItem` in `Domain/Models/Schedule.py`)**
   - 필드: `broadcast_date`, `broadcast_time`, `channel`, `category`, `product_name`, `price`, `link_url`, `estimated_sales`, `estimated_revenue`
2. **월별 편성표 (`outputs/홈쇼핑모아 편성표 - {MM}월.xlsx`)**
   - 시트명: `{MM}-{DD}`
   - 컬럼: `방송날짜`, `방송시간`, `채널`, `카테고리`, `상품명`, `가격`, `링크URL`, `추정판매량`, `추정매출액`
3. **7일 슬라이딩 윈도우 동기화 파일 (`outputs/7day_forecast_sync.xlsx`)**
   - Upsert 고유키(`sync_key`): `{broadcast_date}_{broadcast_time}_{channel}`
   - 컬럼: `broadcast_date`, `broadcast_time`, `channel`, `product_name`, `category`, `price`, `power_score`, `sync_key`
4. **원료별 일일 누적 통계 파일 (`outputs/건기식 - statistics.xlsx`)**
   - 컬럼: `날짜`, `원료명`, `방송 횟수`, `Power Score`, `Weighted Count`, `참여 브랜드 리스트`
5. **AI 리포트 텍스트 아카이브**
   - `outputs/last_ai_report.txt`: 다음 실행 시 연속성(Context) 부여를 위한 직전 리포트 본문
   - `outputs/ai_reports/report_{YYYYMMDD}.txt`: 일자별 AI 리포트 히스토리 보관본

### 4.3 환경 변수 및 인프라 설정 (`.env` & Docker)
| 환경 변수명 | 필수 여부 | 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| `GEMINI_API_KEY` | 필수 | - | Google Gemini API 인증 키 |
| `TELEGRAM_BOT_TOKEN` | 필수 | - | 리포트 발송용 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 필수 | - | 리포트 수신 대상 텔레그램 채팅방 ID |
| `OUTPUT_DIR` | 선택 | `./outputs` | 결과 파일 및 DB 저장 디렉토리 경로 |
| `CRAWLER_SCHEDULE_TIME` | 선택 | `07:00` | Docker 컨테이너(`entrypoint.sh`) 일일 자동 실행 시각 (`HH:MM`, KST 기준) |
| `RUN_ON_STARTUP` | 선택 | `false` | Docker 컨테이너 시작 시 즉시 1회 분석 실행 여부 (`true` / `false`) |

---

## 5. 최근 변경 이력
- `[2026-11-03] 4개 홈쇼핑 스크래퍼(롯데, GS, NS, 현대) 모바일 JSON/AJAX 엔드포인트 교체 및 부분 수집 시 Gemini 리포트 생성 방어/폴백 로직 강화`
- `[2026-11-01] DataHub 크롤러 제거 및 5대 홈쇼핑 비로그인 병렬 어댑터(Infrastructure/Scrapers) 구축, 채널별 헬스체크 DB(channel_health_history) 및 텔레그램 상태 리포트 연동, D+7 슬라이딩 윈도우 전환`
- `[2026-11-01] 프로젝트 전체 구조 분석 및 초기 PROJECT_SPEC.md 명세서 작성`
