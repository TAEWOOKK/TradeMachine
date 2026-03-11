# TradeMachine - KIS API 주식 자동매매 봇 프로젝트 계획서

## 1. 프로젝트 개요

한국투자증권(KIS) Open API를 활용한 **규칙 기반(Rule-based) 스윙 트레이딩** 자동매매 봇.
**국내주식(KRX 코스피/코스닥)** 전용으로, 소수점 매매 없이 정수 단위 매매만 수행한다.
AI 예측이 아닌 명확한 조건식(이동평균선 교차 + 거래량 확인 + RSI 필터)에 의한 매매 전략을 사용하며,
수수료 최소화를 위해 잦은 단타보다는 조건 충족 시에만 매수/매도를 실행한다.

### 운영 원칙

> **모의투자 우선 원칙:** 반드시 모의투자(Paper Trading)로 먼저 운영하고,
> 실측 데이터로 전략을 검증한 후에만 실전 전환한다.
> `.env`의 `KIS_IS_PAPER_TRADING=true`를 `false`로 바꾸면 실전 전환된다.

| 단계 | 기간 | 목적 |
|------|------|------|
| 모의투자 | 최소 3개월 | 실측 승률/MDD 확인, 버그 수정 |
| 소액 실전 | 1~2개월 | 100만원으로 실전 슬리피지/체결 검증 |
| 점진적 증액 | 이후 | 실측 데이터 기반 자산 배분 |

### 문서 구조 & 역할 (Single Source of Truth)

| 문서 | 역할 | 기준(SoT) 영역 |
|------|------|---------------|
| **본 문서 (project-plan.md)** | 전체 계획 허브. 아키텍처, 폴더 구조, .env 마스터 표, Phase | .env 설정값, 기술 스택, 아키텍처, 구현 Phase |
| [system-flow.md](./system-flow.md) | 구현 설계도. 플로우별 API 호출·분기·필드 상세 | 스케줄링 구현, API 호출 흐름, 휴장일 처리, 앱 수명주기 |
| [trading-strategy.md](./trading-strategy.md) | 매매 전략 규칙. 조건식·필터·리스크·예외 처리 | 매매 조건식, 품질 필터, 리스크 관리, 예외 상황 |
| [kis-api-reference.md](./kis-api-reference.md) | API 엔드포인트 레퍼런스. TR ID·필드·파라미터 | API 스펙, TR ID, 요청/응답 필드 |
| [kis-integration-guide.md](./kis-integration-guide.md) | API 연동 가이드. 인증·에러 처리·httpx 패턴 | 인증 플로우, 에러 코드, 호출 패턴 |
| [api-verification.md](./api-verification.md) | API 가능 여부 검증 결과 (일회성) | 검증 결과, 계획 변경 사항 |
| [data-storage.md](./data-storage.md) | 데이터 저장소 설계. DB 스키마·로그·캐시 구조 | 테이블 스키마, 저장소 전략, 데이터 정리 정책 |
| [implementation-spec.md](./implementation-spec.md) | 구현 상세 명세서. 클래스·메서드·로직 설계도 | 클래스 시그니처, 메서드 로직, DI 구조, 구현 순서 |

---

## 2. 기술 스택

| 구분 | 기술 | 비고 |
|------|------|------|
| Framework | **FastAPI** | 비동기 웹 프레임워크 |
| Language | **Python 3.14** | Type Hinting 완전 적용 |
| Scheduling | **APScheduler** | 장 운영 시간 기반 스케줄링 |
| HTTP Client | **httpx** | 비동기 KIS API 통신 |
| Config | **pydantic-settings** | `.env` 기반 설정 관리 |
| DB | **SQLite** + aiosqlite | 매매 내역, 일일 리포트, 잔고 스냅샷, 스캔 로그 |
| DB (추후 확장) | PostgreSQL + asyncpg | 다중 전략·웹 대시보드 확장 시 마이그레이션 |
| Logging | Python logging | RotatingFileHandler (app.log, trading.log, error.log) |

---

## 3. 아키텍처 설계

Spring 프레임워크와 유사한 **계층형 아키텍처(Layered Architecture)** 를 엄격하게 적용한다.

```
┌─────────────────────────────────────────┐
│              Router (Controller)         │  ← API 엔드포인트 요청/응답 처리
├─────────────────────────────────────────┤
│              Service                     │  ← 핵심 비즈니스 로직 / 매매 전략
├─────────────────────────────────────────┤
│              Repository                  │  ← KIS API 외부 통신 / DB 저장
├─────────────────────────────────────────┤
│              Model (Domain / DTO)        │  ← 데이터 구조 정의
└─────────────────────────────────────────┘
```

### 3.1 계층별 책임

- **Router (Controller)**: HTTP 요청을 받아 Service에 위임하고, 응답을 클라이언트에 반환
- **Service**: 핵심 비즈니스 로직 수행. 매매 전략(규칙 기반) 판단 후 Repository 호출
- **Repository**: KIS API와의 외부 HTTP 통신 담당 (SimpleRepository 패턴). 추후 DB I/O 포함
- **Model**: Domain 모델과 요청/응답 DTO 분리

### 3.2 핵심 설계 원칙

- **의존성 주입(DI)**: Service → Repository 방향으로 의존. FastAPI의 `Depends` 활용
- **@transactional 데코레이터**: 데이터 일관성 유지를 위한 커스텀 데코레이터
- **비밀키·전략 파라미터**: `.env` + `pydantic-settings`로 관리. 업계 표준·제도 상수는 `constants.py`에 명시적으로 정의 (§7 3계층 구조 참조)
- **완전한 Type Hinting**: 모든 함수, 변수에 타입 힌트 적용

---

## 4. 프로젝트 폴더 구조

```
TradeMachine/
├── .cursor/
│   └── rules/                            # Cursor AI 프로젝트 규칙
├── docs/
│   └── project-plan.md                   # 본 문서
├── data/
│   └── trademachine.db                   # SQLite DB (자동 생성, .gitignore)
├── logs/
│   ├── app.log                           # 앱 전반 로그 (INFO 이상)
│   ├── trading.log                       # 매매 전용 로그
│   └── error.log                         # 에러 전용 로그 (WARNING 이상)
├── app/
│   ├── __init__.py
│   ├── main.py                           # FastAPI 앱 팩토리 (create_app)
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py                   # pydantic-settings (.env + 전략 파라미터)
│   │   └── constants.py                  # 코드 상수 (업계 표준, 비용, 품질 필터, 시스템)
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py                   # SQLite 연결 관리 (aiosqlite)
│   │   ├── cache.py                      # 메모리 TTL 캐시 (현재가, 일봉)
│   │   ├── dependencies.py               # DI 컨테이너 (FastAPI Depends 팩토리)
│   │   ├── exceptions.py                 # 커스텀 예외 클래스
│   │   ├── logging_config.py             # 로그 설정 (RotatingFileHandler)
│   │   └── rate_limiter.py               # API 호출 속도 제한 (초당 N건)
│   ├── model/
│   │   ├── __init__.py
│   │   ├── domain.py                     # 도메인 모델 (Stock, Order, Position 등)
│   │   └── dto.py                        # 요청/응답 DTO (Pydantic BaseModel)
│   ├── repository/
│   │   ├── __init__.py
│   │   ├── kis_auth_repository.py        # KIS 인증 토큰 발급/캐싱/갱신
│   │   ├── market_data_repository.py     # 현재가, 호가 조회 (httpx 비동기)
│   │   ├── order_repository.py           # 매수/매도 주문 실행
│   │   ├── order_log_repository.py       # orders 테이블 CRUD (SQLite)
│   │   └── report_repository.py          # daily_reports, balance_snapshots, scan_logs CRUD
│   ├── service/
│   │   ├── __init__.py
│   │   └── trading_service.py            # 매매 전략 로직 (규칙 기반 스윙)
│   ├── router/
│   │   ├── __init__.py
│   │   ├── market_router.py              # GET /market/* 시세 조회 엔드포인트
│   │   └── trading_router.py             # POST /trading/* 매매 실행 엔드포인트
│   └── scheduler/
│       ├── __init__.py
│       └── trading_scheduler.py          # APScheduler 장 시간 기반 스케줄러
├── tests/                                # 테스트 코드 (추후)
├── .env.example                          # 환경변수 템플릿
├── requirements.txt                      # 의존성 목록
└── main.py                               # uvicorn 엔트리포인트
```

---

## 5. 핵심 기능 명세

### 5.1 KIS API 인증 (kis_auth_repository)

| 항목 | 내용 |
|------|------|
| 책임 | App Key + App Secret으로 Access Token 발급 |
| 토큰 캐싱 | 메모리 캐시 (만료 시간 기반 자동 갱신) |
| API | `POST /oauth2/tokenP` |
| 갱신 정책 | 만료 전 여유 시간(예: 5분) 남았을 때 선제 갱신 |

### 5.2 시세 조회 (market_data_repository)

| 항목 | 내용 |
|------|------|
| 현재가 조회 | 종목 코드로 현재가, 등락률, 거래량 등 조회 |
| 호가 조회 | 매수/매도 호가 10단계 조회 |
| 일봉 조회 | 이동평균선 계산을 위한 과거 데이터 조회 |
| 통신 방식 | httpx AsyncClient 비동기 호출 |

### 5.3 주문 실행 (order_repository)

| 항목 | 내용 |
|------|------|
| 매수 주문 | 지정가/시장가 매수 |
| 매도 주문 | 지정가/시장가 매도 |
| 주문 조회 | 미체결 주문 내역 조회 |
| 주문 취소 | 미체결 주문 취소 |

### 5.4 매매 전략 (trading_service)

| 항목 | 내용 |
|------|------|
| 전략 유형 | 규칙 기반 스윙 트레이딩 |
| 매수 조건 | 골든크로스 + 3일 확인 + 거래량 확인(1.5배) + RSI 필터(30~70) + MA(20) 상승 + 품질 필터 |
| 매도 조건 | 손절(-5%) / 익절(+15%) / 트레일링스탑(고점-4%) / 데드크로스(2일) / 보유기간 초과(20일) |
| 리스크 관리 | 종목당 10%, 최대 5종목, 일일 매수 3회, 재매수 쿨다운 24h, 잡주+시가총액 필터, KOSPI 하락장 매수 차단 |
| 비용 구조 | 왕복 약 0.36% (수수료+세금 0.21% + 슬리피지 0.15%), 손익분기 승률 약 50.9% |
| 확장 포인트 | Strategy 인터페이스를 두어 전략 교체 가능하도록 설계 |

> 전략 조건식·필터·예외 처리 상세: [trading-strategy.md](./trading-strategy.md) 참조
> 실행 플로우 상세: [system-flow.md](./system-flow.md) §F2 참조

### 5.5 스케줄러 (trading_scheduler)

| 항목 | 내용 |
|------|------|
| 장 시작 전 초기화 | **08:50** — 토큰 발급, 관심 종목 로드, 잔고 동기화, 미체결 정리 |
| 장 시작 직후 관망 | **09:00~09:05** — 시가 형성 대기, 매매 안 함 |
| 정규 스캔 | **09:05~15:20** — 매 5분 간격 (매수+매도 판단) |
| 마감 전 매수 금지 | **15:20 이후** — 매도만 허용, 신규 매수 금지 |
| 장 마감 처리 | **15:30** — 잔고 최종 조회, 미체결 매수 취소, 일일 리포트 로그 |
| 장 마감 후 | **15:30 이후** — API 호출 없음, 다음 영업일 08:50까지 대기 |
| 앱 수명주기 | 24시간 상시 가동. 스케줄러가 영업일에만 Job 실행 |

> 스케줄러 구현 상세: [system-flow.md](./system-flow.md) §F0~F3 참조
> 매매 전략 상세: [trading-strategy.md](./trading-strategy.md) 참조

### 5.6 휴장일 · 공휴일 · 주말 처리

KRX(한국거래소)는 아래 날짜에 정규장을 운영하지 않는다.
**이 날에는 스케줄러 Job이 실행되지 않으며, API 호출도 하지 않는다.**

| 구분 | 규칙 |
|------|------|
| **주말** | 토요일·일요일 — 장 미개장 |
| **법정 공휴일** | 신정, 설날 연휴, 삼일절, 어린이날, 부처님오신날, 광복절, 추석 연휴, 개천절, 한글날, 성탄절 |
| **대체 공휴일** | 공휴일이 주말과 겹치면 다음 평일로 이동 (대체공휴일법 적용) |
| **근로자의 날** | 5월 1일 — 법정 공휴일은 아니나 KRX 휴장 |
| **연말 휴장** | 12월 31일 — 매년 고정 |
| **임시 공휴일** | 정부 지정 임시공휴일 (선거일 등) — KRX도 함께 휴장 |
| **반일 장** | 연초 개장식 등 특수 상황 — 보통 10:00 개장, 15:30 마감 |

#### 구현 방식

```
[1차 방어: APScheduler cron 설정]
  - day_of_week: "mon-fri" → 주말 자동 제외
  - 스케줄러 자체가 평일에만 Job 실행

[2차 방어: KRX 휴장일 목록 관리]
  - constants.py의 KRX_HOLIDAYS: set[str] 로 관리 (매년 갱신, 코드 수정 후 재배포)
  - 스캔 시작 시: today in C.KRX_HOLIDAYS → Job SKIP

[3차 방어: API 응답 기반 감지]
  - 장 미개장 상태에서 API 호출 시 비정상 응답 (거래량 0 등)
  - 이 경우 해당 스캔 SKIP + 로그 기록
```

> 2026년 기준 총 **약 16일** 휴장 (주말 제외).
> 대체공휴일이 많은 해이므로 목록 관리가 중요하다.

---

## 6. 데이터 모델 (초안)

### Domain Models

```python
# Stock: 종목 정보
- stock_code: str          # 종목 코드 (예: "005930")
- stock_name: str          # 종목명 (예: "삼성전자")
- current_price: int       # 현재가
- change_rate: float       # 등락률

# Order: 주문 정보
- order_id: str            # 주문 번호
- stock_code: str          # 종목 코드
- order_type: OrderType    # BUY / SELL
- price_type: PriceType    # LIMIT(지정가) / MARKET(시장가)
- quantity: int            # 수량
- price: int               # 주문 가격
- status: OrderStatus      # PENDING / FILLED / CANCELLED

# Position: 보유 포지션
- stock_code: str          # 종목 코드
- quantity: int            # 보유 수량
- avg_price: int           # 평균 매입가
- current_price: int       # 현재가
- profit_rate: float       # 수익률
```

### Request/Response DTOs

```python
# OrderRequest: 주문 요청
- stock_code: str
- order_type: OrderType
- price_type: PriceType
- quantity: int
- price: int | None        # 시장가일 경우 None

# MarketDataResponse: 시세 응답
- stock_code: str
- stock_name: str
- current_price: int
- change_rate: float
- volume: int
- moving_avg_5: float | None
- moving_avg_20: float | None
```

---

## 7. 설정 관리 — 3계층 구조

> 설정값을 **변경 빈도와 민감도**에 따라 3계층으로 분리한다.

| 계층 | 위치 | 역할 | 개수 |
|------|------|------|------|
| **Tier 1: `.env`** | `.env` 파일 | 비밀키·환경별 설정. 코드에 절대 노출 금지 | **6개** |
| **Tier 2: Settings** | `app/config/settings.py` | 운영 중 조정 가능한 전략 파라미터. .env로 오버라이드 가능하지만 기본값이 있어 .env에 안 써도 됨 | **18개** |
| **Tier 3: Constants** | `app/config/constants.py` | 업계 표준·제도·시스템 상수. 코드 수정 없이는 변경 불필요 | **18개** |

### 7.1 Tier 1 — `.env` 파일 (필수 6개)

> 이것만 `.env`에 작성하면 프로그램이 동작한다.

```env
# KIS API 인증 (한국투자증권에서 발급)
KIS_APP_KEY=PSxxxxxxxxxxxxxxxxxxx
KIS_APP_SECRET=Rh0xxxxxxxxxxxxxxxxxxxxxxx
KIS_CANO=50012345                         # 계좌번호 앞 8자리
KIS_ACNT_PRDT_CD=01                       # 계좌번호 뒤 2자리

# 모의투자: https://openapivts.koreainvestment.com:29443
# 실전투자: https://openapi.koreainvestment.com:9443
KIS_BASE_URL=https://openapivts.koreainvestment.com:29443

# 관심 종목 (쉼표 구분)
WATCH_LIST=005930,000660,035720,051910,006400
```

### 7.2 Tier 2 — Settings 기본값 (18개, .env 오버라이드 가능)

> `app/config/settings.py`의 `Settings` 클래스에 기본값으로 정의.
> 운영 중 조정이 필요하면 `.env`에 해당 키를 추가하여 오버라이드한다.

| 그룹 | 키 | 기본값 | 설명 |
|------|-----|--------|------|
| 환경 | `KIS_IS_PAPER_TRADING` | `true` | 모의/실전 스위치 |
| 스케줄링 | `TRADING_INTERVAL_MINUTES` | `5` | 스캔 간격 (분) |
| 신호 확인 | `SIGNAL_LOOKBACK_DAYS` | `7` | 교차 감지 범위 (일) |
| | `SIGNAL_CONFIRM_DAYS` | `3` | 골든크로스 유지 확인 일수 |
| | `SELL_CONFIRM_DAYS` | `2` | 데드크로스 유지 확인 일수 |
| 보조지표 | `VOLUME_CONFIRM_RATIO` | `1.5` | 교차일 거래량 >= 평균 × N배 |
| | `RSI_OVERBOUGHT` | `70` | RSI 과매수 기준 |
| | `RSI_OVERSOLD` | `30` | RSI 과매도 기준 |
| 리스크 | `MAX_INVESTMENT_RATIO` | `0.1` | 종목당 최대 투자비율 (10%) |
| | `MAX_HOLDING_COUNT` | `5` | 최대 보유 종목 수 |
| | `MAX_DAILY_BUY_COUNT` | `3` | 일일 최대 매수 횟수 |
| | `MAX_HOLDING_DAYS` | `20` | 최대 보유 기간 (영업일) |
| | `STOP_LOSS_RATE` | `-5.0` | 손절 라인 (%) |
| | `TAKE_PROFIT_RATE` | `15.0` | 익절 라인 (%) |
| | `TRAILING_STOP_ACTIVATE` | `8.0` | 트레일링 활성화 (%) |
| | `TRAILING_STOP_RATE` | `4.0` | 고점 대비 하락 허용 (%) |
| | `REBUY_COOLDOWN_HOURS` | `24` | 재매수 금지 시간 |
| 시장필터 | `ENABLE_MARKET_FILTER` | `true` | KOSPI 하락장 매수 차단 |

### 7.3 Tier 3 — 코드 상수 (18개, `app/config/constants.py`)

> 업계 표준·정부 제도·시스템 내부값. 거의 바뀌지 않으므로 코드에 직접 정의한다.
> 변경이 필요하면 코드 수정 후 재배포.

| 그룹 | 상수명 | 값 | 설명 |
|------|--------|-----|------|
| 이동평균선 | `MA_SHORT_PERIOD` | `5` | 단기 MA (업계 표준) |
| | `MA_LONG_PERIOD` | `20` | 중기 MA (업계 표준) |
| | `RSI_PERIOD` | `14` | RSI 기간 (업계 표준) |
| 매매 비용 | `BUY_FEE_RATE` | `0.00015` | 매수 수수료 (0.015%) |
| | `SELL_FEE_RATE` | `0.00015` | 매도 수수료 (0.015%) |
| | `SELL_TAX_RATE` | `0.0018` | 거래세 (0.18%) |
| | `SLIPPAGE_RATE` | `0.0015` | 슬리피지 추정 (0.15%) |
| 품질 필터 | `MIN_STOCK_PRICE` | `5_000` | 최소 주가 (원) |
| | `MIN_TRADING_VALUE` | `1_000_000_000` | 최소 거래대금 (10억) |
| | `MIN_TRADING_VOLUME` | `50_000` | 최소 거래량 (주) |
| | `MIN_MARKET_CAP` | `500_000_000_000` | 최소 시총 (5000억) |
| 안전장치 | `ABNORMAL_CHANGE_RATE` | `30.0` | 비정상 변동률 기준 (%) |
| | `MAX_API_RETRY` | `3` | API 재시도 횟수 |
| | `API_RETRY_DELAY_SECONDS` | `2` | 재시도 대기 (초) |
| | `MAX_CONSECUTIVE_FAILURES` | `5` | 연속실패 시 스캔 SKIP |
| | `RATE_LIMIT_PER_SECOND` | `10` | 초당 API 호출 제한 |
| | `TRADING_CUTOFF_MINUTES` | `10` | 장 마감 N분 전 매수 금지 |
| 휴장일 | `KRX_HOLIDAYS` | `{20260101, ...}` | KRX 휴장일 set (매년 갱신) |

---

## 8. 의존성 목록

```
fastapi
uvicorn[standard]
httpx
pydantic-settings
apscheduler
python-dotenv
aiosqlite
```

---

## 9. 구현 우선순위 (Phase)

### Phase 1: 기반 구조 세팅 (스캐폴딩)
- [ ] 프로젝트 스캐폴딩 (폴더 구조, __init__.py)
- [ ] settings.py (Tier 1 + Tier 2) + constants.py (Tier 3)
- [ ] FastAPI 앱 팩토리 + uvicorn 엔트리포인트
- [ ] 커스텀 예외 클래스, RateLimiter
- [ ] 로그 설정 (logging config, RotatingFileHandler)

### Phase 2: KIS API 연동 + 인프라
- [ ] SQLite 연결 (database.py, aiosqlite, 테이블 자동 생성)
- [ ] 메모리 TTL 캐시 (cache.py)
- [ ] DI 컨테이너 (dependencies.py)
- [ ] 인증 토큰 발급/캐싱 (kis_auth_repository)
- [ ] 현재가/일봉 조회 (market_data_repository)
- [ ] 매수/매도 주문 실행 (order_repository)
- [ ] 주문 기록 (order_log_repository + orders 테이블)
- [ ] 리포트 저장 (report_repository)
- [ ] 시세 조회 API 엔드포인트 (market_router)

### Phase 3: 매매 전략 + 보조지표
- [ ] 이동평균선 계산 (SMA)
- [ ] RSI 계산 로직
- [ ] 골든 크로스 판별 (+ 거래량 확인 + RSI 필터)
- [ ] 데드 크로스 판별
- [ ] 트레일링 스탑, 보유기간 체크
- [ ] 시장 필터 (KOSPI MA(20))
- [ ] Trading Service 통합
- [ ] scan_logs 테이블 + trading.log

### Phase 4: 스케줄러 & 자동화
- [ ] APScheduler 장 시간 기반 스케줄링
- [ ] 장 시작 전 초기화 / 장 마감 처리
- [ ] 매매 실행 API 엔드포인트 (trading_router)
- [ ] daily_reports + balance_snapshots 테이블
- [ ] 데이터 정리 배치

### Phase 5: 모의투자 운영 (최소 3개월)
- [ ] **모의투자 환경 배포** (`KIS_IS_PAPER_TRADING=true`)
- [ ] 일일 리포트 자동 기록 → 승률/MDD/평균이익 추적
- [ ] 주간 리뷰: 매매 로그 분석, 전략 파라미터 검토
- [ ] 실측 데이터 기반 성과 판단 기준:
  - 승률 50%+ → Phase 6 진행
  - 승률 45~50% → 파라미터 조정 후 1개월 연장
  - 승률 45% 미만 → 전략 교체 또는 보조지표 추가

### Phase 6: 소액 실전 (선택적)
- [ ] **실전 전환** (`KIS_IS_PAPER_TRADING=false`)
- [ ] 초기 자산: 100만원 (잃어도 되는 금액)
- [ ] 슬리피지 실측: 모의 vs 실전 체결가 비교
- [ ] 1개월 운영 후 실전 성과 ≥ 모의 성과의 70% 확인
- [ ] 미달 시 모의투자로 복귀

### Phase 7: 안정화 & 확장
- [ ] 점진적 자산 증액 (실측 MDD의 2배까지만 투자)
- [ ] Strategy 인터페이스 도입 (전략 플러그인화)
- [ ] 백테스트 모듈 (과거 일봉 데이터로 전략 시뮬레이션)
- [ ] 웹 대시보드 (매매 내역, 수익률 차트)
- [ ] PostgreSQL 마이그레이션 (선택)

---

## 10. 참고 자료

- [한국투자증권 KIS Developers](https://apiportal.koreainvestment.com/)
- [KIS Open API 문서](https://apiportal.koreainvestment.com/apiservice)
- [FastAPI 공식 문서](https://fastapi.tiangolo.com/)
- [APScheduler 공식 문서](https://apscheduler.readthedocs.io/)
