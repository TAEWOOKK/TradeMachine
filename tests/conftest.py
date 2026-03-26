from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from app.core.database import Database
from app.model.domain import (
    DailyCandle,
    OrderResult,
    Position,
    StockPrice,
)


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.kis_app_key = "test_app_key"
    s.kis_app_secret = "test_app_secret"
    s.kis_cano = "12345678"
    s.kis_acnt_prdt_cd = "01"
    s.kis_base_url = "https://openapi.koreainvestment.com:9443"
    s.kis_is_paper_trading = True
    s.watch_list = "005930,000660,035420"
    s.watch_list_codes = ["005930", "000660", "035420"]
    s.trading_interval_minutes = 5
    s.signal_lookback_days = 7
    s.signal_confirm_days = 3
    s.sell_confirm_days = 2
    s.volume_confirm_ratio = 1.5
    s.rsi_overbought = 70
    s.rsi_oversold = 30
    s.max_investment_ratio = 0.1
    s.max_holding_count = 5
    s.max_daily_buy_count = 3
    s.max_holding_days = 20
    s.stop_loss_rate = -5.0
    s.take_profit_rate = 15.0
    s.trailing_stop_activate = 8.0
    s.trailing_stop_rate = 4.0
    s.rebuy_cooldown_hours = 24
    s.scalping_entry_minute = 0  # 테스트에서 시간 제한 비활성화
    s.min_intraday_change = 0.3
    s.max_intraday_change = 4.0
    s.rsi_scalping_min = 50
    s.rsi_scalping_max = 65
    s.enable_market_filter = True
    s.eod_close_enabled = True
    return s


@pytest.fixture
def sample_position():
    return Position(
        stock_code="005930",
        quantity=10,
        avg_price=70000.0,
        profit_rate=2.5,
        current_price=71750,
    )


@pytest.fixture
def sample_stock_price():
    return StockPrice(
        stock_code="005930",
        current_price=71750,
        upper_limit=90000,
        lower_limit=50000,
        change_rate=1.5,
        volume=10_000_000,
        trading_value=5_000_000_000_000,
        market_cap=400_000_000_000_000,
        is_stopped=False,
        is_managed=False,
        is_caution=False,
        is_clearing=False,
    )


def _make_candle(date: datetime, close: int, volume: int = 1_000_000) -> DailyCandle:
    return DailyCandle(
        date=date.strftime("%Y%m%d"),
        close=close,
        open=close - 50,
        high=close + 100,
        low=close - 100,
        volume=volume,
    )


def _skip_weekends(base: datetime, offset: int) -> datetime:
    d = base - timedelta(days=offset)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@pytest.fixture
def sample_candles():
    """60+ candles with a V-shaped recovery producing a golden cross around day 4-5.

    Price pattern (index 0 = most recent):
      0-4:   10800→10000  (recovery peak, uptrend)
      5-9:   9800→9000    (recovery slope)
      10-12: 8800          (V-bottom)
      13-14: 9000, 9200    (entering dip)
      15-59: 10000          (old stable base)
    """
    base = datetime(2026, 3, 10)
    closes: list[int] = []
    for i in range(5):
        closes.append(10800 - i * 200)
    for i in range(5):
        closes.append(9800 - i * 200)
    closes.extend([8800, 8800, 8800])
    closes.extend([9000, 9200])
    closes.extend([10000] * 45)

    candles: list[DailyCandle] = []
    for i, close in enumerate(closes):
        d = _skip_weekends(base, i)
        vol = 3_000_000 if i == 4 else 1_000_000
        candles.append(_make_candle(d, close, volume=vol))
    return candles


@pytest.fixture
def sample_candles_dead_cross():
    """30 candles producing a dead cross (MA5 crossing below MA20).

    Price pattern (index 0 = most recent):
      0-4:   9200→9600   (recent decline, lowest most recent)
      5-9:   10500→10100 (was at peak)
      10-29: 10000        (old stable base)
    """
    base = datetime(2026, 3, 10)
    closes: list[int] = []
    for i in range(5):
        closes.append(9200 + i * 100)
    for i in range(5):
        closes.append(10500 - i * 100)
    closes.extend([10000] * 20)

    candles: list[DailyCandle] = []
    for i, close in enumerate(closes):
        d = _skip_weekends(base, i)
        candles.append(_make_candle(d, close, volume=800_000))
    return candles


@pytest.fixture
def sample_order_result_success():
    return OrderResult(success=True, order_no="12345", error_message=None)


@pytest.fixture
def sample_order_result_failure():
    return OrderResult(success=False, order_no=None, error_message="error")


@pytest_asyncio.fixture
async def db_instance():
    db = Database(db_path=":memory:")
    await db.connect()
    yield db
    await db.disconnect()
