from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model.domain import (
    DailyCandle,
    OrderReason,
    OrderResult,
    OrderType,
    Position,
    StockPrice,
)
from app.service.trading_service import TradingService


def _golden_cross_candles(current_price: int = 10400) -> list[DailyCandle]:
    """골든크로스가 발생하면서 RSI ~61 (과열 아님)인 60개 캔들 생성.

    가격 패턴 (index 0 = 최신):
      0: 10400, 1: 10150, 2: 10300, 3: 10100, 4: 10050 (교차일, 고거래량)
      5: 9950, 6: 9750, 7: 9550, 8: 9400, 9: 9300, 10: 9200
      11: 9300, 12: 9500, 13: 9700, 14: 9900
      15-59: 9800 (안정 기간)
    """
    base = datetime(2026, 3, 10)
    closes = [
        current_price, 10150, 10300, 10100, 10050,
        9950, 9750, 9550, 9400, 9300, 9200,
        9300, 9500, 9700, 9900,
    ]
    closes.extend([9800] * 45)

    candles: list[DailyCandle] = []
    for i, close in enumerate(closes):
        d = base - timedelta(days=i)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        candles.append(DailyCandle(
            date=d.strftime("%Y%m%d"),
            close=close,
            open=close - 50,
            high=close + 100,
            low=close - 100,
            volume=3_000_000 if i == 4 else 1_000_000,
        ))
    return candles


def _make_stock_price(
    stock_code: str = "005930",
    current_price: int = 10800,
    **overrides,
) -> StockPrice:
    defaults = dict(
        stock_code=stock_code,
        current_price=current_price,
        upper_limit=14000,
        lower_limit=7000,
        change_rate=2.0,
        volume=10_000_000,
        trading_value=5_000_000_000_000,
        market_cap=400_000_000_000_000,
        is_stopped=False,
        is_managed=False,
        is_caution=False,
        is_clearing=False,
    )
    defaults.update(overrides)
    return StockPrice(**defaults)


def _make_kospi_price(current_price: int = 2600) -> StockPrice:
    return StockPrice(
        stock_code="0001",
        current_price=current_price,
        upper_limit=3000,
        lower_limit=2000,
        change_rate=0.5,
        volume=500_000_000,
        trading_value=10_000_000_000_000,
        market_cap=0,
        is_stopped=False,
        is_managed=False,
        is_caution=False,
        is_clearing=False,
    )


def _make_kospi_candles(ma20_avg: int = 2500) -> list[DailyCandle]:
    """KOSPI가 MA20 위에 있도록 20개 캔들 생성."""
    base = datetime(2026, 3, 10)
    return [
        DailyCandle(
            date=(base - timedelta(days=i)).strftime("%Y%m%d"),
            close=ma20_avg,
            open=ma20_avg - 10,
            high=ma20_avg + 20,
            low=ma20_avg - 20,
            volume=500_000_000,
        )
        for i in range(25)
    ]


def _build_service(
    mock_settings: MagicMock,
    *,
    market_repo: AsyncMock | None = None,
    order_repo: AsyncMock | None = None,
    order_log_repo: AsyncMock | None = None,
    report_repo: AsyncMock | None = None,
    auth_repo: AsyncMock | None = None,
) -> TradingService:
    return TradingService(
        auth_repo=auth_repo or AsyncMock(),
        market_repo=market_repo or AsyncMock(),
        order_repo=order_repo or AsyncMock(),
        order_log_repo=order_log_repo or AsyncMock(),
        report_repo=report_repo or AsyncMock(),
        settings=mock_settings,
    )


# ──────────────────────────────────────────────────────────────────────
# 골든크로스 매수 성공
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buy_on_golden_cross(mock_settings):
    """모든 조건 충족 시 골든크로스 매수."""
    gc_candles = _golden_cross_candles(current_price=10400)
    stock_price = _make_stock_price(current_price=10400)
    kospi_price = _make_kospi_price(current_price=2600)
    kospi_candles = _make_kospi_candles(ma20_avg=2500)

    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = kospi_price
    market_repo.get_current_price.return_value = stock_price

    async def _get_chart(code: str, days: int = 60):
        if code == "0001":
            return kospi_candles
        return gc_candles

    market_repo.get_daily_chart.side_effect = _get_chart

    order_repo = AsyncMock()
    order_repo.get_account_summary = AsyncMock(return_value={"total_cash": 10_000_000})
    order_repo.get_available_cash.return_value = 10_000_000
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="BUY001", error_message=None,
    )

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    result = await svc._evaluate_buy(
        "005930",
        current_holding_count=0,
        holding_codes=set(),
        unfilled_codes=set(),
        market_ok=True,
    )

    assert result == "BOUGHT"
    order_repo.execute_order.assert_called_once()
    call_args = order_repo.execute_order.call_args
    assert call_args[0][0] == "005930"
    assert call_args[0][1] == OrderType.BUY

    order_log_repo.save_order.assert_called_once()
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.SCALPING_ENTRY


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 관리종목
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_managed_stock(mock_settings):
    """관리종목은 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(is_managed=True)

    svc = _build_service(mock_settings, market_repo=market_repo)

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 저가주
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_low_price(mock_settings):
    """현재가 < MIN_STOCK_PRICE(5000)이면 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=3000)

    kospi_candles = _make_kospi_candles(ma20_avg=2500)

    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price(current_price=3000)

    async def _get_chart(code, days=60):
        if code == "0001":
            return kospi_candles
        return []

    market_repo.get_daily_chart.side_effect = _get_chart

    svc = _build_service(mock_settings, market_repo=market_repo)

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 시가총액 미달
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_low_market_cap(mock_settings):
    """시가총액 < MIN_MARKET_CAP이면 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price(
        current_price=10000,
        market_cap=100_000_000_000,
    )
    market_repo.get_daily_chart.return_value = _make_kospi_candles(2500)

    svc = _build_service(mock_settings, market_repo=market_repo)

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 이미 보유 중
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_already_holding(mock_settings):
    """이미 보유 중인 종목은 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price()
    market_repo.get_daily_chart.return_value = _make_kospi_candles(2500)

    svc = _build_service(mock_settings, market_repo=market_repo)

    result = await svc._evaluate_buy(
        "005930", current_holding_count=1,
        holding_codes={"005930"}, unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 일일 매수 한도
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_daily_buy_limit(mock_settings):
    """_daily_buy_count >= max_daily_buy_count이면 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price()
    market_repo.get_daily_chart.return_value = _make_kospi_candles(2500)

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None

    svc = _build_service(
        mock_settings, market_repo=market_repo, order_log_repo=order_log_repo,
    )
    svc._daily_buy_count = 3  # max_daily_buy_count = 3

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 재매수 쿨다운
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_rebuy_cooldown(mock_settings):
    """마지막 매도가 1시간 전이면 쿨다운(24h) 미충족 → SKIP."""
    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price()
    market_repo.get_daily_chart.return_value = _make_kospi_candles(2500)

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = datetime.now() - timedelta(hours=1)

    svc = _build_service(
        mock_settings, market_repo=market_repo, order_log_repo=order_log_repo,
    )

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 골든크로스 없음
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_no_golden_cross(mock_settings):
    """MA 배열에서 골든크로스가 없으면 SKIP."""
    base = datetime(2026, 3, 10)
    flat_candles = [
        DailyCandle(
            date=(base - timedelta(days=i)).strftime("%Y%m%d"),
            close=10000, open=9950, high=10050, low=9950,
            volume=1_000_000,
        )
        for i in range(60)
    ]

    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price(current_price=10000)

    async def _get_chart(code, days=60):
        if code == "0001":
            return _make_kospi_candles(2500)
        return flat_candles

    market_repo.get_daily_chart.side_effect = _get_chart

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None

    svc = _build_service(
        mock_settings, market_repo=market_repo, order_log_repo=order_log_repo,
    )

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: RSI 과열
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_rsi_overbought(mock_settings, sample_candles):
    """RSI > 70이면 SKIP.
    RSI를 강제로 높이기 위해 최근 15개 캔들을 연속 상승으로 조작."""
    base = datetime(2026, 3, 10)
    rsi_high_candles = []
    for i in range(60):
        rsi_high_candles.append(
            DailyCandle(
                date=(base - timedelta(days=i)).strftime("%Y%m%d"),
                close=15000 - i * 50,
                open=15000 - i * 50 - 30,
                high=15000 - i * 50 + 100,
                low=15000 - i * 50 - 100,
                volume=3_000_000 if i == 4 else 1_000_000,
            )
        )

    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price(current_price=15000)

    async def _get_chart(code, days=60):
        if code == "0001":
            return _make_kospi_candles(2500)
        return rsi_high_candles

    market_repo.get_daily_chart.side_effect = _get_chart

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None

    svc = _build_service(
        mock_settings, market_repo=market_repo, order_log_repo=order_log_repo,
    )

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 시장 필터 (KOSPI 약세)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_market_filter_bearish(mock_settings):
    """시장 필터가 bearish 판정 시 (market_ok=False) SKIP."""
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price()

    svc = _build_service(mock_settings, market_repo=market_repo)

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=False,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 거래량 부족
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_volume_insufficient(mock_settings, sample_candles):
    """교차일 거래량이 평균 * 1.5 미만이면 SKIP.
    sample_candles의 cross_day(index 4) 볼륨을 낮춰서 테스트."""
    low_vol_candles = list(sample_candles)
    original = low_vol_candles[4]
    low_vol_candles[4] = DailyCandle(
        date=original.date,
        close=original.close,
        open=original.open,
        high=original.high,
        low=original.low,
        volume=100_000,
    )

    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price(current_price=10800)

    async def _get_chart(code, days=60):
        if code == "0001":
            return _make_kospi_candles(2500)
        return low_vol_candles

    market_repo.get_daily_chart.side_effect = _get_chart

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None

    svc = _build_service(
        mock_settings, market_repo=market_repo, order_log_repo=order_log_repo,
    )

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# SKIP 시나리오: 상한가
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_at_upper_limit(mock_settings):
    """현재가 == 상한가이면 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_index_price.return_value = _make_kospi_price(2600)
    market_repo.get_current_price.return_value = _make_stock_price(current_price=14000, upper_limit=14000)
    market_repo.get_daily_chart.return_value = _make_kospi_candles(2500)

    svc = _build_service(mock_settings, market_repo=market_repo)

    result = await svc._evaluate_buy(
        "005930", current_holding_count=0,
        holding_codes=set(), unfilled_codes=set(), market_ok=True,
    )
    assert result == "SKIP"
