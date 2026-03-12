# KIS Open API - 국내주식 API 레퍼런스

> 본 문서는 TradeMachine 프로젝트에서 사용할 한국투자증권 국내주식 API를 정리한 문서입니다.
> 공식 문서: https://apiportal.koreainvestment.com/apiservice

---

## 1. 공통 사항

### Base URL

| 환경 | URL |
|------|-----|
| 실전투자 | `https://openapi.koreainvestment.com:9443` |
| 모의투자 | `https://openapivts.koreainvestment.com:29443` |

### 공통 요청 헤더

| 헤더 | 값 | 설명 |
|------|-----|------|
| `Content-Type` | `application/json; charset=utf-8` | 고정 |
| `authorization` | `Bearer {ACCESS_TOKEN}` | OAuth 토큰 |
| `appkey` | `{APP_KEY}` | 발급받은 앱 키 |
| `appsecret` | `{APP_SECRET}` | 발급받은 앱 시크릿 |
| `tr_id` | 각 API별 TR ID | 거래 구분 ID |
| `custtype` | `P` | 개인 고객 |

### 호출 제한

| 제한 유형 | 수치 | 비고 |
|-----------|------|------|
| REST API 초당 | **초당 20건** | 초과 시 HTTP 429 에러 |
| 토큰 발급 | **1분에 1회** | 초과 시 `EGW00133` 에러 |
| 일일 총 호출 | **구체적 수치 미공개** | 존재하나 비공개 (추정 5,000건 내외) |
| WebSocket | **동시 접속 1세션, 41종목** | 하나의 연결에서 여러 종목 구독 |
| 동시 접속 | **1 세션** | 동일 계정 다중 동시 호출 제한 |

> 호출 제한 vs 우리 프로그램 상세 분석: [api-verification.md §10](./api-verification.md) 참조

---

## 2. OAuth 인증

### 2.1 접근토큰 발급 (Access Token)

| 항목 | 값 |
|------|-----|
| Method | `POST` |
| Path | `/oauth2/tokenP` |
| 용도 | REST API 호출에 필요한 접근 토큰 발급 |
| 유효기간 | 약 24시간 (만료 전 갱신 필요) |

**요청 Body:**
```json
{
  "grant_type": "client_credentials",
  "appkey": "{APP_KEY}",
  "appsecret": "{APP_SECRET}"
}
```

**응답:**
```json
{
  "access_token": "eyJ0eXAi...",
  "token_type": "Bearer",
  "expires_in": 86400,
  "access_token_token_expired": "2026-03-12 10:00:00"
}
```

### 2.2 WebSocket 접속키 발급 (Approval Key)

| 항목 | 값 |
|------|-----|
| Method | `POST` |
| Path | `/oauth2/Approval` |
| 용도 | WebSocket 실시간 시세 수신에 필요한 접속키 발급 |
| 유효기간 | 24시간 |

**요청 Body:**
```json
{
  "grant_type": "client_credentials",
  "appkey": "{APP_KEY}",
  "secretkey": "{APP_SECRET}"
}
```

---

## 3. [국내주식] 주문/계좌 — REST API

Base Path: `/uapi/domestic-stock/v1/trading`

### 3.1 주식 현금 매수 주문

| 항목 | 값 |
|------|-----|
| Method | `POST` |
| Path | `/uapi/domestic-stock/v1/trading/order-cash` |
| TR ID (실전) | `TTTC0802U` |
| TR ID (모의) | `VTTC0802U` |
| 우리 프로젝트 매핑 | `OrderRepository.buy()` |

**요청 Body:**
```json
{
  "CANO": "계좌번호 앞8자리",
  "ACNT_PRDT_CD": "계좌번호 뒤2자리",
  "PDNO": "005930",
  "ORD_DVSN": "00",
  "ORD_QTY": "10",
  "ORD_UNPR": "70000"
}
```

**ORD_DVSN (주문구분) 코드:**

| 코드 | 설명 |
|------|------|
| `00` | 지정가 |
| `01` | 시장가 |
| `02` | 조건부지정가 |
| `03` | 최유리지정가 |
| `04` | 최우선지정가 |
| `05` | 장전 시간외 |
| `06` | 장후 시간외 |
| `07` | 시간외 단일가 |

### 3.2 주식 현금 매도 주문

| 항목 | 값 |
|------|-----|
| Method | `POST` |
| Path | `/uapi/domestic-stock/v1/trading/order-cash` |
| TR ID (실전) | `TTTC0801U` |
| TR ID (모의) | `VTTC0801U` |
| 우리 프로젝트 매핑 | `OrderRepository.sell()` |

> 요청 Body는 매수와 동일. TR ID만 다름.

### 3.3 주식 주문 정정

| 항목 | 값 |
|------|-----|
| Method | `POST` |
| Path | `/uapi/domestic-stock/v1/trading/order-rvsecncl` |
| TR ID (실전) | `TTTC0803U` |
| TR ID (모의) | `VTTC0803U` |
| 우리 프로젝트 매핑 | `OrderRepository.modify()` |

### 3.4 주식 주문 취소

| 항목 | 값 |
|------|-----|
| Method | `POST` |
| Path | `/uapi/domestic-stock/v1/trading/order-rvsecncl` |
| TR ID (실전) | `TTTC0803U` (+ `RVSE_CNCL_DVSN_CD="02"` 로 취소 구분) |
| TR ID (모의) | `VTTC0803U` |
| 우리 프로젝트 매핑 | `OrderRepository.cancel()` |

### 3.5 주식 잔고 조회

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/trading/inquire-balance` |
| TR ID (실전) | `TTTC8434R` |
| TR ID (모의) | `VTTC8434R` |
| 우리 프로젝트 매핑 | `OrderRepository.get_balance()` |

**주요 응답 필드:**
- `pdno`: 종목코드
- `prdt_name`: 종목명
- `hldg_qty`: 보유수량
- `pchs_avg_pric`: 매입평균가격
- `prpr`: 현재가
- `evlu_pfls_rt`: 평가손익률

### 3.6 매수 가능 조회

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/trading/inquire-psbl-order` |
| TR ID (실전) | `TTTC8908R` |
| TR ID (모의) | `VTTC8908R` |
| 우리 프로젝트 매핑 | `OrderRepository.get_available_buy()` |

### 3.7 주식 일별 주문체결 조회

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/trading/inquire-daily-ccld` |
| TR ID (실전) | `TTTC8001R` |
| TR ID (모의) | `VTTC8001R` |
| 우리 프로젝트 매핑 | `OrderRepository.get_daily_orders()` |

**주요 Query 파라미터:**
- `SLL_BUY_DVSN_CD`: 매도매수구분 (`00`:전체, `01`:매도, `02`:매수)
- `CCLD_DVSN`: 체결구분 (`00`:전체, `01`:체결, `02`:미체결)
- `PDNO`: 종목코드 (전체 조회 시 빈 값)

---

## 4. [국내주식] 기본시세 — REST API

Base Path: `/uapi/domestic-stock/v1/quotations`

### 4.1 주식 현재가 시세

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-price` |
| TR ID | `FHKST01010100` |
| 우리 프로젝트 매핑 | `MarketDataRepository.get_current_price()` |

**Query 파라미터:**
- `FID_COND_MRKT_DIV_CODE`: `J` (주식)
- `FID_INPUT_ISCD`: 종목코드 (예: `005930`)

**주요 응답 필드:**
- `stck_prpr`: 현재가
- `prdy_vrss`: 전일대비
- `prdy_ctrt`: 전일대비율
- `acml_vol`: 누적거래량
- `acml_tr_pbmn`: 누적거래대금
- `stck_oprc`: 시가
- `stck_hgpr`: 고가
- `stck_lwpr`: 저가

### 4.2 주식 현재가 호가/예상체결

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` |
| TR ID | `FHKST01010200` |
| 우리 프로젝트 매핑 | `MarketDataRepository.get_orderbook()` |

**주요 응답 필드:**
- 매도호가 1~10단계 (`askp1` ~ `askp10`)
- 매수호가 1~10단계 (`bidp1` ~ `bidp10`)
- 각 호가별 잔량

### 4.3 주식 현재가 체결

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-ccnl` |
| TR ID | `FHKST01010300` |
| 우리 프로젝트 매핑 | `MarketDataRepository.get_ccnl()` |

> 당일 체결 내역 조회 (시간대별 체결가, 체결량 등)

### 4.4 주식 현재가 일자별

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-daily-price` |
| TR ID | `FHKST01010400` |
| 우리 프로젝트 매핑 | `MarketDataRepository.get_daily_price()` |

> 최근 30일간 일별 시가/고가/저가/종가/거래량 조회

### 4.5 국내주식 기간별 시세 (일/주/월/년)

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` |
| TR ID | `FHKST03010100` |
| 우리 프로젝트 매핑 | `MarketDataRepository.get_chart_price()` |
| **핵심 용도** | **이동평균선 계산을 위한 과거 데이터 조회** |

**Query 파라미터:**
- `FID_COND_MRKT_DIV_CODE`: `J` (주식)
- `FID_INPUT_ISCD`: 종목코드
- `FID_INPUT_DATE_1`: 조회 시작일 (YYYYMMDD)
- `FID_INPUT_DATE_2`: 조회 종료일 (YYYYMMDD)
- `FID_PERIOD_DIV_CODE`: `D`(일), `W`(주), `M`(월), `Y`(년)

**주요 응답 필드:**
- `stck_bsop_date`: 영업일자
- `stck_oprc`: 시가
- `stck_hgpr`: 고가
- `stck_lwpr`: 저가
- `stck_clpr`: 종가
- `acml_vol`: 거래량

### 4.6 주식 현재가 투자자

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-investor` |
| TR ID | `FHKST01010900` |
| 우리 프로젝트 매핑 | (추후 확장) |

> 개인/외국인/기관별 매수매도 동향

### 4.7 주식 현재가 회원사

| 항목 | 값 |
|------|-----|
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-member` |
| TR ID | `FHKST01010600` |
| 우리 프로젝트 매핑 | (추후 확장) |

> 증권사별 매수매도 현황

---

## 5. [국내주식] 실시간시세 — WebSocket

### 접속 정보

| 환경 | WebSocket URL |
|------|--------------|
| 실전투자 | `ws://ops.koreainvestment.com:21000` |
| 모의투자 | `ws://ops.koreainvestment.com:31000` |

### 제약사항

- **동시 접속 1개**: 체결가 + 호가를 동시에 받으려면 하나의 연결에서 구독
- **PINGPONG 유지**: 장 마감 후에도 연결 유지하려면 약 1분마다 PINGPONG 메시지 필요
- **데이터 구분**: 응답의 `trid` 필드로 체결가/호가 구분

### 5.1 실시간 체결가

| 항목 | 값 |
|------|-----|
| TR ID | `H0STCNT0` |
| 용도 | 종목별 실시간 체결 데이터 수신 |
| 우리 프로젝트 매핑 | (Phase 5 이후 확장) |

### 5.2 실시간 호가

| 항목 | 값 |
|------|-----|
| TR ID | `H0STASP0` |
| 용도 | 종목별 실시간 10단계 호가 수신 |
| 우리 프로젝트 매핑 | (Phase 5 이후 확장) |

### 5.3 실시간 체결 통보

| 항목 | 값 |
|------|-----|
| TR ID | `H0STCNI0` |
| 용도 | 내 주문의 체결/미체결 실시간 알림 |
| 우리 프로젝트 매핑 | (Phase 5 이후 확장) |

---

## 6. 프로젝트에서 사용할 API 우선순위

### Phase 1~2에서 반드시 필요 (핵심)

| API | 용도 | Repository 매핑 |
|-----|------|-----------------|
| 접근토큰 발급 | 인증 | `KisAuthRepository.get_token()` |
| 주식 현재가 시세 | 현재가 조회 | `MarketDataRepository.get_current_price()` |
| 기간별 시세 | 이동평균선 계산 데이터 | `MarketDataRepository.get_chart_price()` |
| 현금 매수 주문 | 매수 실행 | `OrderRepository.buy()` |
| 현금 매도 주문 | 매도 실행 | `OrderRepository.sell()` |
| 주식 잔고 조회 | 보유 종목 확인 | `OrderRepository.get_balance()` |

### Phase 3~4에서 필요 (보조)

| API | 용도 | Repository 매핑 |
|-----|------|-----------------|
| 호가/예상체결 | 호가 확인 후 지정가 결정 | `MarketDataRepository.get_orderbook()` |
| 매수 가능 조회 | 투자 가능 금액 확인 | `OrderRepository.get_available_buy()` |
| 일별 체결 조회 | 주문 상태 추적 | `OrderRepository.get_daily_orders()` |
| 주문 정정/취소 | 미체결 주문 관리 | `OrderRepository.modify()` / `.cancel()` |

### Phase 5 이후 (확장)

| API | 용도 |
|-----|------|
| 실시간 체결가 (WebSocket) | 실시간 모니터링 |
| 실시간 호가 (WebSocket) | 실시간 호가 추적 |
| 실시간 체결 통보 (WebSocket) | 주문 체결 알림 |
| 투자자별 매매동향 | 외국인/기관 수급 분석 |
| 현재가 일자별 | 추가 분석 데이터 |

---

## 7. 참고: TR ID 요약 테이블

### REST API

| 기능 | 실전 TR ID | 모의 TR ID |
|------|-----------|-----------|
| 매수 주문 | `TTTC0802U` | `VTTC0802U` |
| 매도 주문 | `TTTC0801U` | `VTTC0801U` |
| 주문 정정/취소 | `TTTC0803U` | `VTTC0803U` | `RVSE_CNCL_DVSN_CD`: "01"=정정, "02"=취소 |
| 잔고 조회 | `TTTC8434R` | `VTTC8434R` |
| 매수가능 조회 | `TTTC8908R` | `VTTC8908R` |
| 일별체결 조회 | `TTTC8001R` | `VTTC8001R` |
| 현재가 시세 | `FHKST01010100` | (동일) |
| 현재가 호가 | `FHKST01010200` | (동일) |
| 현재가 체결 | `FHKST01010300` | (동일) |
| 일자별 시세 | `FHKST01010400` | (동일) |
| 기간별 차트 | `FHKST03010100` | (동일) |
| 투자자별 | `FHKST01010900` | (동일) |

### WebSocket

| 기능 | TR ID |
|------|-------|
| 실시간 체결가 | `H0STCNT0` |
| 실시간 호가 | `H0STASP0` |
| 실시간 체결통보 | `H0STCNI0` |

---

## 8. 공식 리소스

- [KIS Developers 포털](https://apiportal.koreainvestment.com/)
- [API 가이드 문서](https://apiportal.koreainvestment.com/apiservice)
- [공식 GitHub (샘플 코드)](https://github.com/koreainvestment/open-trading-api)
- [전체 API 문서 (Excel 다운로드)](https://apiportal.koreainvestment.com/apiservice) — 페이지 상단 "전체 API 문서 (Excel) 다운로드"
