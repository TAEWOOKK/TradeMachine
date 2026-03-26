# TradeMachine

![TradeMachine Dashboard](docs/dashboard-screenshot.png)

한국투자증권(KIS) Open API 기반 **국내 주식 자동매매 시스템**.

규칙 기반(Rule-based) 스윙 트레이딩 전략을 사용하며, 골든크로스/데드크로스, RSI, 거래량 확인, 시장 필터 등의 기술적 지표로 매수/매도를 판단합니다.

## 기술 스택

| 항목 | 기술 |
|------|------|
| Language | Python 3.14 |
| Framework | FastAPI |
| Scheduler | APScheduler (AsyncIOScheduler) |
| HTTP Client | httpx (async) |
| Database | SQLite (aiosqlite) |
| Config | pydantic-settings (.env) |

## 프로젝트 구조

```
TradeMachine/
├── app/
│   ├── config/
│   │   ├── settings.py          # Tier 1 (.env) + Tier 2 (기본값) 설정
│   │   └── constants.py         # Tier 3: 코드 상수 (MA, 수수료, 필터)
│   ├── core/
│   │   ├── database.py          # SQLite 연결 (WAL, aiosqlite)
│   │   ├── cache.py             # 메모리 TTL 캐시
│   │   ├── rate_limiter.py      # API 호출 속도 제한
│   │   ├── exceptions.py        # 커스텀 예외
│   │   ├── logging_config.py    # 로깅 설정 (RotatingFileHandler)
│   │   └── dependencies.py      # DI 컨테이너 (lifespan 초기화)
│   ├── model/
│   │   ├── domain.py            # 도메인 모델 (Position, StockPrice, etc.)
│   │   └── dto.py               # API 요청/응답 DTO
│   ├── repository/
│   │   ├── kis_auth_repository.py      # 토큰 발급/갱신, hashkey
│   │   ├── market_data_repository.py   # 현재가, 일봉 조회
│   │   ├── order_repository.py         # 잔고, 주문, 미체결 조회
│   │   ├── order_log_repository.py     # 주문 로그 DB 관리
│   │   └── report_repository.py        # 스캔 로그, 일일 리포트
│   ├── service/
│   │   └── trading_service.py   # 핵심 매매 로직 (전략 엔진)
│   ├── router/
│   │   ├── market_router.py     # GET /market/price, /market/balance
│   │   └── trading_router.py    # POST /trading/scan, /trading/order
│   ├── scheduler/
│   │   └── trading_scheduler.py # APScheduler cron jobs
│   └── main.py                  # FastAPI 앱 팩토리
├── tests/
│   ├── unit/                    # 유닛 테스트 (44개)
│   ├── integration/             # 통합 테스트 (33개)
│   └── scenario/                # 시나리오 테스트 (4개)
├── docs/                        # 설계 문서
├── pyproject.toml
├── main.py                      # uvicorn 엔트리포인트
└── .env.example
```

## 설치 및 실행

### 1. 가상환경 생성 및 의존성 설치

```bash
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

pip install -e ".[dev]"
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 **필수 6개 값**을 입력합니다:

```env
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_앱시크릿
KIS_CANO=계좌번호_앞8자리
KIS_ACNT_PRDT_CD=01
KIS_BASE_URL=https://openapivts.koreainvestment.com:29443
WATCH_LIST=005930,000660,035720
```

| 변수 | 설명 |
|------|------|
| `KIS_APP_KEY` | KIS Developers에서 발급받은 앱 키 |
| `KIS_APP_SECRET` | KIS Developers에서 발급받은 앱 시크릿 |
| `KIS_CANO` | 계좌번호 앞 8자리 |
| `KIS_ACNT_PRDT_CD` | 계좌 상품코드 (보통 `01`) |
| `KIS_BASE_URL` | 모의투자: `https://openapivts.koreainvestment.com:29443` / 실전: `https://openapi.koreainvestment.com:9443` |
| `WATCH_LIST` | 감시할 종목코드 (쉼표 구분) |

### 3. 서버 실행

```bash
python main.py
```

또는:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

서버가 실행되면:
- API 문서: `http://localhost:8000/docs`
- 스케줄러가 자동으로 장 시간에 맞춰 매매를 수행합니다

### 4. 수동 API 사용

```bash
# 현재가 조회
curl http://localhost:8000/market/price/005930

# 잔고 조회
curl http://localhost:8000/market/balance

# 수동 스캔 트리거
curl -X POST http://localhost:8000/trading/scan

# 수동 주문
curl -X POST http://localhost:8000/trading/order \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "005930", "order_type": "BUY", "quantity": 1}'
```

## 테스트

```bash
# 전체 테스트 실행
pytest

# 상세 출력
pytest -v

# 커버리지 포함
pytest --cov=app --cov-report=term-missing

# 특정 카테고리만
pytest tests/unit/
pytest tests/integration/
pytest tests/scenario/
```

## 자동 매매 스케줄

| 시간 | 작업 | 설명 |
|------|------|------|
| 08:50 | Pre-market | 토큰 발급, 카운터 초기화, 미체결 정리 |
| 09:00~15:25 | Scan (5분 간격) | 매도 판단 전 구간 / **신규 매수**는 `SCALPING_ENTRY_MINUTE`(기본 09:25) 이후 |
| 15:28 (월~금) | EOD close | 보유 종목 전량 청산 (야간·익일 갭 리스크 방지) |
| 15:30 | Post-market | 일일 리포트 저장, 잔고 스냅샷 |

주말 및 KRX 휴장일에는 자동으로 스킵됩니다.

## 매매 전략 요약 (단타)

상세·기본값은 [docs/trading-strategy.md](docs/trading-strategy.md) 참조.

**매수 조건** (대략, 모두 충족):
1. KOSPI > MA(20) (시장 필터, 설정으로 끌 수 있음)
2. 종목 품질 필터 (주가·거래대금·거래량·시총)
3. 장 시작 **N분 이후**(기본 09:25) — `SCALPING_ENTRY_MINUTE`
4. **전일 종가 대비** 등락률이 설정 구간 내(기본 +0.3% ~ +4%)
5. 일봉 기준 MA(5) > MA(20) 3일 유지, 현재가 ≥ MA(5)
6. RSI(14) 기본 50~65 (`RSI_SCALPING_MIN` / `MAX`)

**매도 조건** (우선순위, `.env`로 조정):
1. 손절 (예: -2%)
2. 익절 (예: +2%)
3. 트레일링 스탑 (예: +1.5% 활성화 후 고점 대비 -0.8%)
4. 최대 보유 영업일 초과 (예: 2일) — EOD가 실패했을 때의 안전망
5. 데드크로스 (MA5 < MA20, 2일 유지)
6. **매 영업일 15:28** 장마감 전량 청산 (`EOD_CLOSE_ENABLED`)

## 설정 관리 (3계층)

| 계층 | 위치 | 역할 |
|------|------|------|
| Tier 1 | `.env` | 비밀키, 환경별 설정 (6개) |
| Tier 2 | `app/config/settings.py` | 조정 가능한 전략 파라미터 (18개) |
| Tier 3 | `app/config/constants.py` | 업계 표준, 제도 상수 (18개) |

Tier 2 파라미터는 `.env`에 추가하여 오버라이드할 수 있습니다:

```env
# 예: 손절 기준을 -3%로 변경
STOP_LOSS_RATE=-3.0

# 예: 최대 보유 종목 수를 3으로 변경
MAX_HOLDING_COUNT=3
```

## 모의투자 → 실전 전환

1. 모의투자 (기본값): `KIS_BASE_URL=https://openapivts.koreainvestment.com:29443`
2. 실전 전환: `.env`에서 `KIS_BASE_URL`을 실전 URL로 변경 + `KIS_IS_PAPER_TRADING=false` 추가

```env
KIS_BASE_URL=https://openapi.koreainvestment.com:9443
KIS_IS_PAPER_TRADING=false
```

## 데이터 저장

- **SQLite** (`data/trademachine.db`): 주문 로그, 일일 리포트, 잔고 스냅샷, 스캔 로그
- **로그 파일** (`logs/`): app.log, trading.log, error.log (각 10MB, 로테이션)
