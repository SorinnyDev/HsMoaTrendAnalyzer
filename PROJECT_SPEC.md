# PROJECT_SPEC: HsMoa Trend Analyzer (5대 홈쇼핑 건강기능식품 트렌드 분석기)

본 문서는 `HsMoaTrendAnalyzer` 프로젝트의 아키텍처, 실행 인터페이스, API/외부 연동, 데이터 스키마 및 설정을 정의하는 단일 진실 공급원(Single Source of Truth)입니다.

---

## 1. 프로젝트 개요 및 아키텍처 (Architecture)

### 1.1 프로젝트 목적
- **5대 주요 홈쇼핑 채널(GS SHOP, CJ온스타일, NS홈쇼핑, 롯데홈쇼핑, 현대홈쇼핑)**의 비로그인 모바일 공개 편성표 및 내부 AJAX/JSON 엔드포인트를 경량 비동기 HTTP(`aiohttp`) 어댑터로 병렬 수집하여 **건강식품(건강기능식품)** 카테고리의 방송 트렌드를 정밀 분석합니다.
- 채널별 수집 헬스체크 및 연속 실패 일수를 추적(`channel_health_history`)하여 특정 채널 장애 시에도 결함 허용(Fault-tolerant) 방식으로 동작합니다.
- 방송 시간대별 가중치(**Power Score**)를 산출하고, **Google Gemini AI**를 활용해 카테고리별 상위 5대 트렌드(데이터 트렌드 및 소싱 전략)와 핵심 시너지 성분 5종(성분 인텔리전스)을 도출하여 채널 헬스체크 현황과 함께 **Telegram**으로 자동 발송합니다.
- 수집된 편성 데이터는 SQLite 수집 이력 DB, 7일 예측 슬라이딩 윈도우 엑셀, 원료별 Power Score 누적 통계 엑셀, AI 전략 리포트 텍스트 등 4개 저장소에 무결하게 영속화됩니다.

### 1.2 DDD(도메인 주도 설계) 레이어 구조
```text
HsMoaTrendAnalyzer/
├── main.py                                      # 프로젝트 루트 실행 엔트리포인트
├── Interfaces/
│   └── CLI/
│       └── main.py                              # CLI 인자 파싱 및 비동기 실행 제어
├── Application/
│   ├── Services/
│   │   └── TrendAnalysisService.py              # 멀티 채널 병렬 수집, 4대 저장소 무결 영속화, 헬스체크 및 AI 리포트 오케스트레이션
│   └── Service/
│       ├── TrendManager.py                      # 일별 상품 원료/브랜드 AI 분석 및 누적 통계(건기식 - statistics.xlsx) 갱신
│       ├── WeightCalculator.py                  # 방송 시간대별 가중치(Power Score) 계산기
│       └── NotificationService.py               # 채널별 헬스체크 포맷팅 및 Telegram Bot 메시지 발송 서비스
├── Domain/
│   ├── Models/
│   │   └── Schedule.py                          # ScheduleItem 도메인 모델 (편성표 아이템)
│   └── HomeShopping/
│       └── Service/
│           └── WindowService.py                 # 7일 슬라이딩 윈도우(D+0 ~ D+6, Today + 6일) 날짜 계산 및 과거 데이터 필터링
└── Infrastructure/
    ├── Scrapers/                                # [Adapter Pattern] 5대 홈쇼핑 비로그인 모바일 편성표 스크래퍼
    │   ├── BaseChannelScraper.py                # 추상 인터페이스 (fetch_schedule, 타임아웃, User-Agent 로테이션, 건기식 키워드 및 화장품 제외 필터링)
    │   ├── GsShopScraper.py                     # GS SHOP 모바일 공개 편성표 HTML 어댑터 (/main/broadSchedule/{YYYYMMDD}, D+0 ~ D+6)
    │   ├── CjOnstyleScraper.py                  # CJ온스타일 모바일 REST/JSON 편성표 어댑터 (bdStrDtm epoch ms 변환, D+0 ~ D+6)
    │   ├── NsMallScraper.py                     # NS홈쇼핑 모바일 백엔드 REST JSON API 어댑터 (mapi.nsmall.com, D+0 ~ D+3)
    │   ├── LotteImallScraper.py                 # 롯데홈쇼핑 모바일 JSON 편성표 어댑터 (scheduleLive.lotte / scheduleOne.lotte, D+0 ~ D+3)
    │   ├── HmallScraper.py                      # 현대홈쇼핑 모바일 TV 편성표 REST JSON API 어댑터 (/api/hf/dp/v1/main-tv-new/tv-list, D+0 ~ D+3)
    │   └── MultiChannelAggregator.py            # asyncio.gather(..., return_exceptions=True) 기반 5개 채널 병렬 수집 조율기
    ├── AI/
    │   └── GeminiClient.py                      # Google GenAI (gemini-flash-latest + 모델 폴백) 전략 리포트 연동 클라이언트
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

### 2.2 핵심 비즈니스 워크플로우 및 데이터 보관 무결성
1. **7일 슬라이딩 윈도우 병렬 동기화 모드 (`--mode forecast` 또는 `--auto`)**
   - `WindowService`를 통해 오늘부터 7일간(`D+0 ~ D+6`)의 날짜 범위를 산출합니다.
   - `MultiChannelAggregator`가 `asyncio.gather(..., return_exceptions=True)`를 사용해 5개 채널을 비동기 병렬 수집합니다.
   - 일부 채널의 파싱 지연 또는 장애 발생 시에도 정상 수집된 채널의 `ScheduleItem`은 중단 없이 온전히 합산(Fault-tolerant)됩니다.
   - **4대 저장소 무결 영속화 프로세스**:
     1. `outputs/crawling_history.db`: 각 채널의 성공/실패 상태 및 연속 실패 일수(`consecutive_failures`)를 `channel_health_history`에 갱신하고, 일자별 수집 상태를 기록합니다.
     2. `outputs/7day_forecast_sync.xlsx`: D+0 ~ D+6 윈도우 데이터를 Upsert하고 범위 밖의 만료 데이터를 정리합니다.
     3. `outputs/건기식 - statistics.xlsx`: 수집된 일자별 편성 상품에 대해 `TrendManager.process_daily_trends()`를 실행하여 원료별 Power Score 및 참여 브랜드 누적 통계를 갱신합니다.
     4. `outputs/last_ai_report.txt`: Gemini AI 전략 리포트 원문을 백업하고, `outputs/ai_reports/report_YYYYMMDD.txt`에 일자별 아카이빙합니다.
   - 직전 리포트 맥락을 로드하여 `GeminiClient`로 2개 핵심 섹션(카테고리별 상위 5대 트렌드, 오늘의 성분 인텔리전스 5종) 리포트를 렌더링합니다.
   - `NotificationService`가 상단에 `[📡 채널별 편성표 수집 현황]` 블록을 결합하여 Telegram으로 발송합니다.

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
  - 메서드: `fetch_schedule`, `create_stealth_session`, `warm_up_session`, `sleep_with_jitter`, `fetch_text`, `fetch_post_text`, `fetch_json`, `is_health_food_item`
  - **안티봇 스텔스(Stealth) 수집 프로토콜**:
    1. **모바일 브라우저 핑거프린트 프로필 동기화**: iOS Safari / Android Chrome별 `User-Agent`, `Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`, `Sec-Ch-Ua-Platform`, `Sec-Fetch-Site`, `Sec-Fetch-Mode`, `Sec-Fetch-Dest` 헤더 세트를 세션 단위로 일관되게 유지.
    2. **세션 쿠키 워밍업 (`warm_up_session`)**: 내부 AJAX/REST API 호출 전 각 채널의 모바일 편성표 진입 페이지(`Referer` URL)를 먼저 방문하여 정상 세션 쿠키(`JSESSIONID` 등)를 `CookieJar`에 선발급.
    3. **인간 행동 모사 랜덤 지터 (`sleep_with_jitter`) & 분산 출발 (`MultiChannelAggregator`)**: 날짜/페이지 전환 시 `0.65~1.65초` 랜덤 지터 지연 및 채널 간 스태거링(분산 출발) 적용, `429/403/503` 응답 시 지수 백오프 + 프로필 교체 재시도.
  - 건기식 키워드 매칭 및 화장품/이미용/의료기기 네거티브 키워드(`크림, 화장품, 에센스, 세럼, 로션, 마스크팩, 앰플, 센텔리안, 마데카, 샴푸, 치약, 기기, 혈압계, 측정기, 세라젬` 등) 사전 차단.

| 채널명 | 어댑터 클래스 | 수집 구간 | 요청 메서드 / 엔드포인트 / 파라미터 규격 | 파싱 방식 |
| :--- | :--- | :--- | :--- | :--- |
| **GS SHOP** | `GsShopScraper` | `D+0 ~ D+6` | **GET** `https://m.gsshop.com/main/broadSchedule/{YYYYMMDD}`<br>- Params: `broadType=ALL&startDate=YYYYMMDD`<br>- Headers: `X-Requested-With: XMLHttpRequest`, `Referer: https://m.gsshop.com/main/broadSchedule` | `<article class="prd-item">` DOM 및 상위 `data-broadStartDate` / `data-pgminfo` 파싱 |
| **CJ온스타일** | `CjOnstyleScraper` | `D+0 ~ D+6` | **GET** `https://display.cjonstyle.com/c/rest/tv/tvSchedule`<br>- Params: `bdDt=YYYYMMDD` | `result.programList`의 `bdStrDtm`(Epoch ms -> KST `HH:MM`) 및 하위 `itemList` 추출 |
| **NS홈쇼핑** | `NsMallScraper` | `D+0 ~ D+3` | **GET** `https://mapi.nsmall.com/md/api/v1/display/media/schedule/all/{YYYYMMDD}`<br>- Params: `formDate=YYYYMMDD`<br>- Headers: `Origin: https://m.nsmall.com`, `Referer: https://m.nsmall.com/store/fixed/schedule/tv/{YYYYMMDD}` | `data.resultData.totalOrgan` 내 `startDtm`, `goods`, `relTotalOrgan` JSON 직접 파싱 |
| **롯데홈쇼핑** | `LotteImallScraper` | `D+0 ~ D+3` | **GET** `https://m.lotteimall.com/schedule/scheduleLive.lotte` & `scheduleOne.lotte`<br>- Params: `bdDate=YYYYMMDD`<br>- Headers: `X-Requested-With: XMLHttpRequest`, `Referer: https://m.lotteimall.com/main/tvschedule.lotte?direct=L&isAlone=T` | `body.prod` 배열(`stime`, `name`, `brand`, `price_disc`, `related`, `relatedAdd`) JSON 파싱 |
| **현대홈쇼핑** | `HmallScraper` | `D+0 ~ D+3` | **GET** `https://www.hmall.com/api/hf/dp/v1/main-tv-new/tv-list`<br>- Params: `brodDt=YYYYMMDD&brodPrrgPage=1..6&categoryList=001,002`<br>- Headers: `Referer: https://www.hmall.com/md/dpl/index?mainDispSeq=2&brodType=all` | `respData.broadItemList` 및 `withItemList` 내 `slitmNm`, `brodStrtDtm`, `bbprc`/`sellPrc` JSON 직접 파싱 |

### 3.2 Google Gemini API 리포트 포맷 (`Infrastructure/AI/GeminiClient.py`)
- **SDK 및 모델**: `google-genai` 패키지 / 기본 `gemini-2.5-flash` (폴백: `gemini-flash-latest`, `gemini-2.0-flash`)
- **전략 리포트 출력 규격**:
  ```markdown
  # 건강기능식품 시장 데이터 분석 리포트

  1. **[트렌드 제목 및 전략 요약]**
     * **데이터 트렌드**: [상품명, Power Score, 방송 횟수, 패키지 구성(3/6/12/18개월 등) 분석]
     * **소싱 전략**: [용량 패키징, 제형 기술, 프로모션 할인율 대응 전략]
  ... (상위 5개 트렌드)

  ---

  ### 오늘의 성분 인텔리전스

  📍 **[원료 1] + [원료 2]**
  - 🧬 기대 효과: [상호작용 메커니즘 및 복합 케어 시너지 요약]
  - 📢 마케팅 소구점:
    * (타깃 연령층 및 구매 트리거)
    * (시장 차별점 및 제형 기술/원료 희소성 강조)
    * (섭취 편의성 및 패키지 구성 전략)
  ... (핵심 시너지 성분 5종)
  ```

### 3.3 Telegram Bot API (`Application/Service/NotificationService.py`)
- **엔드포인트**: `POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`
- **메시지 구조**: `[📡 채널별 편성표 수집 현황]` 헤더 + Gemini 전략 리포트 본문 결합 발송.

---

## 4. 데이터베이스 및 파일 스키마, 환경 설정 (DB Schema & Config)

### 4.1 SQLite 데이터베이스 (`outputs/crawling_history.db`)
- **관리 클래스**: `SqliteRepository`
- **테이블 1**: `crawling_history` (`id`, `target_date`, `status`, `items_count`, `crawled_at`)
- **테이블 2**: `channel_health_history` (`channel_name`, `last_status`, `consecutive_failures`, `last_error_message`, `last_updated_at`)

### 4.2 도메인 모델 및 4대 저장소 (`outputs/`)
1. `outputs/crawling_history.db`: 일별 수집 상태 및 채널별 헬스체크 히스토리
2. `outputs/7day_forecast_sync.xlsx`: D+0 ~ D+6 기간 슬라이딩 윈도우 동기화 엑셀 (`sync_key`: `{date}_{time}_{channel}_{product_name}`)
3. `outputs/건기식 - statistics.xlsx`: 원료별 일일 누적 통계 엑셀 (`날짜`, `원료명`, `방송 횟수`, `Power Score`, `Weighted Count`, `참여 브랜드 리스트`)
4. `outputs/last_ai_report.txt` & `outputs/ai_reports/report_YYYYMMDD.txt`: 전략 리포트 텍스트 백업

### 4.3 환경 변수 (`.env`)
- `GEMINI_API_KEY`: Google Gemini API 인증 키
- `TELEGRAM_BOT_TOKEN`: 텔레그램 봇 토큰
- `TELEGRAM_CHAT_ID`: 텔레그램 채팅방 ID
- `OUTPUT_DIR`: 결과 파일 저장 디렉토리 (기본 `./outputs`)

---

## 5. 최근 변경 이력
- `[2026-11-09] 안티봇 스텔스(Stealth) 수집 프로토콜 도입(모바일 브라우저 핑거프린트 프로필 헤더 동기화, 편성표 진입 페이지 세션 쿠키 워밍업, 랜덤 지터 지연 및 채널 간 스태거링 분산 출발, 429/403 지수 백오프 재시도)`
- `[2026-11-09] 5대 홈쇼핑 실제 패킷 검증 기반 엔드포인트 전면 교체(GS SHOP /main/broadSchedule/{YYYYMMDD}, NS홈쇼핑 mapi.nsmall.com REST API, 롯데홈쇼핑 scheduleLive.lotte/scheduleOne.lotte JSON API, 현대홈쇼핑 /api/hf/dp/v1/main-tv-new/tv-list REST API, CJ온스타일 bdStrDtm Epoch 변환), 화장품/의료기기 오탐 제거 필터링, SyncManager sync_key 중복 누락 버그 수정, Gemini API 크레딧 소진 시 룰 기반 원료 분석/심층 전략 리포트 폴백 및 텔레그램 장문 분할 발송 적용`
- `[2026-11-06] GS SHOP(POST AJAX), NS홈쇼핑(REST API+HTML폴백), 롯데홈쇼핑(POST AJAX) 스크래퍼 정밀 정상화, 4대 저장소 무결 영속화(건기식 통계 엑셀 연동), Gemini AI 전략 리포트 심층 포맷 복원`
- `[2026-11-03] 4개 홈쇼핑 스크래퍼(롯데, GS, NS, 현대) 모바일 JSON/AJAX 엔드포인트 교체 및 부분 수집 시 Gemini 리포트 생성 방어/폴백 로직 강화`
- `[2026-11-01] DataHub 크롤러 제거 및 5대 홈쇼핑 비로그인 병렬 어댑터(Infrastructure/Scrapers) 구축, 채널별 헬스체크 DB(channel_health_history) 및 텔레그램 상태 리포트 연동, D+7 슬라이딩 윈도우 전환`
- `[2026-11-01] 프로젝트 전체 구조 분석 및 초기 PROJECT_SPEC.md 명세서 작성`
