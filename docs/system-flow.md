# 시스템 플로우 상세 명세서

> 프로그램의 전체 동작 흐름과, 각 플로우별 실제 API 호출·필드·수치·분기 조건을 정의한 문서.
> 이 문서가 곧 구현의 설계도다.

---

## 전체 플로우 맵

```
┌──────────────────────────────────────────────────────┐
│                    앱 수명주기 (24시간 상시 가동)         │
│                                                       │
│  [F0] 앱 시작 & 초기화 (1회)                            │
│       │  - Settings(.env) + Constants(코드 상수)         │
│       │  - httpx, DI, APScheduler                      │
│       │                                               │
│       ▼                                               │
│  ┌── 매 영업일 반복 (월~금, 휴장일 제외) ──────────┐     │
│  │                                                │     │
│  │  [F1] 장 시작 전 준비 (08:50)                   │     │
│  │       │  ※ 휴장일이면 SKIP                      │     │
│  │       ▼                                        │     │
│  │  ┌─── 장 운영 시간 루프 (09:05~15:20) ────┐    │     │
│  │  │                                        │    │     │
│  │  │  [F2] 스캔 사이클 (매 5분)              │    │     │
│  │  │       │  ※ 휴장일이면 SKIP              │    │     │
│  │  │       ├─ [F2-1] 잔고 동기화            │    │     │
│  │  │       ├─ [F2-2] 미체결 주문 정리        │    │     │
│  │  │       ├─ [F2-3] 보유 종목 매도 판단     │    │     │
│  │  │       ├─ [F2-4] 관심 종목 매수 판단     │    │     │
│  │  │       └─ [F2-5] 스캔 결과 로깅         │    │     │
│  │  │                                        │    │     │
│  │  └────────────────────────────────────────┘    │     │
│  │       │                                        │     │
│  │       ▼                                        │     │
│  │  [F3] 장 마감 처리 (15:30)                      │     │
│  │       │  ※ 휴장일이면 SKIP                      │     │
│  │       ▼                                        │     │
│  │  15:30~익일 08:50: 대기 (API 호출 없음)          │     │
│  │                                                │     │
│  └────────────────────────────────────────────────┘     │
│                                                       │
│  [F4] 앱 종료 (SIGTERM/SIGINT 시에만)                   │
│                                                       │
└──────────────────────────────────────────────────────┘
```

> 매매 전략 조건 상세: [trading-strategy.md](./trading-strategy.md) 참조
> 설정값(.env) 상세: [project-plan.md §7](./project-plan.md) 참조
> API 스펙 상세: [kis-api-reference.md](./kis-api-reference.md) 참조

---

## [F0] 앱 시작 & 초기화

### 목적
FastAPI 서버 기동, 외부 의존성 연결, 스케줄러 등록

### 상세 흐름

```
앱 시작 (uvicorn)
  │
  ▼
[F0-1] Settings 로드 + Constants import
  │  .env 파일 → pydantic-settings 클래스로 파싱 (Tier 1: 필수 6개 + Tier 2: 전략 파라미터)
  │  필수 검증: KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD, KIS_BASE_URL, WATCH_LIST
  │  누락 시 → 앱 시작 실패, 에러 로그
  │  코드 상수 (Tier 3): app.config.constants에서 import (MA, 비용, 품질필터, KRX_HOLIDAYS 등)
  │
  ▼
[F0-1.5] SQLite DB 초기화
  │  data/ 디렉토리 확인 → 없으면 생성
  │  trademachine.db 연결 (aiosqlite)
  │  CREATE TABLE IF NOT EXISTS: orders, daily_reports, balance_snapshots, scan_logs
  │  ※ DB 스키마 상세: data-storage.md §2 참조
  │
  ▼
[F0-2] httpx AsyncClient 생성
  │  base_url: Settings.kis_base_url
  │  timeout: connect=5s, read=10s, write=5s
  │  앱 종료 시 aclose() 호출 필요
  │
  ▼
[F0-3] Repository 인스턴스 생성 (DI)
  │  KisAuthRepository(client, settings)
  │  MarketDataRepository(client, settings, auth_repo, cache, rate_limiter)
  │  OrderRepository(client, settings, auth_repo, rate_limiter)
  │
  ▼
[F0-4] Service 인스턴스 생성 (DI)
  │  TradingService(auth_repo, market_repo, order_repo, order_log_repo, report_repo, settings)
  │
  ▼
[F0-5] APScheduler 등록 + 휴장일 처리
  │  Job 1: 장 시작 전 준비 → cron 08:50 (월~금)
  │  Job 2: 스캔 사이클 → cron 09:00~15:25, */5분 (월~금), max_instances=1
  │  Job 3: 장 마감 처리 → cron 15:30 (월~금)
  │
  │  ※ 휴장일·공휴일·주말 처리 (3단계 방어):
  │    [1차] APScheduler: day_of_week="mon-fri" → 주말(토·일) 자동 제외
  │    [2차] 각 Job 시작 시 오늘 날짜 체크:
  │          today = datetime.now().strftime("%Y%m%d")
  │          if today in C.KRX_HOLIDAYS:
  │            → Job SKIP + 로그 "휴장일: {today}"
  │    [3차] F2 스캔 중 API 비정상 응답 (거래량 0 등) → 스캔 SKIP
  │
  ▼
[F0-6] FastAPI 라우터 등록
  │  /market/* (시세 조회)
  │  /trading/* (수동 매매)
  │
  ▼
앱 Ready — 24시간 상시 가동, 스케줄러가 영업일에만 Job 실행
```

### 관련 설정값

> 설정 3계층 구조 상세: [project-plan.md §7](./project-plan.md) 참조

| 구분 | 항목 | 소스 |
|------|------|------|
| .env (Tier 1) | `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_CANO`, `KIS_ACNT_PRDT_CD`, `KIS_BASE_URL`, `WATCH_LIST` | `.env` 필수 |
| Settings (Tier 2) | `KIS_IS_PAPER_TRADING`, 전략 파라미터 18개 | `settings.py` 기본값 (.env 오버라이드 가능) |
| Constants (Tier 3) | `KRX_HOLIDAYS`, MA/비용/품질필터/안전장치 18개 | `constants.py` 코드 상수 |

---

## [F1] 장 시작 전 준비 (08:50)

### 목적
매매에 필요한 인증과 데이터를 미리 확보

### 상세 흐름

```
[F1-0] 휴장일 확인
  │  today in C.KRX_HOLIDAYS?
  │  → YES: SKIP + 로그 "휴장일: {today}, 준비 생략"
  │
  ▼
[F1-1] Access Token 발급
  │
  │  API: POST /oauth2/tokenP
  │  Body: { grant_type, appkey, appsecret }
  │  응답: access_token, expires_in
  │
  │  처리:
  │    - 토큰을 메모리에 캐싱
  │    - 만료 시간 = 현재시간 + expires_in - 3600 (1시간 여유)
  │    - 실패 시 → 30초 후 재시도 (최대 3회)
  │    - 3회 모두 실패 → 에러 로그 + 앱은 계속 동작 (스캔 시 재시도)
  │
  ▼
[F1-2] 관심 종목 목록 로드
  │
  │  소스: Settings.watch_list (= .env의 WATCH_LIST)
  │  파싱: "005930,000660,035720" → ["005930", "000660", "035720"]
  │  검증: 각 코드가 6자리 숫자인지 확인
  │
  ▼
[F1-3] 보유 종목 잔고 조회
  │
  │  API: GET /uapi/domestic-stock/v1/trading/inquire-balance
  │  TR ID: TTTC8434R (실전) / VTTC8434R (모의)
  │  Params: { CANO, ACNT_PRDT_CD, ... }
  │
  │  응답에서 추출:
  │    output1 배열의 각 항목:
  │      pdno         → 종목코드 (str)
  │      hldg_qty     → 보유수량 (str → int 변환)
  │      pchs_avg_pric → 매입평균가 (str → float 변환)
  │      evlu_pfls_rt  → 평가손익률 (str → float 변환)
  │
  │  결과: 보유 종목 리스트 메모리 저장
  │
  ▼
[F1-4] 미체결 매수 주문 전량 취소
  │
  │  API: GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld
  │  TR ID: TTTC8001R (실전) / VTTC8001R (모의)
  │  Params: { CCLD_DVSN: "02", SLL_BUY_DVSN_CD: "02" }
  │                        ↑ 미체결만       ↑ 매수만
  │
  │  결과의 각 미체결 주문에 대해:
  │    API: POST /uapi/domestic-stock/v1/trading/order-rvsecncl
  │    TR ID: TTTC0804U (실전) / VTTC0804U (모의)
  │    → 주문 취소 실행
  │
  ▼
장 시작 전 준비 완료. 09:05 첫 스캔 대기.
```

---

## [F2] 스캔 사이클 (매 5분, 09:05~15:20)

### 목적
보유 종목 매도 판단 → 관심 종목 매수 판단을 매 주기마다 수행

### 진입 조건 체크

```
스케줄러 트리거
  │
  ▼
[사전 체크 1] 휴장일 확인
  │  today = datetime.now().strftime("%Y%m%d")
  │  today in C.KRX_HOLIDAYS?
  │  → YES: SKIP + 로그 "휴장일: {today}"
  │
  ▼
[사전 체크 2] 현재 시간 확인
  ├─ < 09:05 → SKIP (장 시작 직후 관망)
  ├─ > 15:30 → SKIP (장 마감)
  ├─ > 15:20 → 매도만 실행, 매수 금지 플래그 ON
  └─ 09:05 ~ 15:20 → 정상 실행
```

---

### [F2-1] 잔고 동기화

### 목적
KIS 서버의 실제 잔고를 조회하여 메모리 상태와 동기화

```
API: GET /uapi/domestic-stock/v1/trading/inquire-balance
TR ID: TTTC8434R (실전) / VTTC8434R (모의)

Params:
  CANO: "{KIS_CANO}"                    # "50012345"
  ACNT_PRDT_CD: "{KIS_ACNT_PRDT_CD}"    # "01"
  AFHR_FLPR_YN: "N"
  OFL_YN: ""
  INQR_DVSN: "02"
  UNPR_DVSN: "01"
  FUND_STTL_ICLD_YN: "N"
  FNCG_AMT_AUTO_RDPT_YN: "N"
  PRCS_DVSN: "00"
  CTX_AREA_FK100: ""
  CTX_AREA_NK100: ""

응답 파싱 (output1 배열):
  각 항목에서:
    stock_code  = item["pdno"]                           # "005930"
    quantity    = int(item["hldg_qty"])                   # 10
    avg_price   = float(item["pchs_avg_pric"])            # 70000.0
    profit_rate = float(item["evlu_pfls_rt"])             # -2.5
    current_prc = int(item["prpr"])                       # 68250

  → quantity > 0 인 항목만 보유 종목 리스트에 저장

실패 시:
  → 재시도 1회
  → 그래도 실패 → 이번 스캔 전체 SKIP + 에러 로그
```

---

### [F2-2] 미체결 주문 정리

### 목적
이전 스캔에서 넣었지만 체결 안 된 매수 주문을 정리

```
API: GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld
TR ID: TTTC8001R (실전) / VTTC8001R (모의)

Params:
  CANO: "{KIS_CANO}"
  ACNT_PRDT_CD: "{KIS_ACNT_PRDT_CD}"
  INQR_STRT_DT: "{오늘 날짜 YYYYMMDD}"
  INQR_END_DT: "{오늘 날짜 YYYYMMDD}"
  SLL_BUY_DVSN_CD: "00"       # 전체 (매수+매도)
  INQR_DVSN: "00"
  PDNO: ""                     # 전체 종목
  CCLD_DVSN: "02"              # 미체결만
  ORD_GNO_BRNO: ""
  ODNO: ""
  INQR_DVSN_3: "00"
  INQR_DVSN_1: ""
  CTX_AREA_FK100: ""
  CTX_AREA_NK100: ""

응답 파싱:
  미체결 주문 목록에서:
    매수 주문 (sll_buy_dvsn_cd == "02"):
      → 자동 취소 (API: POST order-rvsecncl, TR ID: TTTC0804U)
    매도 주문 (sll_buy_dvsn_cd == "01"):
      → 유지 (손절 매도일 수 있으므로 취소하지 않음)

  미체결 종목코드 목록을 메모리에 저장
    → [F2-4] 매수 판단에서 중복 주문 방지용
```

---

### [F2-3] 보유 종목 매도 판단

### 목적
보유 중인 각 종목에 대해 손절/익절/데드크로스 조건을 체크하고 매도 실행

> 매도 전략 조건식 상세: [trading-strategy.md §4](./trading-strategy.md) 참조

```
보유 종목 리스트 순회 (F2-1에서 조회한 결과)
  │
  ▼ (종목별 반복)

[STEP 1] 현재가 시세 조회
  │
  │  API: GET /uapi/domestic-stock/v1/quotations/inquire-price
  │  TR ID: FHKST01010100
  │  Params:
  │    FID_COND_MRKT_DIV_CODE: "J"
  │    FID_INPUT_ISCD: "{종목코드}"
  │
  │  응답에서 추출:
  │    current_price = int(output["stck_prpr"])       # 현재가
  │    upper_limit   = int(output["stck_mxpr"])       # 상한가
  │    lower_limit   = int(output["stck_llam"])       # 하한가
  │    change_rate   = float(output["prdy_ctrt"])     # 전일대비율
  │    temp_stopped  = output["temp_stop_yn"]         # "Y"/"N"
  │
  │  실패 시 → 이 종목 SKIP, 다음 종목으로
  │
  ▼
[STEP 2] 데이터 유효성 검증
  │
  │  current_price <= 0             → SKIP (비정상 데이터)
  │  temp_stopped == "Y"            → SKIP (거래 정지)
  │  abs(change_rate) > 30.0        → SKIP (비정상 변동, 분할/병합 의심)
  │
  ▼
[STEP 3] 미체결 매도 주문 중복 체크
  │
  │  이 종목이 F2-2의 미체결 매도 목록에 있음?
  │  → YES: SKIP (이미 매도 주문 나감)
  │
  ▼
[STEP 4] 손절 체크 (최우선, P0)
  │
  │  profit_rate = F2-1에서 조회한 evlu_pfls_rt (%)
  │
  │  profit_rate <= STOP_LOSS_RATE (-5.0)?
  │  → YES: ★ 즉시 매도 실행 → [SELL] 으로 이동
  │
  ▼
[STEP 5] 익절 체크 (P1)
  │
  │  profit_rate >= TAKE_PROFIT_RATE (15.0)?
  │  → YES: ★ 매도 실행 → [SELL] 으로 이동
  │
  ▼
[STEP 5.5] 트레일링 스탑 체크 (P2)
  │
  │  메모리의 _highest_prices[종목코드] 갱신:
  │    highest = max(highest, current_price)
  │  활성화 조건: profit_rate >= TRAILING_STOP_ACTIVATE (8.0)?
  │    → YES: 트레일링 활성화 상태
  │      trailing_threshold = highest × (1 - TRAILING_STOP_RATE / 100)
  │      current_price <= trailing_threshold?
  │      → YES: ★ 매도 실행 → [SELL] (order_reason: TRAILING_STOP)
  │
  ▼
[STEP 5.7] 보유 기간 초과 체크 (P3)
  │
  │  orders 테이블에서 이 종목의 최초 BUY 성공 일시 조회
  │  (현재 영업일 - 매수일) > MAX_HOLDING_DAYS (20)?
  │  → YES: ★ 매도 실행 → [SELL] (order_reason: MAX_HOLDING)
  │
  ▼
[STEP 6] 데드크로스 체크 (확인 기간 포함, P4)
  │
  │  [6-a] 일봉 데이터 조회
  │    API: GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice
  │    TR ID: FHKST03010100
  │    Params:
  │      FID_COND_MRKT_DIV_CODE: "J"
  │      FID_INPUT_ISCD: "{종목코드}"
  │      FID_INPUT_DATE_1: "{25일 전 YYYYMMDD}"
  │      FID_INPUT_DATE_2: "{오늘 YYYYMMDD}"
  │      FID_PERIOD_DIV_CODE: "D"
  │      FID_ORG_ADJ_PRC: "0"          ← 수정주가
  │
  │    응답 output2 배열에서:
  │      각 항목의 stck_clpr (종가, str → int) 추출
  │      → 날짜 역순 정렬 (최신이 앞)
  │
  │  [6-b] MA 계산
  │    close_prices = [종가1(최신), 종가2, ..., 종가25]
  │
  │    MA5[i]  = sum(close_prices[i:i+5]) / 5
  │    MA20[i] = sum(close_prices[i:i+20]) / 20
  │
  │    오늘 MA5  = MA5[0],  어제 MA5  = MA5[1],  ...
  │    오늘 MA20 = MA20[0], 어제 MA20 = MA20[1], ...
  │
  │  [6-c] 데드크로스 판별
  │    ※ 조건식 상세: trading-strategy.md §4.1 참조
  │    요약: 최근 5일 내 MA5가 MA20 위→아래 교차 + SELL_CONFIRM_DAYS(2)일 연속 유지?
  │    → YES: ★ 매도 실행 → [SELL] 으로 이동
  │
  ▼
조건 없음 → 보유 유지 (HOLD), 다음 종목으로

────────────────────────────────────────────

[SELL] 매도 실행
  │
  │  매도 사유 기록: "STOP_LOSS" / "TAKE_PROFIT" / "TRAILING_STOP" / "MAX_HOLDING" / "DEAD_CROSS"
  │
  │  [SELL-1] Hashkey 발급
  │    API: POST /uapi/hashkey
  │    Headers: { appkey, appsecret }
  │    Body: 매도 주문과 동일한 Body
  │    응답: HASH 값
  │
  │  [SELL-2] 매도 주문 실행
  │    API: POST /uapi/domestic-stock/v1/trading/order-cash
  │    TR ID: TTTC0801U (실전) / VTTC0801U (모의)
  │    Headers: { ..., hashkey: "{HASH}" }
  │    Body:
  │      CANO: "{KIS_CANO}"
  │      ACNT_PRDT_CD: "{KIS_ACNT_PRDT_CD}"
  │      PDNO: "{종목코드}"
  │      ORD_DVSN: "01"                    ← 시장가
  │      ORD_QTY: "{보유수량 전량}"          ← str(quantity)
  │      ORD_UNPR: "0"                     ← 시장가이므로 0
  │
  │    응답 확인:
  │      rt_cd == "0" → 성공, 주문번호(ODNO) 기록
  │      rt_cd == "1" → 실패, 에러 로그
  │
  │  결과 기록:
  │    → DB INSERT: orders 테이블 (stock_code, SELL, 사유, 수량, 성공여부, ODNO)
  │    → 로그: trading.log에 매도 실행 기록
```

---

### [F2-4] 관심 종목 매수 판단

### 목적
관심 종목 중 골든크로스 + 품질 필터를 통과한 종목을 매수

> 매수 전략 조건식 상세: [trading-strategy.md §3](./trading-strategy.md) 참조
> 종목 품질 필터 상세: [trading-strategy.md §6.3](./trading-strategy.md) 참조

### 진입 조건

```
마감 전 매수 금지 (15:20 이후)? → YES: 이 단계 전체 SKIP
```

### 상세 흐름

```
관심 종목 리스트 순회
  │
  ▼ (종목별 반복)

[STEP 1] 현재가 시세 조회
  │
  │  API: GET /uapi/domestic-stock/v1/quotations/inquire-price
  │  TR ID: FHKST01010100
  │  Params:
  │    FID_COND_MRKT_DIV_CODE: "J"
  │    FID_INPUT_ISCD: "{종목코드}"
  │
  │  응답에서 추출:
  │    current_price = int(output["stck_prpr"])           # 현재가
  │    upper_limit   = int(output["stck_mxpr"])           # 상한가
  │    volume        = int(output["acml_vol"])             # 누적거래량
  │    tr_value      = int(output["acml_tr_pbmn"])        # 누적거래대금
  │    market_cap    = output["hts_avls"]                  # 시가총액
  │    is_managed    = output["mang_issu_cls_code"]        # 관리종목
  │    is_caution    = output["invt_caful_yn"]             # 투자유의
  │    is_stopped    = output["temp_stop_yn"]              # 임시정지
  │    is_clearing   = output["sltr_yn"]                   # 정리매매
  │    change_rate   = float(output["prdy_ctrt"])          # 전일대비율
  │
  │  실패 시 → 이 종목 SKIP
  │
  ▼
[STEP 2] 종목 안전성 필터
  │
  │  is_stopped == "Y"     → SKIP "거래 정지"
  │  is_managed != "00"    → SKIP "관리종목"  (※ "00"이 정상, 코드값 확인 필요)
  │  is_caution == "Y"     → SKIP "투자유의"
  │  is_clearing == "Y"    → SKIP "정리매매"
  │  current_price <= 0    → SKIP "비정상 데이터"
  │  abs(change_rate) > 30 → SKIP "비정상 변동"
  │
  ▼
[STEP 2.5] 시장 상황 필터 (ENABLE_MARKET_FILTER=true 일 때)
  │
  │  KOSPI 현재가 조회 (종목코드 "0001", market_code="U", 캐시 5초)
  │  KOSPI 일봉 25일 조회 (캐시 5분)
  │  KOSPI MA(20) 계산
  │  KOSPI 현재가 < MA(20)? → SKIP "하락장 매수 보류"
  │
  ▼
[STEP 3] 종목 품질 필터
  │
  │  current_price < MIN_STOCK_PRICE (5,000원)
  │    → SKIP "저가주 제외"
  │
  │  tr_value < MIN_TRADING_VALUE (1,000,000,000 = 10억)
  │    → SKIP "거래대금 부족"
  │
  │  volume < MIN_TRADING_VOLUME (50,000주)
  │    → SKIP "거래량 부족"
  │
  │  market_cap < MIN_MARKET_CAP (500,000,000,000 = 5,000억)
  │    → SKIP "시가총액 부족"
  │
  │  current_price == upper_limit
  │    → SKIP "상한가 종목"
  │
  ▼
[STEP 4] 이미 보유 중인지 확인
  │
  │  F2-1 잔고 목록에 이 종목이 있음?
  │  → YES: SKIP "이미 보유"
  │
  ▼
[STEP 5] 미체결 매수 주문 있는지 확인
  │
  │  F2-2 미체결 목록에 이 종목의 매수 주문이 있음?
  │  → YES: SKIP "매수 주문 진행 중"
  │
  ▼
[STEP 6] 보유 종목 수 한도 확인
  │
  │  현재 보유 종목 수 >= MAX_HOLDING_COUNT (5)?
  │  → YES: SKIP "보유 한도 초과"
  │
  ▼
[STEP 6.3] 일일 매수 횟수 확인
  │
  │  오늘 매수 성공 횟수 >= MAX_DAILY_BUY_COUNT (3)?
  │  → YES: SKIP "일일 매수 한도"
  │
  ▼
[STEP 6.5] 재매수 쿨다운 확인
  │
  │  orders 테이블에서 이 종목의 최근 SELL 성공 시각 조회
  │  현재 시각 - 매도 시각 < REBUY_COOLDOWN_HOURS (24시간)?
  │  → YES: SKIP "재매수 쿨다운"
  │
  ▼
[STEP 7] 일봉 데이터 조회 & MA 계산
  │
  │  API: GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice
  │  TR ID: FHKST03010100
  │  Params:
  │    FID_COND_MRKT_DIV_CODE: "J"
  │    FID_INPUT_ISCD: "{종목코드}"
  │    FID_INPUT_DATE_1: "{30일 전 YYYYMMDD}"
  │    FID_INPUT_DATE_2: "{오늘 YYYYMMDD}"
  │    FID_PERIOD_DIV_CODE: "D"
  │    FID_ORG_ADJ_PRC: "0"           ← 수정주가
  │
  │  데이터 검증:
  │    조회된 일봉 수 < MA_LONG_PERIOD (20)?
  │    → SKIP "데이터 부족"
  │
  │  종가 배열 추출: close_prices (최신순)
  │    각 항목의 stck_clpr (str → int)
  │
  │  MA 계산:
  │    MA5[i]  = sum(close_prices[i:i+5]) / 5
  │    MA20[i] = sum(close_prices[i:i+20]) / 20
  │
  ▼
[STEP 8] 골든크로스 판별 (확인 기간 포함)
  │
  │  ※ 조건식 상세: trading-strategy.md §3.1 참조
  │  요약 (4단계 모두 통과해야 매수):
  │    [8-a] 최근 7일 내 MA5가 MA20 아래→위 교차 발생?
  │    [8-b] 교차 후 3일 연속 MA5 > MA20 유지?
  │    [8-c] 현재가 > MA20?
  │    [8-d] MA20 상승 추세? (오늘 MA20 > 3일전 MA20)
  │    → 하나라도 NO: SKIP
  │
  ▼
[STEP 8.5] 거래량 확인 (Volume Confirmation)
  │
  │  교차일의 거래량 (candles[cross_day].volume)
  │  MA(20) 평균 거래량 = 최근 20일 거래량 평균
  │  교차일 거래량 >= 평균 × VOLUME_CONFIRM_RATIO (1.5)?
  │  → NO: SKIP "거래량 미수반 교차"
  │
  ▼
[STEP 8.7] RSI 과열 필터
  │
  │  RSI(14) 계산 (최근 14일 종가 기준)
  │  RSI > RSI_OVERBOUGHT (70)? → SKIP "과매수"
  │  RSI < RSI_OVERSOLD (30)? → SKIP "과매도"
  │  30 <= RSI <= 70 → 통과
  │
  ▼
[STEP 9] 매수 수량 계산
  │
  │  [9-a] 매수 가능 금액 조회
  │    API: GET /uapi/domestic-stock/v1/trading/inquire-psbl-order
  │    TR ID: TTTC8908R (실전) / VTTC8908R (모의)
  │    Params:
  │      CANO: "{KIS_CANO}"
  │      ACNT_PRDT_CD: "{KIS_ACNT_PRDT_CD}"
  │      PDNO: "{종목코드}"
  │      ORD_UNPR: "0"
  │      ORD_DVSN: "01"
  │      CMA_EVLU_AMT_ICLD_YN: "Y"
  │      OVRS_ICLD_YN: "Y"
  │    응답: available_cash = int(output["ord_psbl_cash"])
  │
  │  [9-b] 투자 금액 산정
  │    invest_amount = available_cash × MAX_INVESTMENT_RATIO (0.1)
  │    예: 10,000,000 × 0.1 = 1,000,000원
  │
  │  [9-c] 수량 계산
  │    quantity = invest_amount // current_price  (정수 나눗셈)
  │    예: 1,000,000 // 73,000 = 13주
  │
  │    quantity == 0?
  │    → SKIP "잔액 부족"
  │
  ▼
[STEP 10] 매수 실행 ★
  │
  │  [10-a] Hashkey 발급
  │    API: POST /uapi/hashkey
  │    Body: 매수 주문과 동일한 Body
  │    응답: HASH
  │
  │  [10-b] 매수 주문
  │    API: POST /uapi/domestic-stock/v1/trading/order-cash
  │    TR ID: TTTC0802U (실전) / VTTC0802U (모의)
  │    Headers: { ..., hashkey: "{HASH}" }
  │    Body:
  │      CANO: "{KIS_CANO}"
  │      ACNT_PRDT_CD: "{KIS_ACNT_PRDT_CD}"
  │      PDNO: "{종목코드}"                  # "005930"
  │      ORD_DVSN: "01"                     # 시장가
  │      ORD_QTY: "{수량}"                   # "13"
  │      ORD_UNPR: "0"                      # 시장가이므로 0
  │
  │    응답 확인:
  │      rt_cd == "0" → 성공
  │        주문번호 = output["ODNO"]
  │      rt_cd == "1" → 실패, 에러 로그
  │
  │  결과 기록:
  │    → DB INSERT: orders 테이블 (stock_code, BUY, GOLDEN_CROSS, 수량, 성공여부, ODNO)
  │    → 로그: trading.log에 매수 실행 기록
  │
  ▼
다음 종목으로
```

---

### [F2-5] 스캔 결과 로깅

```
스캔 결과 요약:
  │
  │  스캔 시간: 2026-03-11 10:05:00
  │  보유 종목: 3개
  │  매도 실행: 1건 (005930 손절 -5.2%)
  │  매수 실행: 1건 (000660 골든크로스 13주)
  │  SKIP 사유:
  │    035720: 거래대금 부족 (8.5억 < 10억)
  │    051910: 골든크로스 확인 중 (2/3일)
  │  에러: 0건
  │  API 호출 횟수: 17건
  │  소요 시간: 2.3초
  │
  │  → DB INSERT: scan_logs 테이블 (요약 수치)
  │  → 로그: app.log에 요약 출력, trading.log에 상세 SKIP 사유
```

---

## [F3] 장 마감 처리 (15:30)

### 목적
하루 매매 마무리, 상태 정리

```
[F3-0] 휴장일 확인
  │  today in C.KRX_HOLIDAYS?
  │  → YES: SKIP (이 날에는 F1, F2도 실행 안 했으므로 F3도 불필요)
  │
  ▼
[F3-1] 잔고 최종 조회
  │  API: inquire-balance (F2-1과 동일)
  │  → 오늘의 최종 보유 종목/수량/수익률 기록
  │
  ▼
[F3-2] 미체결 주문 정리
  │  API: inquire-daily-ccld (CCLD_DVSN: "02")
  │  미체결 매수 주문 → 전부 취소
  │  미체결 매도 주문 → 로그만 기록 (장 후 시간외 거래 가능)
  │
  ▼
[F3-3] 일일 매매 리포트 & DB 저장
  │
  │  오늘의 거래 내역:
  │    매수: N건 (종목, 수량, 금액)
  │    매도: N건 (종목, 수량, 금액, 손익)
  │    미체결: N건
  │
  │  보유 현황:
  │    총 보유 종목: N개
  │    총 평가금액: XXX원
  │    총 평가손익: XXX원 (XX.X%)
  │
  │  → DB INSERT: daily_reports 테이블 (일일 리포트 1건)
  │  → DB INSERT: balance_snapshots 테이블 (보유 종목별 1건씩)
  │  → 로그: app.log에 일일 요약 출력
  │
  ▼
장 마감 처리 완료.
15:30 이후 API 호출 없음.
다음 영업일 08:50까지 대기.
```

---

## [F4] 앱 수명주기 & 종료

### 앱은 24시간 상시 가동

```
[핵심 원칙]
  앱은 15:30 장 마감 후에도 종료되지 않는다.
  24시간 상시 가동 상태를 유지하며, APScheduler가
  다음 영업일 08:50에 자동으로 F1을 실행한다.

  ┌────────────────────────────────────────────┐
  │  08:50 → F1 실행                            │
  │  09:05~15:20 → F2 반복 실행                  │
  │  15:30 → F3 실행                            │
  │  15:30~익일 08:50 → 대기 (API 호출 없음)      │
  │  주말/휴장일 → Job 실행 안 함 (대기)           │
  └─────────────── 이 사이클이 계속 반복 ──────────┘
```

### 앱 종료 (수동 또는 시스템 종료 시에만)

```
종료 시그널 수신 (SIGTERM/SIGINT) 또는 수동 종료
  │
  ▼
APScheduler 정지
  │
  ▼
httpx AsyncClient 종료
  │  await client.aclose()
  │
  ▼
최종 로그: "앱 정상 종료"
```

> 앱 비정상 종료 후 재시작 시 복구 절차: [trading-strategy.md §9.4](./trading-strategy.md) 참조

---

## 부록: 플로우별 API 호출 매핑 요약

| 플로우 | 호출 API | TR ID | Method |
|--------|---------|-------|--------|
| F1-1 토큰 발급 | `/oauth2/tokenP` | - | POST |
| F1-3 잔고 조회 | `inquire-balance` | TTTC8434R | GET |
| F1-4 미체결 조회 | `inquire-daily-ccld` | TTTC8001R | GET |
| F1-4 주문 취소 | `order-rvsecncl` | TTTC0804U | POST |
| F2-1 잔고 조회 | `inquire-balance` | TTTC8434R | GET |
| F2-2 미체결 조회 | `inquire-daily-ccld` | TTTC8001R | GET |
| F2-3 현재가 조회 | `inquire-price` | FHKST01010100 | GET |
| F2-3 일봉 조회 | `inquire-daily-itemchartprice` | FHKST03010100 | GET |
| F2-3 Hashkey | `/uapi/hashkey` | - | POST |
| F2-3 매도 주문 | `order-cash` | TTTC0801U | POST |
| F2-4 현재가 조회 | `inquire-price` | FHKST01010100 | GET |
| F2-4 일봉 조회 | `inquire-daily-itemchartprice` | FHKST03010100 | GET |
| F2-4 매수가능 조회 | `inquire-psbl-order` | TTTC8908R | GET |
| F2-4 Hashkey | `/uapi/hashkey` | - | POST |
| F2-4 매수 주문 | `order-cash` | TTTC0802U | POST |
| F3-1 잔고 조회 | `inquire-balance` | TTTC8434R | GET |
| F3-2 미체결 조회 | `inquire-daily-ccld` | TTTC8001R | GET |
