# 구현 상세 명세서

> 각 파일을 어떤 클래스·메서드·로직으로 구현할지 정의한 문서.
> 이 문서가 곧 **코딩의 설계도**다. 여기에 적힌 시그니처와 로직을 그대로 코드로 옮기면 된다.
>
> 관련 문서:
> - 아키텍처·폴더 구조: [project-plan.md](./project-plan.md)
> - 플로우별 API 호출: [system-flow.md](./system-flow.md)
> - 매매 전략 규칙: [trading-strategy.md](./trading-strategy.md)
> - DB 스키마: [data-storage.md](./data-storage.md)
> - API 레퍼런스: [kis-api-reference.md](./kis-api-reference.md)
> - 설정 3계층: [project-plan.md §7](./project-plan.md)

---

## 목차

1. [Config 계층](#1-config-계층) — `settings.py` + `constants.py`
2. [Core 계층](#2-core-계층) — `database.py`, `cache.py`, `exceptions.py`, `dependencies.py`
3. [Model 계층](#3-model-계층) — `domain.py`, `dto.py`
4. [Repository 계층](#4-repository-계층) — KIS API 통신 + DB
5. [Service 계층](#5-service-계층) — 핵심 매매 로직
6. [Scheduler 계층](#6-scheduler-계층) — APScheduler Job
7. [Router 계층](#7-router-계층) — FastAPI 엔드포인트
8. [Main (앱 진입점)](#8-main-앱-진입점) — `main.py`, 엔트리포인트
9. [로깅 설정](#9-로깅-설정)
10. [구현 순서 (Phase별)](#10-구현-순서)

---

## 1. Config 계층

> 설정은 3계층으로 분리한다. 상세 기준: `project-plan.md` §7 참조.
> - **`.env`**: 비밀키 + 관심종목 (6개, 필수)
> - **Settings**: 전략 파라미터 (18개, 기본값 있음, .env로 오버라이드 가능)
> - **Constants**: 업계 표준·제도·시스템 상수 (18개, 코드에 직접 정의)

### 1.1 `app/config/settings.py`

```python
class Settings(BaseSettings):
    """
    .env에서 로드하는 설정.
    - 기본값 없는 필드 = .env 필수
    - 기본값 있는 필드 = .env 선택 (오버라이드용)
    """

    # ── Tier 1: .env 필수 (비밀키·환경) ──
    kis_app_key: str
    kis_app_secret: str
    kis_cano: str
    kis_acnt_prdt_cd: str
    kis_base_url: str
    watch_list: str                          # "005930,000660,035720"

    # ── Tier 2: 기본값 있음 (.env 오버라이드 가능) ──
    kis_is_paper_trading: bool = True

    # 스케줄링
    trading_interval_minutes: int = 5

    # 신호 확인
    signal_lookback_days: int = 14
    signal_confirm_days: int = 3
    sell_confirm_days: int = 2

    # 보조지표
    volume_confirm_ratio: float = 1.5
    rsi_overbought: int = 70
    rsi_oversold: int = 30

    # 리스크 관리 (수익률 % 단위)
    max_investment_ratio: float = 0.1
    max_holding_count: int = 5
    max_daily_buy_count: int = 3
    max_holding_days: int = 2        # 단타
    stop_loss_rate: float = -2.0    # 단타
    take_profit_rate: float = 2.0   # 단타
    trailing_stop_activate: float = 1.5  # 단타
    trailing_stop_rate: float = 0.8  # 단타
    rebuy_cooldown_hours: int = 24

    # 시장 필터
    enable_market_filter: bool = True

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def watch_list_codes(self) -> list[str]:
        return [c.strip() for c in self.watch_list.split(",") if c.strip()]
```

### 1.2 `app/config/constants.py`

```python
"""
업계 표준·정부 제도·시스템 상수.
거의 바뀌지 않으므로 코드에 직접 정의한다.
변경 시 코드 수정 후 재배포.
"""

# ── 이동평균선 (업계 표준) ──
MA_SHORT_PERIOD: int = 5
MA_LONG_PERIOD: int = 20
RSI_PERIOD: int = 14

# ── 매매 비용 (증권사·정부 제도) ──
BUY_FEE_RATE: float = 0.00015          # 매수 수수료 (0.015%)
SELL_FEE_RATE: float = 0.00015         # 매도 수수료 (0.015%)
SELL_TAX_RATE: float = 0.0018          # 증권거래세 (0.18%)
SLIPPAGE_RATE: float = 0.0015          # 슬리피지 추정 (왕복 0.15%)

# ── 종목 품질 필터 ──
MIN_STOCK_PRICE: int = 5_000            # 최소 주가 (원)
MIN_TRADING_VALUE: int = 1_000_000_000  # 최소 거래대금 (10억)
MIN_TRADING_VOLUME: int = 50_000        # 최소 거래량 (주)
MIN_MARKET_CAP: int = 500_000_000_000   # 최소 시총 (5000억)

# ── 안전장치 ──
ABNORMAL_CHANGE_RATE: float = 30.0      # 비정상 변동률 기준 (%)
MAX_API_RETRY: int = 3                  # API 재시도 횟수
API_RETRY_DELAY_SECONDS: int = 2        # 재시도 대기 (초)
MAX_CONSECUTIVE_FAILURES: int = 5       # 연속실패 시 스캔 SKIP
RATE_LIMIT_PER_SECOND: int = 2          # 초당 API 호출 제한 (모의투자 서버 기준, 실전 시 10으로 상향)
TRADING_CUTOFF_MINUTES: int = 10        # 장 마감 N분 전 매수 금지

# ── KRX 휴장일 (매년 갱신) ──
# ※ 대체공휴일, 임시공휴일은 KRX 공식 발표 후 갱신
KRX_HOLIDAYS: set[str] = {
    "20260101", "20260216", "20260217", "20260218",
    "20260302", "20260501", "20260505", "20260525",
    "20260817", "20260924", "20260925", "20260928",
    "20261005", "20261009", "20261225", "20261231",
}
```

**핵심 포인트:**
- `Settings`는 `BaseSettings` 상속 → `.env` 자동 로드, 기본값 없는 필드는 `.env` 필수
- `constants.py`는 순수 Python 모듈 → `from app.config.constants import MA_SHORT_PERIOD` 형태로 import
- `.env` 파일은 **6줄만** 작성하면 프로그램이 동작
- 전략 파라미터 조정이 필요하면 `.env`에 해당 키를 추가하면 됨 (코드 수정 불필요)

---

## 2. Core 계층

### 2.1 `app/core/database.py` — SQLite 연결 관리

```python
class Database:
    def __init__(self, db_path: str = "data/trademachine.db"):
        self._db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """앱 시작 시 호출. data/ 디렉토리 생성 + DB 연결 + 테이블 생성"""
        # 1) data/ 디렉토리 없으면 생성
        # 2) aiosqlite.connect(self._db_path) → self._connection
        # 3) WAL 모드 활성화 (성능)
        # 4) _create_tables() 호출

    async def disconnect(self) -> None:
        """앱 종료 시 호출"""
        # self._connection.close()

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """INSERT/UPDATE/DELETE 실행 + commit"""

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """SELECT 결과를 dict 리스트로 반환"""

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """SELECT 1건 반환"""

    async def _create_tables(self) -> None:
        """IF NOT EXISTS로 4개 테이블 생성"""
        # CREATE TABLE IF NOT EXISTS orders (...)
        # CREATE TABLE IF NOT EXISTS daily_reports (...)
        # CREATE TABLE IF NOT EXISTS balance_snapshots (...)
        # CREATE TABLE IF NOT EXISTS scan_logs (...)
```

**핵심 포인트:**
- 단일 `Database` 인스턴스를 앱 전체에서 공유 (DI)
- `row_factory = aiosqlite.Row` 설정으로 dict 접근 가능
- WAL 모드로 읽기/쓰기 동시 수행 가능

---

### 2.2 `app/core/cache.py` — 메모리 TTL 캐시

```python
class TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expire_time)

    def get(self, key: str) -> Any | None:
        """캐시에서 값 조회. 만료됐으면 None 반환 + 삭제"""
        if key in self._store:
            value, expire_time = self._store[key]
            if time.time() < expire_time:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """캐시에 저장. ttl_seconds 후 만료"""
        self._store[key] = (value, time.time() + ttl_seconds)

    def invalidate(self, key: str) -> None:
        """특정 키 삭제"""
        self._store.pop(key, None)

    def clear(self) -> None:
        """전체 캐시 초기화"""
        self._store.clear()
```

**사용 패턴:**

```python
# MarketDataRepository에서 사용 예시
cache_key = f"price:{stock_code}"
cached = self._cache.get(cache_key)
if cached:
    return cached            # 캐시 HIT → API 호출 안 함

result = await self._call_api(...)  # 캐시 MISS → API 호출
self._cache.set(cache_key, result, ttl_seconds=5.0)
return result
```

| 캐시 키 패턴 | TTL | 용도 |
|-------------|-----|------|
| `price:{종목코드}` | 5초 | 현재가 시세 |
| `daily:{종목코드}` | 300초 (5분) | 일봉 데이터 + MA 계산 결과 |

---

### 2.3 `app/core/exceptions.py` — 커스텀 예외

```python
class KisApiError(Exception):
    """KIS API 호출 실패 (rt_cd != "0")"""
    def __init__(self, msg_cd: str, msg: str):
        self.msg_cd = msg_cd
        super().__init__(f"[{msg_cd}] {msg}")

class TokenExpiredError(KisApiError):
    """토큰 만료 (EGW00123)"""

class RateLimitError(KisApiError):
    """호출 제한 초과 (HTTP 429)"""
```

---

### 2.4 `app/core/rate_limiter.py` — API 호출 속도 제한

```python
class RateLimiter:
    """초당 최대 N건으로 API 호출을 제한"""
    def __init__(self, max_per_second: int = 10):
        self._max = max_per_second
        self._timestamps: list[float] = []

    async def acquire(self) -> None:
        """호출 전 대기. 초당 제한 초과 시 sleep."""
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 1.0]
        if len(self._timestamps) >= self._max:
            wait = 1.0 - (now - self._timestamps[0])
            if wait > 0:
                await asyncio.sleep(wait)
        self._timestamps.append(time.time())
```

**사용 패턴:** 모든 KIS API 호출 직전에 `await self._rate_limiter.acquire()` 호출.

---

### 2.5 `app/core/dependencies.py` — DI 컨테이너

```python
from app.config import constants as C

# 전역 인스턴스 (lifespan에서 초기화)
_settings: Settings | None = None
_client: httpx.AsyncClient | None = None
_database: Database | None = None
_cache: TTLCache | None = None
_rate_limiter: RateLimiter | None = None

# Repository 인스턴스
_auth_repo: KisAuthRepository | None = None
_market_repo: MarketDataRepository | None = None
_order_repo: OrderRepository | None = None
_order_log_repo: OrderLogRepository | None = None
_report_repo: ReportRepository | None = None

# Service 인스턴스
_trading_service: TradingService | None = None


async def init_dependencies() -> None:
    """앱 시작 시 호출 — 모든 의존성 초기화"""
    global _settings, _client, _database, _cache, _rate_limiter
    global _auth_repo, _market_repo, _order_repo
    global _order_log_repo, _report_repo, _trading_service

    setup_logging()
    _settings = Settings()
    _database = Database()
    await _database.connect()
    _cache = TTLCache()
    _rate_limiter = RateLimiter(max_per_second=C.RATE_LIMIT_PER_SECOND)
    _client = httpx.AsyncClient(
        base_url=_settings.kis_base_url,
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
    )

    # Repository 생성 (하위 → 상위 순서)
    _auth_repo = KisAuthRepository(_client, _settings)
    _market_repo = MarketDataRepository(_client, _settings, _auth_repo, _cache, _rate_limiter)
    _order_repo = OrderRepository(_client, _settings, _auth_repo, _rate_limiter)
    _order_log_repo = OrderLogRepository(_database)
    _report_repo = ReportRepository(_database)

    # Service 생성 (Repository 주입)
    _trading_service = TradingService(
        auth_repo=_auth_repo,
        market_repo=_market_repo,
        order_repo=_order_repo,
        order_log_repo=_order_log_repo,
        report_repo=_report_repo,
        settings=_settings,
    )


async def close_dependencies() -> None:
    """앱 종료 시 호출 — 리소스 정리"""
    if _client:
        await _client.aclose()
    if _database:
        await _database.disconnect()


# FastAPI Depends용 getter
def get_settings() -> Settings:
    return _settings

def get_trading_service() -> TradingService:
    return _trading_service

def get_market_repo() -> MarketDataRepository:
    return _market_repo

def get_order_repo() -> OrderRepository:
    return _order_repo
```

**의존성 그래프:**

```
Settings ──┐
httpx ─────┤
RateLimiter┤
           ▼
     KisAuthRepository ──────────────────────┐
           │                                  │
     ┌─────┴──────┐                           │
     ▼             ▼                           ▼
MarketDataRepo  OrderRepository         TradingService
     │             │                    ↑  ↑  ↑
     └──────┬──────┘                    │  │  │
            └───────────────────────────┘  │  │
                   OrderLogRepo ───────────┘  │
                   ReportRepo ────────────────┘
```

---

## 3. Model 계층

### 3.1 `app/model/domain.py` — 도메인 모델

```python
from dataclasses import dataclass
from enum import Enum

class OrderType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderReason(str, Enum):
    GOLDEN_CROSS = "GOLDEN_CROSS"
    DEAD_CROSS = "DEAD_CROSS"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    MAX_HOLDING = "MAX_HOLDING"
    UPTREND_ENTRY = "UPTREND_ENTRY"

@dataclass
class Position:
    """보유 종목 (KIS 잔고 조회 결과)"""
    stock_code: str
    quantity: int
    avg_price: float
    profit_rate: float         # % 단위 (KIS API evlu_pfls_rt 그대로)
    current_price: int

@dataclass
class StockPrice:
    """현재가 시세 (KIS inquire-price 결과)"""
    stock_code: str
    current_price: int
    upper_limit: int           # 상한가
    lower_limit: int           # 하한가
    change_rate: float         # 전일대비율 (%)
    volume: int                # 누적 거래량
    trading_value: int         # 누적 거래대금
    market_cap: int            # 시가총액 (HTS 시가총액)
    is_stopped: bool           # 거래 정지
    is_managed: bool           # 관리종목
    is_caution: bool           # 투자유의
    is_clearing: bool          # 정리매매

@dataclass
class DailyCandle:
    """일봉 데이터 1건"""
    date: str                  # YYYYMMDD
    close: int                 # 종가
    open: int                  # 시가
    high: int                  # 고가
    low: int                   # 저가
    volume: int                # 거래량

@dataclass
class MaResult:
    """이동평균선 계산 결과"""
    ma_short: list[float]      # MA(5) 일별 값 (최신순)
    ma_long: list[float]       # MA(20) 일별 값 (최신순)
    candles: list[DailyCandle] # 원본 일봉 데이터

@dataclass
class OrderResult:
    """주문 실행 결과"""
    success: bool
    order_no: str | None       # KIS 주문번호 (ODNO)
    error_message: str | None

@dataclass
class ScanResult:
    """1회 스캔 실행 결과 (로깅용)"""
    scan_time: str
    holding_count: int
    sell_count: int
    buy_count: int
    skip_count: int
    error_count: int
    api_call_count: int
    elapsed_ms: int
    note: str | None = None
```

### 3.2 `app/model/dto.py` — 요청/응답 DTO

```python
from pydantic import BaseModel

class OrderRequest(BaseModel):
    stock_code: str
    order_type: str            # BUY / SELL
    quantity: int
    price: int | None = None   # 시장가면 None

class OrderResponse(BaseModel):
    success: bool
    order_no: str | None
    message: str

class BalanceResponse(BaseModel):
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: int
    profit_rate: float

class StockPriceResponse(BaseModel):
    stock_code: str
    current_price: int
    change_rate: float
    volume: int
```

---

## 4. Repository 계층

### 4.1 `app/repository/kis_auth_repository.py`

```python
class KisAuthRepository:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self._client = client
        self._settings = settings
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def get_token(self) -> str:
        """유효한 Access Token 반환. 만료 임박 시 자동 갱신"""
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at:
                return self._access_token
        await self._issue_token()
        return self._access_token

    async def _issue_token(self) -> None:
        """POST /oauth2/tokenP → 토큰 발급 + 메모리 캐싱"""
        # Body: grant_type, appkey, appsecret
        # 응답: access_token, expires_in
        # 만료시간 = now + expires_in - 3600 (1시간 여유)
        # 실패 시: 30초 후 재시도 (최대 3회)

    async def get_hashkey(self, body: dict) -> str:
        """POST /uapi/hashkey → Body 해시값 발급"""
        # Headers: appkey, appsecret (토큰 불필요)
        # Body: 주문 Body와 동일
        # 응답: HASH

    async def get_common_headers(self, tr_id: str) -> dict[str, str]:
        """모든 API 호출용 공통 헤더 생성. 토큰 만료 시 자동 갱신."""
        token = await self.get_token()
        return {
            "authorization": f"Bearer {token}",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
            "custtype": "P",
        }

    def get_tr_id(self, real_tr_id: str) -> str:
        """실전/모의 TR ID 자동 분기"""
        # TTTC → VTTC (모의), FHKST → FHKST (동일)
        if self._settings.kis_is_paper_trading and real_tr_id.startswith("TTTC"):
            return real_tr_id.replace("TTTC", "VTTC")
        return real_tr_id
```

**핵심 로직:**
- `get_token()`은 호출 시마다 만료 확인 → 캐싱된 토큰 반환 or 재발급
- `get_tr_id()`로 모의/실전 분기를 한 곳에서 처리

---

### 4.2 `app/repository/market_data_repository.py`

```python
class MarketDataRepository:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        auth_repo: KisAuthRepository,
        cache: TTLCache,
        rate_limiter: RateLimiter,
    ):
        self._client = client
        self._settings = settings
        self._auth = auth_repo
        self._cache = cache
        self._rate_limiter = rate_limiter
        self._api_call_count = 0    # 스캔 단위 리셋

    def reset_api_count(self) -> None:
        self._api_call_count = 0

    @property
    def api_call_count(self) -> int:
        return self._api_call_count

    async def get_current_price(
        self, stock_code: str, market_code: str = "J"
    ) -> StockPrice:
        """현재가 시세 조회 (캐시 5초). market_code: "J"=주식, "U"=업종지수"""
        cache_key = f"price:{stock_code}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        tr_id = "FHKST01010100"
        headers = await self._auth.get_common_headers(tr_id)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": stock_code,
        }

        response = await self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers, params=params,
        )

        output = response.get("output")
        if not output:
            raise KisApiError("NO_OUTPUT", "응답에 output 필드 없음")

        # safe_int, safe_float는 app/core/utils.py에서 import하여 사용
        # safe_int: val.replace(",", "") 쉼표 제거 후 int 변환
        # safe_float: val.replace(",", "") 쉼표 제거 후 float 변환
        # 예외 처리: ValueError, TypeError, AttributeError → default 반환
        from app.core.utils import safe_int, safe_float

        result = StockPrice(
            stock_code=stock_code,
            current_price=safe_int(output.get("stck_prpr")),
            upper_limit=safe_int(output.get("stck_mxpr")),
            lower_limit=safe_int(output.get("stck_llam")),
            change_rate=float(output.get("prdy_ctrt", "0")),
            volume=safe_int(output.get("acml_vol")),
            trading_value=safe_int(output.get("acml_tr_pbmn")),
            market_cap=safe_int(output.get("hts_avls")),
            is_stopped=output.get("temp_stop_yn") == "Y",
            is_managed=output.get("mang_issu_cls_code", "00") != "00",
            is_caution=output.get("invt_caful_yn") == "Y",
            is_clearing=output.get("sltr_yn") == "Y",
        )

        self._cache.set(cache_key, result, ttl_seconds=5.0)
        return result

    async def get_index_price(self, index_code: str = "0001") -> StockPrice:
        """업종지수(KOSPI 등) 현재가 조회 — inquire-index-price API 사용.
        TR_ID: FHPUP02100000, FID_COND_MRKT_DIV_CODE="U"
        시장 필터에서 KOSPI MA(20) 비교 시 사용."""
        ...

    async def get_daily_chart(
        self, stock_code: str, days: int = 60
    ) -> list[DailyCandle]:
        """기간별 시세(일봉) 조회 (캐시 5분)"""
        cache_key = f"daily:{stock_code}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        tr_id = "FHKST03010100"
        headers = await self._auth.get_common_headers(tr_id)
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 15)).strftime("%Y%m%d")
        # +15일 여유: 주말·공휴일 감안하여 영업일 기준 충분한 데이터 확보

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",   # 수정주가
        }

        response = await self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers, params=params,
        )

        candles = []
        for item in response.get("output2", []):
            close_price = int(item["stck_clpr"])
            if close_price <= 0:
                continue
            candles.append(DailyCandle(
                date=item["stck_bsop_date"],
                close=close_price,
                open=int(item["stck_oprc"]),
                high=int(item["stck_hgpr"]),
                low=int(item["stck_lwpr"]),
                volume=int(item["acml_vol"]),
            ))

        candles.sort(key=lambda c: c.date, reverse=True)  # 최신순 명시적 정렬

        self._cache.set(cache_key, candles, ttl_seconds=300.0)
        return candles

    async def _request_get(
        self, path: str, headers: dict, params: dict
    ) -> dict:
        """GET 요청 공통 래퍼 (재시도, 토큰 갱신, 에러 처리)"""
        for attempt in range(C.MAX_API_RETRY):
            try:
                await self._rate_limiter.acquire()
                self._api_call_count += 1
                resp = await self._client.get(path, headers=headers, params=params)

                if resp.status_code == 429:
                    wait = C.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    await asyncio.sleep(wait)
                    continue

                data = resp.json()

                if data.get("rt_cd") == "1":
                    msg_cd = data.get("msg_cd", "")
                    if msg_cd == "EGW00123":
                        headers = await self._auth.get_common_headers(
                            headers["tr_id"]
                        )
                        continue
                    raise KisApiError(msg_cd, data.get("msg1", ""))

                return data

            except httpx.TimeoutException:
                if attempt == C.MAX_API_RETRY - 1:
                    raise
                await asyncio.sleep(C.API_RETRY_DELAY_SECONDS)

        raise KisApiError("MAX_RETRY", "최대 재시도 횟수 초과")
```

---

### 4.3 `app/repository/order_repository.py`

```python
class OrderRepository:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        auth_repo: KisAuthRepository,
        rate_limiter: RateLimiter,
    ):
        self._client = client
        self._settings = settings
        self._auth = auth_repo
        self._rate_limiter = rate_limiter

    async def get_balance(self) -> list[Position] | None:
        """잔고 조회 → Position 리스트 반환. 실패 시 None."""
        tr_id = self._auth.get_tr_id("TTTC8434R")
        headers = await self._auth.get_common_headers(tr_id)
        params = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        # GET inquire-balance → output1 배열 파싱
        # quantity > 0 인 항목만 Position으로 변환하여 반환

    async def get_unfilled_orders(self) -> list[dict]:
        """미체결 주문 조회"""
        tr_id = self._auth.get_tr_id("TTTC8001R")
        # GET inquire-daily-ccld, CCLD_DVSN="02"
        # 반환: [{"stock_code": ..., "order_no": ..., "side": "BUY"/"SELL"}, ...]

    async def cancel_order(self, order_no: str, quantity: int) -> bool:
        """미체결 주문 취소"""
        tr_id = self._auth.get_tr_id("TTTC0803U")
        # POST order-rvsecncl + hashkey
        # 반환: 성공 여부

    async def get_available_cash(self, stock_code: str) -> int:
        """매수 가능 금액 조회"""
        tr_id = self._auth.get_tr_id("TTTC8908R")
        # GET inquire-psbl-order
        # 반환: ord_psbl_cash (int)

    async def execute_order(
        self,
        stock_code: str,
        order_type: OrderType,
        quantity: int,
        price: int = 0,
    ) -> OrderResult:
        """매수/매도 주문 실행"""
        # 1) TR ID 결정: BUY → TTTC0802U, SELL → TTTC0801U
        # 2) Body 구성
        body = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": "01",              # 시장가
            "ORD_QTY": str(quantity),       # 문자열!
            "ORD_UNPR": str(price),         # 시장가면 "0"
        }
        # 3) Hashkey 발급
        hashkey = await self._auth.get_hashkey(body)
        # 4) POST order-cash + headers에 hashkey 추가
        # 5) 응답 파싱 → OrderResult 반환
```

---

### 4.4 `app/repository/order_log_repository.py`

```python
class OrderLogRepository:
    def __init__(self, database: Database):
        self._db = database

    async def save_order(
        self,
        stock_code: str,
        order_type: OrderType,
        order_reason: OrderReason,
        quantity: int,
        price: int,
        result: OrderResult,
        scan_time: str,
    ) -> None:
        """orders 테이블에 INSERT"""
        await self._db.execute(
            """INSERT INTO orders
               (created_at, stock_code, order_type, order_reason, order_method,
                quantity, price, kis_order_no, success, error_message, scan_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), stock_code, order_type.value,
             order_reason.value, "MARKET", quantity, price,
             result.order_no, 1 if result.success else 0,
             result.error_message, scan_time),
        )

    async def get_last_sell_time(self, stock_code: str) -> datetime | None:
        """해당 종목의 가장 최근 SELL 성공 시각 반환 (재매수 쿨다운용)"""
        row = await self._db.fetch_one(
            """SELECT created_at FROM orders
               WHERE stock_code = ? AND order_type = 'SELL' AND success = 1
               ORDER BY created_at DESC LIMIT 1""",
            (stock_code,),
        )
        if row:
            return datetime.fromisoformat(row["created_at"])
        return None

    async def get_first_buy_date(self, stock_code: str) -> str | None:
        """해당 종목의 현재 보유 시작일 반환 (보유 기간 체크용).
        가장 최근 매수일이 아닌, 마지막 매도 이후 첫 매수일을 반환한다.
        서브쿼리로 마지막 매도일을 구한 뒤, 그 이후 ASC LIMIT 1로 첫 매수일을 조회."""
        row = await self._db.fetch_one(
            """SELECT created_at FROM orders
               WHERE stock_code = ? AND order_type = 'BUY' AND success = 1
                 AND created_at > COALESCE(
                   (SELECT MAX(created_at) FROM orders
                    WHERE stock_code = ? AND order_type = 'SELL' AND success = 1),
                   '1970-01-01')
               ORDER BY created_at ASC LIMIT 1""",
            (stock_code, stock_code),
        )
        if row:
            return row["created_at"][:10]  # "2026-03-11"
        return None

    async def get_today_counts(self, today: str) -> dict:
        """오늘의 매수/매도/실패 건수 집계 (일일 리포트용)"""
        row = await self._db.fetch_one(
            """SELECT
                 COALESCE(SUM(CASE WHEN order_type='BUY' AND success=1 THEN 1 ELSE 0 END), 0) as buy_count,
                 COALESCE(SUM(CASE WHEN order_type='SELL' AND success=1 THEN 1 ELSE 0 END), 0) as sell_count,
                 COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END), 0) as fail_count
               FROM orders WHERE created_at LIKE ?""",
            (f"{today}%",),
        )
        return dict(row) if row else {"buy_count": 0, "sell_count": 0, "fail_count": 0}
```

### 4.5 `app/repository/report_repository.py`

```python
class ReportRepository:
    def __init__(self, database: Database):
        self._db = database

    async def save_scan_log(self, result: ScanResult) -> None:
        """scan_logs 테이블에 INSERT"""

    async def save_daily_report(
        self, report_date: str, buy_count: int, sell_count: int,
        unfilled: int, holding_count: int, eval_amount: int,
        eval_profit: int, profit_rate: float,
    ) -> None:
        """daily_reports 테이블에 INSERT"""

    async def save_balance_snapshot(
        self, snapshot_date: str, positions: list[Position],
    ) -> None:
        """balance_snapshots 테이블에 종목별 INSERT"""

    async def cleanup_old_scan_logs(self, days: int = 90) -> int:
        """N일 이상 된 scan_logs 삭제, 삭제 건수 반환"""
```

---

## 5. Service 계층 — 핵심 매매 로직

### `app/service/trading_service.py`

이 파일이 프로그램의 **두뇌**다. 모든 매매 판단이 여기서 이루어진다.

```python
from app.config import constants as C   # 코드 상수 (MA, 비용, 품질필터, 안전장치)

class TradingService:
    def __init__(
        self,
        auth_repo: KisAuthRepository,
        market_repo: MarketDataRepository,
        order_repo: OrderRepository,
        order_log_repo: OrderLogRepository,
        report_repo: ReportRepository,
        settings: Settings,
    ):
        self._auth = auth_repo
        self._market = market_repo
        self._order = order_repo
        self._order_log = order_log_repo
        self._report = report_repo
        self._settings = settings
        self._consecutive_failures = 0
        self._daily_buy_count = 0
        self._scan_running = False
        self._highest_prices: dict[str, int] = {}  # 종목코드 → 보유 중 최고가
        self._trailing_activated: set[str] = set()  # 트레일링 스탑 활성화된 종목
```

#### 5.0 `recover_state()` — 서버 재시작 시 인메모리 상태 복구

```python
    async def recover_state(self) -> None:
        """서버 재시작 시 DB + 현재 잔고에서 인메모리 상태 복구.
        init_dependencies()에서 TradingService 생성 직후 호출."""
        # 1) DB에서 금일 매수 건수 복구
        today = datetime.now().strftime("%Y-%m-%d")
        counts = await self._order_log.get_today_counts(today)
        self._daily_buy_count = counts["buy_count"]
        # 2) 잔고에서 트레일링 스탑 상태 복구
        positions = await self._order.get_balance() or []
        for pos in positions:
            self._highest_prices[pos.stock_code] = pos.current_price
            if pos.profit_rate >= self._settings.trailing_stop_activate:
                self._trailing_activated.add(pos.stock_code)
```

#### 5.1 `run_scan()` — 스캔 사이클 메인 (F2)

```python
    async def run_scan(self) -> None:
        """매 5분마다 스케줄러가 호출하는 메인 메서드"""
        # 동시 실행 방지 (APScheduler max_instances=1과 이중 보호)
        if self._scan_running:
            return
        self._scan_running = True

        # import time as time_mod (모듈 상단), from datetime import datetime, time as dt_time
        scan_time = datetime.now().isoformat()
        start = time_mod.time()
        self._market.reset_api_count()

        positions: list[Position] = []
        sell_count = buy_count = skip_count = error_count = 0

        try:
            # [진입 조건 체크]
            now = datetime.now()
            if now.weekday() >= 5:
                return  # 주말
            if now.strftime("%Y%m%d") in C.KRX_HOLIDAYS:
                return  # 휴장일

            # 연속 실패 체크
            if self._consecutive_failures >= 10:
                logger.critical(f"연속 {self._consecutive_failures}회 실패 — 봇 정지")
                self._scheduler_running = False
                return
            if self._consecutive_failures >= C.MAX_CONSECUTIVE_FAILURES:
                logger.warning(f"연속 {self._consecutive_failures}회 실패, 스캔 SKIP")
                return

            current_time = now.time()
            if current_time < dt_time(9, 5):
                return
            if current_time > dt_time(15, 25):
                return  # 15:25 이후는 run_post_market에 양보
            buy_allowed = current_time <= dt_time(15, 20)

            # [F2-1] 잔고 동기화
            positions = await self._order.get_balance()
            if positions is None:
                self._consecutive_failures += 1
                return

            # [F2-2] 미체결 주문 정리
            unfilled_codes = await self._cleanup_unfilled_orders()

            # [F2-3] 보유 종목 매도 판단
            for pos in positions:
                try:
                    result = await self._evaluate_sell(pos, unfilled_codes)
                    if result == "SOLD":
                        sell_count += 1
                    elif result == "SKIP":
                        skip_count += 1
                except Exception:
                    error_count += 1

            # [F2-4] 관심 종목 매수 판단
            if buy_allowed:
                # 시장 필터를 여기서 1회만 체크 (종목 루프 밖)
                market_ok = True
                if self._settings.enable_market_filter:
                    kospi_data = await self._market.get_index_price("0001")
                    kospi_candles = await self._market.get_daily_chart("0001", days=25)
                    if len(kospi_candles) >= 20:
                        kospi_ma20 = sum(c.close for c in kospi_candles[:20]) / 20
                        if kospi_data.current_price < kospi_ma20:
                            market_ok = False

                holding_codes = {p.stock_code for p in positions}
                current_holding = len(positions)
                for code in self._settings.watch_list_codes:
                    try:
                        result = await self._evaluate_buy(
                            code, current_holding, holding_codes, unfilled_codes, market_ok,
                        )
                        if result == "BOUGHT":
                            buy_count += 1
                            self._daily_buy_count += 1
                            current_holding += 1
                            holding_codes.add(code)
                        elif result == "SKIP":
                            skip_count += 1
                    except Exception:
                        error_count += 1

            self._consecutive_failures = 0

        except Exception:
            error_count += 1
            self._consecutive_failures += 1

        finally:
            self._scan_running = False  # finally에서 반드시 해제

        # [F2-5] 스캔 결과 로깅 + DB 저장
        elapsed = int((time_mod.time() - start) * 1000)
        scan_result = ScanResult(
            scan_time=scan_time,
            holding_count=len(positions),
            sell_count=sell_count,
            buy_count=buy_count,
            skip_count=skip_count,
            error_count=error_count,
            api_call_count=self._market.api_call_count,
            elapsed_ms=elapsed,
        )
        await self._report.save_scan_log(scan_result)
```

#### 5.2 `_evaluate_sell()` — 매도 판단 로직 (F2-3)

```python
    async def _evaluate_sell(self, pos: Position, unfilled_codes: set[str]) -> str:
        """보유 종목 1개에 대한 매도 판단. "SOLD" / "SKIP" / "HOLD" 반환"""

        # STEP 1: 현재가 조회
        price_data = await self._market.get_current_price(pos.stock_code)

        # STEP 2: 데이터 유효성
        if price_data.current_price <= 0:
            return "SKIP"
        if price_data.is_stopped:
            return "SKIP"
        if abs(price_data.change_rate) > C.ABNORMAL_CHANGE_RATE:
            return "SKIP"

        # STEP 3: 미체결 매도 주문 중복 체크
        if pos.stock_code in unfilled_codes:
            return "SKIP"

        # STEP 3.5: 수익률 직접 재계산 (KIS API 제공값 대신 직접 계산)
        profit_rate = (price_data.current_price - pos.avg_price) / pos.avg_price * 100

        # STEP 4: 손절 (최우선, P0) — % 단위 직접 비교
        if profit_rate <= self._settings.stop_loss_rate:
            await self._execute_sell(pos, OrderReason.STOP_LOSS, price_data.current_price)
            return "SOLD"

        # STEP 5: 익절 (P1)
        if profit_rate >= self._settings.take_profit_rate:
            await self._execute_sell(pos, OrderReason.TAKE_PROFIT, price_data.current_price)
            return "SOLD"

        # STEP 5.5: 트레일링 스탑 (P2)
        code = pos.stock_code
        cur = price_data.current_price
        self._highest_prices[code] = max(self._highest_prices.get(code, 0), cur)

        if profit_rate >= self._settings.trailing_stop_activate:
            self._trailing_activated.add(code)

        if code in self._trailing_activated:
            highest = self._highest_prices[code]
            threshold = highest * (1 - self._settings.trailing_stop_rate / 100)
            if cur <= threshold:
                await self._execute_sell(pos, OrderReason.TRAILING_STOP, cur)
                self._highest_prices.pop(code, None)
                self._trailing_activated.discard(code)
                return "SOLD"

        # STEP 5.7: 보유 기간 초과 (P3) — 영업일 기준
        buy_date = await self._order_log.get_first_buy_date(code)
        if buy_date:
            biz_days = self._count_business_days(
                datetime.strptime(buy_date, "%Y-%m-%d").date(),
                datetime.now().date(),
            )
            if biz_days > self._settings.max_holding_days:
                await self._execute_sell(pos, OrderReason.MAX_HOLDING, cur)
                self._cleanup_trailing(code)
                return "SOLD"

        # STEP 6: 데드크로스 (P4)
        if await self._check_dead_cross(pos.stock_code):
            await self._execute_sell(pos, OrderReason.DEAD_CROSS, cur)
            self._cleanup_trailing(code)
            return "SOLD"

        return "HOLD"
```

#### 5.3 `_evaluate_buy()` — 매수 판단 로직 (F2-4)

```python
    async def _evaluate_buy(
        self,
        stock_code: str,
        current_holding_count: int,
        holding_codes: set[str],
        unfilled_codes: set[str],
        market_ok: bool,
    ) -> str:
        """관심 종목 1개에 대한 매수 판단. "BOUGHT" / "SKIP" 반환.
        market_ok는 run_scan()에서 1회 계산한 시장 필터 결과."""

        # STEP 1: 중복/한도 체크 (API 호출 없이 빠르게 필터링)
        if stock_code in holding_codes:
            return "SKIP"
        if stock_code in unfilled_codes:
            return "SKIP"
        if current_holding_count >= self._settings.max_holding_count:
            return "SKIP"
        if self._daily_buy_count >= self._settings.max_daily_buy_count:
            return "SKIP"

        # STEP 2: 시장 상황 필터 (run_scan()에서 1회 체크한 결과)
        if not market_ok:
            return "SKIP"

        # STEP 3: 현재가 조회 + 안전성 필터
        price_data = await self._market.get_current_price(stock_code)
        if price_data.is_stopped or price_data.is_managed:
            return "SKIP"
        if price_data.is_caution or price_data.is_clearing:
            return "SKIP"
        if price_data.current_price <= 0:
            return "SKIP"
        if abs(price_data.change_rate) > C.ABNORMAL_CHANGE_RATE:
            return "SKIP"

        # STEP 4: 품질 필터
        if price_data.current_price < C.MIN_STOCK_PRICE:
            return "SKIP"
        if price_data.trading_value < C.MIN_TRADING_VALUE:
            return "SKIP"
        if price_data.volume < C.MIN_TRADING_VOLUME:
            return "SKIP"
        if price_data.market_cap < C.MIN_MARKET_CAP:
            return "SKIP"
        if price_data.current_price == price_data.upper_limit:
            return "SKIP"

        # STEP 5: 재매수 쿨다운
        last_sell = await self._order_log.get_last_sell_time(stock_code)
        if last_sell:
            hours_since = (datetime.now() - last_sell).total_seconds() / 3600
            if hours_since < self._settings.rebuy_cooldown_hours:
                return "SKIP"

        # STEP 6: 일봉 + MA 계산
        candles = await self._market.get_daily_chart(stock_code)
        if len(candles) < C.MA_LONG_PERIOD + self._settings.signal_lookback_days:
            return "SKIP"

        ma_result = self._calculate_ma(candles)

        # STEP 7: 골든크로스 판별 → 실패 시 상승추세 판별
        buy_reason = OrderReason.GOLDEN_CROSS
        is_golden = self._check_golden_cross(ma_result, price_data.current_price)

        if is_golden:
            # 거래량 확인 (교차일 거래량 >= 평균 × 1.5)
            cross_idx = self._find_cross_day_index(ma_result)
            if cross_idx is not None and not self._check_volume_confirmation(candles, cross_idx):
                return "SKIP"
        else:
            # 골든크로스 미충족 → 상승추세 진입(UPTREND_ENTRY) 판별
            if not self._check_existing_uptrend(ma_result, candles, price_data.current_price):
                return "SKIP"
            buy_reason = OrderReason.UPTREND_ENTRY

        # STEP 7.5: RSI 과열 필터
        rsi = self._calculate_rsi(candles)
        if rsi is not None:
            if rsi > self._settings.rsi_overbought:
                return "SKIP"
            if rsi < self._settings.rsi_oversold:
                return "SKIP"

        # STEP 8: 매수 수량 (수수료 포함 계산)
        available = await self._order.get_available_cash(stock_code)
        invest = int(available * self._settings.max_investment_ratio)
        price_with_fee = price_data.current_price * (1 + C.BUY_FEE_RATE)
        quantity = int(invest / price_with_fee)
        if quantity <= 0:
            return "SKIP"

        # STEP 9: 매수 실행
        result = await self._order.execute_order(
            stock_code, OrderType.BUY, quantity,
        )
        await self._order_log.save_order(
            stock_code, OrderType.BUY, buy_reason,
            quantity, price_data.current_price, result, datetime.now().isoformat(),
        )
        return "BOUGHT" if result.success else "SKIP"
```

#### 5.4 `_calculate_ma()` — 이동평균선 계산

```python
    def _calculate_ma(self, candles: list[DailyCandle]) -> MaResult:
        """일봉 데이터로 MA(5), MA(20) 일별 배열 계산"""
        closes = [c.close for c in candles]  # 최신순
        short = C.MA_SHORT_PERIOD
        long = C.MA_LONG_PERIOD

        ma_short = []
        ma_long = []

        for i in range(len(closes) - long + 1):
            if i + short <= len(closes):
                ma_short.append(sum(closes[i:i+short]) / short)
            if i + long <= len(closes):
                ma_long.append(sum(closes[i:i+long]) / long)

        return MaResult(ma_short=ma_short, ma_long=ma_long, candles=candles)
```

#### 5.5 `_check_golden_cross()` — 골든크로스 판별

```python
    def _check_golden_cross(self, ma: MaResult, current_price: int) -> bool:
        """4단계 골든크로스 확인"""
        if len(ma.ma_short) < self._settings.signal_lookback_days + 1:
            return False
        if len(ma.ma_long) < self._settings.signal_lookback_days + 1:
            return False

        # [8-a] 최근 N일 내 교차 발생?
        cross_day = -1
        for i in range(self._settings.signal_lookback_days):
            if (ma.ma_short[i+1] < ma.ma_long[i+1] and
                    ma.ma_short[i] >= ma.ma_long[i]):
                cross_day = i
                break
        if cross_day < 0:
            return False

        # [8-b] 교차 후 M일 연속 유지?
        if cross_day < self._settings.signal_confirm_days:
            return False  # 아직 확인 기간 미충족
        for j in range(cross_day):
            if ma.ma_short[j] <= ma.ma_long[j]:
                return False

        # [8-c] 현재가 > MA(20)?
        if current_price <= ma.ma_long[0]:
            return False

        # [8-d] MA(20) 상승 추세?
        if len(ma.ma_long) > 3 and ma.ma_long[0] <= ma.ma_long[3]:
            return False

        return True
```

#### 5.6 `_check_dead_cross()` — 데드크로스 판별

```python
    async def _check_dead_cross(self, stock_code: str) -> bool:
        """데드크로스 + 확인 기간 체크"""
        candles = await self._market.get_daily_chart(stock_code, days=30)
        if len(candles) < C.MA_LONG_PERIOD:
            return False

        ma = self._calculate_ma(candles)

        # 최근 N일 내 MA5가 MA20 위→아래 교차? (매도는 매수(14일)보다 짧게 5일)
        sell_lookback = 5
        cross_day = -1
        for i in range(sell_lookback):
            if (i + 1 < len(ma.ma_short) and i + 1 < len(ma.ma_long) and
                    ma.ma_short[i+1] > ma.ma_long[i+1] and
                    ma.ma_short[i] <= ma.ma_long[i]):
                cross_day = i
                break
        if cross_day < 0:
            return False

        # 교차 후 SELL_CONFIRM_DAYS(2일) 연속 유지?
        if cross_day < self._settings.sell_confirm_days:
            return False
        for j in range(cross_day):
            if ma.ma_short[j] >= ma.ma_long[j]:
                return False

        return True
```

#### 5.7 `_find_cross_day_index()` — 골든크로스 교차일 인덱스

```python
    def _find_cross_day_index(self, ma: MaResult) -> int | None:
        """최근 N일 내 골든크로스 교차일의 candle 인덱스 반환. 없으면 None."""
        for i in range(self._settings.signal_lookback_days):
            if (i + 1 < len(ma.ma_short) and i + 1 < len(ma.ma_long) and
                    ma.ma_short[i+1] < ma.ma_long[i+1] and
                    ma.ma_short[i] >= ma.ma_long[i]):
                return i
        return None
```

#### 5.8 `_calculate_rsi()` — RSI 계산

```python
    def _calculate_rsi(self, candles: list[DailyCandle]) -> float | None:
        """RSI(14) 계산. 데이터 부족 시 None 반환."""
        period = C.RSI_PERIOD
        if len(candles) < period + 1:
            return None

        gains, losses = [], []
        for i in range(period):
            diff = candles[i].close - candles[i + 1].close
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
```

#### 5.9 `_check_volume_confirmation()` — 거래량 확인

```python
    def _check_volume_confirmation(
        self, candles: list[DailyCandle], cross_day_index: int
    ) -> bool:
        """교차일의 거래량이 MA(20) 평균 거래량의 N배 이상인지 확인"""
        if cross_day_index >= len(candles):
            return False

        cross_volume = candles[cross_day_index].volume
        vol_period = min(20, len(candles) - 1)
        if vol_period < 5:
            return False

        avg_volume = sum(c.volume for c in candles[1:vol_period + 1]) / vol_period
        if avg_volume <= 0:
            return False

        return cross_volume >= avg_volume * self._settings.volume_confirm_ratio
```

#### 5.10 `_check_existing_uptrend()` — 상승추세 진입 판별

```python
    def _check_existing_uptrend(
        self, ma: MaResult, candles: list[DailyCandle], current_price: int
    ) -> bool:
        """골든크로스 lookback 밖이지만 이미 상승추세에 있는 종목 감지.
        조건: MA5>MA20 5일 연속, 현재가>MA20, MA20 상승, 괴리율 5% 이내,
        최근 5일 중 거래량이 20일 평균 이상인 날 2일 이상."""
        if len(ma.ma_short) < 5 or len(ma.ma_long) < 5:
            return False

        # [1] MA5 > MA20 최근 5일 연속 유지
        for i in range(5):
            if ma.ma_short[i] <= ma.ma_long[i]:
                return False

        # [2] 현재가 > MA20
        if current_price <= ma.ma_long[0]:
            return False

        # [3] MA20 상승 추세 (오늘 MA20 > 3일전 MA20)
        if len(ma.ma_long) > 3 and ma.ma_long[0] <= ma.ma_long[3]:
            return False

        # [4] MA5와 MA20의 괴리율 5% 이내
        if ma.ma_long[0] > 0:
            gap_rate = (ma.ma_short[0] - ma.ma_long[0]) / ma.ma_long[0] * 100
            if gap_rate > 5.0:
                return False

        # [5] 최근 5일 중 거래량이 20일 평균 이상인 날이 2일 이상
        if len(candles) < 20:
            return False
        avg_vol_20 = sum(c.volume for c in candles[:20]) / 20
        high_vol_days = sum(1 for c in candles[:5] if c.volume >= avg_vol_20)
        if high_vol_days < 2:
            return False

        return True
```

#### 5.11 `_cleanup_trailing()` — 트레일링 상태 정리 (기존 5.10)

```python
    def _cleanup_trailing(self, code: str) -> None:
        """매도 완료 후 트레일링 스탑 추적 데이터 정리"""
        self._highest_prices.pop(code, None)
        self._trailing_activated.discard(code)
```

#### 5.12 `_count_business_days()` — 영업일 계산

```python
    def _count_business_days(self, start: date, end: date) -> int:
        """start ~ end 사이의 영업일 수 (주말 + KRX 휴장일 제외)"""
        count = 0
        current = start
        while current < end:
            current += timedelta(days=1)
            if current.weekday() < 5 and current.strftime("%Y%m%d") not in C.KRX_HOLIDAYS:
                count += 1
        return count
```

#### 5.13 `_execute_sell()` — 매도 실행 헬퍼

```python
    async def _execute_sell(self, pos: Position, reason: OrderReason, price: int = 0) -> bool:
        """매도 주문 실행 + DB 기록. 손절 실패 시 1회 재시도.
        price: 현재가 (DB 기록용). 반환: 매도 성공 여부."""
        result = await self._order.execute_order(
            pos.stock_code, OrderType.SELL, pos.quantity,
        )

        if not result.success and reason == OrderReason.STOP_LOSS:
            await asyncio.sleep(1)
            result = await self._order.execute_order(
                pos.stock_code, OrderType.SELL, pos.quantity,
            )

        await self._order_log.save_order(
            pos.stock_code, OrderType.SELL, reason,
            pos.quantity, price, result, datetime.now().isoformat(),
        )
        return result.success
```

#### 5.14 `run_pre_market()` — 장 시작 전 준비 (F1)

```python
    async def run_pre_market(self) -> None:
        """08:50 스케줄러가 호출"""
        if datetime.now().strftime("%Y%m%d") in C.KRX_HOLIDAYS:
            return

        # F1-1: 토큰 발급
        await self._auth.get_token()

        # 일일 카운터 초기화
        self._daily_buy_count = 0

        # F1-3: 잔고 조회 + 트레일링 정보 동기화
        positions = await self._order.get_balance()
        holding_codes = {p.stock_code for p in positions} if positions else set()
        self._highest_prices = {
            code: price for code, price in self._highest_prices.items()
            if code in holding_codes
        }
        self._trailing_activated &= holding_codes

        # F1-4: 미체결 매수 전량 취소
        await self._cleanup_unfilled_orders()
```

#### 5.15 `run_post_market()` — 장 마감 처리 (F3)

```python
    async def run_post_market(self) -> None:
        """15:30 스케줄러가 호출"""
        if datetime.now().strftime("%Y%m%d") in C.KRX_HOLIDAYS:
            return

        today = datetime.now().strftime("%Y-%m-%d")

        # F3-1: 잔고 최종 조회
        positions = await self._order.get_balance()

        # F3-2: 미체결 정리
        await self._cleanup_unfilled_orders()

        # F3-3: DB 저장
        total_eval = sum(p.current_price * p.quantity for p in positions)
        total_cost = sum(p.avg_price * p.quantity for p in positions)
        total_profit = total_eval - total_cost
        rate = (total_profit / total_cost * 100) if total_cost > 0 else 0.0

        counts = await self._order_log.get_today_counts(today)

        await self._report.save_daily_report(
            report_date=today,
            buy_count=counts["buy_count"],
            sell_count=counts["sell_count"],
            unfilled=counts["fail_count"],
            holding_count=len(positions),
            eval_amount=total_eval,
            eval_profit=total_profit,
            profit_rate=rate,
        )

        await self._report.save_balance_snapshot(today, positions)

        # F3-4: 90일 이전 스캔 로그 정리
        await self._report.cleanup_old_scan_logs(days=90)
```

#### 5.16 `_cleanup_unfilled_orders()` — 미체결 정리

```python
    async def _cleanup_unfilled_orders(self) -> set[str]:
        """미체결 매수 주문 취소. 미체결 중인 종목코드 set 반환 (중복 주문 방지용)."""
        unfilled = await self._order.get_unfilled_orders()
        unfilled_codes: set[str] = set()

        for order in unfilled:
            if order["side"] == "BUY":
                await self._order.cancel_order(
                    order["order_no"], order["quantity"]
                )
                unfilled_codes.add(order["stock_code"])
            else:
                unfilled_codes.add(order["stock_code"])

        return unfilled_codes
```

---

## 6. Scheduler 계층

### `app/scheduler/trading_scheduler.py`

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class TradingScheduler:
    def __init__(self, trading_service: TradingService):
        self._service = trading_service
        self._scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    def start(self) -> None:
        """APScheduler Job 등록 + 시작"""
        # Job 1: 장 시작 전 준비 (08:50, 월~금)
        self._scheduler.add_job(
            self._service.run_pre_market,
            "cron", hour=8, minute=50, day_of_week="mon-fri",
            id="pre_market", replace_existing=True,
        )

        # Job 2: 스캔 사이클 (09:05~15:25, 매 5분, 월~금)
        # hour="9-14"는 09:xx~14:xx, 15:00~15:25는 별도 cron으로 분리
        self._scheduler.add_job(
            self._service.run_scan,
            "cron",
            day_of_week="mon-fri",
            hour="9-14",
            minute="*/5",
            id="scan_cycle_main", replace_existing=True,
            max_instances=1,
        )
        self._scheduler.add_job(
            self._service.run_scan,
            "cron",
            day_of_week="mon-fri",
            hour=15,
            minute="0,5,10,15,20,25",
            id="scan_cycle_closing", replace_existing=True,
            max_instances=1,
        )

        # Job 3: 장 마감 처리 (15:30, 월~금)
        self._scheduler.add_job(
            self._service.run_post_market,
            "cron", hour=15, minute=30, day_of_week="mon-fri",
            id="post_market", replace_existing=True,
        )

        self._scheduler.start()

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
```

**핵심 포인트:**
- `AsyncIOScheduler` 사용 (FastAPI의 이벤트 루프와 호환)
- `day_of_week="mon-fri"`로 주말 자동 제외
- 휴장일은 각 Job 내부(`run_scan` 등)에서 `krx_holiday_set` 체크로 2차 방어
- 스캔 Job은 `hour="9-14"` + `hour=15, minute="0~25"`로 분리하여 15:30 `run_post_market`과 충돌 방지

---

## 7. Router 계층

### 7.1 `app/router/market_router.py`

```python
router = APIRouter(prefix="/market", tags=["시세"])

@router.get("/price/{stock_code}")
async def get_price(
    stock_code: str,
    market_repo: MarketDataRepository = Depends(get_market_repo),
) -> StockPriceResponse:
    """수동 현재가 조회"""
    data = await market_repo.get_current_price(stock_code)
    return StockPriceResponse(...)

@router.get("/balance")
async def get_balance(
    order_repo: OrderRepository = Depends(get_order_repo),
) -> list[BalanceResponse]:
    """수동 잔고 조회"""
    positions = await order_repo.get_balance()
    return [BalanceResponse(...) for p in positions]
```

### 7.2 `app/router/trading_router.py`

```python
router = APIRouter(prefix="/trading", tags=["매매"])

@router.post("/scan")
async def trigger_scan(
    service: TradingService = Depends(get_trading_service),
) -> dict:
    """수동 스캔 트리거 (디버깅용)"""
    await service.run_scan()
    return {"status": "scan completed"}

@router.post("/order")
async def manual_order(
    req: OrderRequest,
    order_repo: OrderRepository = Depends(get_order_repo),
) -> OrderResponse:
    """수동 주문 실행"""
    result = await order_repo.execute_order(
        req.stock_code, OrderType(req.order_type), req.quantity, req.price or 0,
    )
    return OrderResponse(success=result.success, order_no=result.order_no, message="")
```

---

## 8. Main (앱 진입점)

### `app/main.py`

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 수명주기"""
    # ── 시작 ──
    await init_dependencies()
    scheduler = TradingScheduler(get_trading_service())
    scheduler.start()
    logger.info("앱 시작 완료")

    yield

    # ── 종료 ──
    scheduler.stop()
    await close_dependencies()
    logger.info("앱 정상 종료")


def create_app() -> FastAPI:
    app = FastAPI(title="TradeMachine", lifespan=lifespan)
    app.include_router(market_router)
    app.include_router(trading_router)
    return app

app = create_app()
```

### `main.py` (루트 엔트리포인트)

```python
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
```

---

## 9. 로깅 설정

### `app/core/logging_config.py`

```python
import logging
from logging.handlers import RotatingFileHandler

def setup_logging() -> None:
    """로깅 초기 설정 — init_dependencies()에서 호출"""
    Path("logs").mkdir(exist_ok=True)
    fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    formatter = logging.Formatter(fmt)

    # app.log — 전반 로그
    app_handler = RotatingFileHandler(
        "logs/app.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)

    # trading.log — 매매 전용
    trade_handler = RotatingFileHandler(
        "logs/trading.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8",
    )
    trade_handler.setLevel(logging.INFO)
    trade_handler.setFormatter(formatter)

    # error.log — 에러 전용
    error_handler = RotatingFileHandler(
        "logs/error.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)

    # 루트 로거
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(app_handler)
    root.addHandler(error_handler)

    # 매매 전용 로거
    trade_logger = logging.getLogger("trading")
    trade_logger.addHandler(trade_handler)
```

**사용 패턴:**

```python
import logging
logger = logging.getLogger(__name__)

# 일반 로그
logger.info("스캔 시작")

# 매매 전용 로그
trade_logger = logging.getLogger("trading")
trade_logger.info(f"매수 실행: {stock_code} {quantity}주")
```

---

## 10. 구현 순서 (Phase별)

### Phase 1: 기반 구조 (뼈대)

```
순서:
  1) 폴더 구조 생성 (app/, data/, logs/ + __init__.py)
  2) settings.py — .env 로드 (Tier 1 + Tier 2)
  3) constants.py — 코드 상수 (Tier 3)
  4) logging_config.py — 로그 설정
  5) exceptions.py — 커스텀 예외
  6) domain.py, dto.py — 모델 정의
  7) main.py — FastAPI 앱 팩토리 (빈 lifespan)
  8) 루트 main.py — uvicorn 엔트리포인트
  9) 실행 확인: uvicorn → FastAPI 서버 기동 ✓
```

### Phase 2: KIS API 연동

```
순서:
  1) database.py — SQLite 연결 + 테이블 생성
  2) cache.py — TTL 캐시
  3) kis_auth_repository.py — 토큰 발급/캐싱/hashkey
  4) market_data_repository.py — 현재가, 일봉 조회
  5) order_repository.py — 잔고, 미체결, 주문 실행
  6) order_log_repository.py — orders INSERT
  7) dependencies.py — DI 초기화
  8) market_router.py — /market/price, /market/balance
  9) 실행 확인: API로 현재가 조회 → KIS 응답 확인 ✓
```

### Phase 3: 매매 전략 + 보조지표

```
순서:
  1) trading_service.py — _calculate_ma()
  2) trading_service.py — _calculate_rsi()
  3) trading_service.py — _check_golden_cross()
  4) trading_service.py — _find_cross_day_index()
  5) trading_service.py — _check_volume_confirmation()
  6) trading_service.py — _check_dead_cross()
  7) trading_service.py — _evaluate_sell()
  8) trading_service.py — _evaluate_buy()
  9) trading_service.py — run_scan()
  10) report_repository.py — scan_logs INSERT
  11) trading_router.py — /trading/scan (수동 테스트)
  12) 실행 확인: 수동 스캔 → 매매 판단 로그 확인 ✓
```

### Phase 4: 스케줄러 & 자동화

```
순서:
  1) trading_service.py — run_pre_market(), run_post_market()
  2) report_repository.py — daily_reports, balance_snapshots
  3) 데이터 정리 배치 (cleanup_old_scan_logs)
  4) trading_scheduler.py — APScheduler 3 Jobs
  5) main.py lifespan에 스케줄러 연결
  6) 실행 확인: 모의투자 환경에서 하루 종일 자동 동작 ✓
```

### Phase 5: 모의투자 운영 (최소 3개월)

```
순서:
  1) KIS_IS_PAPER_TRADING=true로 배포
  2) 일일 리포트 → 승률/MDD/평균이익 추적
  3) 주간 리뷰: 매매 로그 분석, 파라미터 검토
  4) 성과 판단: 승률 50%+ → Phase 6 / 45~50% → 파라미터 조정 / <45% → 전략 교체
```

### Phase 6: 소액 실전 (선택적)

```
순서:
  1) KIS_IS_PAPER_TRADING=false로 전환 (100만원)
  2) 슬리피지 실측 (모의 vs 실전 체결가 비교)
  3) 1개월 운영 → 실전 성과 >= 모의 70% 확인
```

### Phase 7: 안정화 & 확장

```
순서:
  1) 점진적 자산 증액
  2) Strategy 인터페이스 (전략 플러그인화)
  3) 백테스트 모듈
  4) 웹 대시보드
  5) PostgreSQL 마이그레이션 (선택)
```
