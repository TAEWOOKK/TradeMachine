from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import pytest

from app.model.domain import (
    DailyCandle,
    OrderReason,
    OrderResult,
    OrderType,
    Position,
    ScanResult,
    StockPrice,
)
from app.service.trading_service import TradingService


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _sp(
    stock_code: str = "005930",
    current_price: int = 70000,
    **overrides,
) -> StockPrice:
    defaults = dict(
        stock_code=stock_code,
        current_price=current_price,
        upper_limit=90000,
        lower_limit=50000,
        change_rate=1.0,
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


def _kospi(current_price: int = 2600) -> StockPrice:
    return _sp(
        stock_code="0001",
        current_price=current_price,
        market_cap=0,
        volume=500_000_000,
        trading_value=10_000_000_000_000,
    )


def _kospi_candles(ma20_avg: int = 2500, count: int = 25) -> list[DailyCandle]:
    base = datetime(2026, 3, 10)
    return [
        DailyCandle(
            date=(base - timedelta(days=i)).strftime("%Y%m%d"),
            close=ma20_avg, open=ma20_avg - 10,
            high=ma20_avg + 20, low=ma20_avg - 20,
            volume=500_000_000,
        )
        for i in range(count)
    ]


def _golden_cross_candles() -> list[DailyCandle]:
    """골든크로스가 발생하면서 RSI ~61인 60개 캔들.

    가격 패턴 (index 0 = 최신):
      0: 10400, 1: 10150, 2: 10300, 3: 10100, 4: 10050 (교차일)
      5-10: 하락 구간, 11-14: 회복 전환, 15-59: 안정 기반 9800
    """
    base = datetime(2026, 3, 10)
    closes = [
        10400, 10150, 10300, 10100, 10050,
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


def _build_service(
    mock_settings: MagicMock,
    *,
    market_repo: AsyncMock | None = None,
    order_repo: AsyncMock | None = None,
    order_log_repo: AsyncMock | None = None,
    report_repo: AsyncMock | None = None,
    auth_repo: AsyncMock | None = None,
) -> TradingService:
    mr = market_repo or AsyncMock()
    if not hasattr(mr, "reset_api_count") or isinstance(mr.reset_api_count, AsyncMock):
        mr.reset_api_count = MagicMock()
    if not hasattr(mr, "_api_call_count_configured"):
        type(mr).api_call_count = PropertyMock(return_value=5)
        mr._api_call_count_configured = True
    return TradingService(
        auth_repo=auth_repo or AsyncMock(),
        market_repo=mr,
        order_repo=order_repo or AsyncMock(),
        order_log_repo=order_log_repo or AsyncMock(),
        report_repo=report_repo or AsyncMock(),
        settings=mock_settings,
    )


# ══════════════════════════════════════════════════════════════════════
# Scenario 1: 정상 거래일
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_normal_trading_day(mock_settings):
    """정상 거래일 시나리오:
    1) Pre-market: 토큰 발급, 카운터 초기화
    2) Scan 1: 1 포지션이 stop_loss → 매도
    3) Scan 2: watch_list 종목 1개가 골든크로스 → 매수
    4) Post-market: 일일 리포트 저장 (매도 1, 매수 1)
    """
    auth_repo = AsyncMock()
    order_repo = AsyncMock()
    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None
    report_repo = AsyncMock()
    report_repo.get_yesterday_report.return_value = None
    market_repo = AsyncMock()
    market_repo.reset_api_count = MagicMock()
    type(market_repo).api_call_count = PropertyMock(return_value=5)

    mock_settings.watch_list_codes = ["000660"]

    # ── Phase 1: Pre-market ──

    order_repo.get_balance.return_value = [
        Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=-6.0, current_price=65800,
        ),
    ]
    order_repo.get_unfilled_orders.return_value = []

    svc = _build_service(
        mock_settings,
        auth_repo=auth_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
        report_repo=report_repo,
        market_repo=market_repo,
    )

    pre_market_time = datetime(2026, 3, 11, 8, 50)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = pre_market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_pre_market()

    auth_repo.get_token.assert_called_once()
    assert svc._daily_buy_count == 0

    # ── Phase 2: Scan 1 — 손절 매도 ──

    order_repo.get_balance.return_value = [
        Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=-6.0, current_price=65800,
        ),
    ]
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="SELL001", error_message=None,
    )

    market_repo.get_current_price.return_value = _sp(current_price=65800)
    market_repo.get_daily_chart.return_value = []

    scan_time_1 = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = scan_time_1
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    sell_calls = [
        c for c in order_repo.execute_order.call_args_list
        if c[0][1] == OrderType.SELL
    ]
    assert len(sell_calls) >= 1

    # ── Phase 3: Scan 2 — 골든크로스 매수 ──

    order_repo.get_balance.return_value = []  # 전량 매도 후

    gc_candles = _golden_cross_candles()
    kc = _kospi_candles(ma20_avg=2500)

    market_repo.get_index_price.return_value = _kospi(2600)
    market_repo.get_current_price.return_value = _sp(stock_code="000660", current_price=10400)

    async def _get_chart(code, days=60):
        if code == "0001":
            return kc
        return gc_candles

    market_repo.get_daily_chart.side_effect = _get_chart

    order_repo.get_available_cash.return_value = 10_000_000
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="BUY001", error_message=None,
    )

    scan_time_2 = datetime(2026, 3, 11, 10, 5)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = scan_time_2
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    buy_calls = [
        c for c in order_repo.execute_order.call_args_list
        if c[0][1] == OrderType.BUY
    ]
    assert len(buy_calls) >= 1

    # ── Phase 4: Post-market ──

    order_repo.get_balance.return_value = [
        Position(
            stock_code="000660", quantity=90,
            avg_price=10800.0, profit_rate=0.5, current_price=10854,
        ),
    ]
    order_repo.get_unfilled_orders.return_value = []
    order_repo.get_account_summary.return_value = {
        "total_cash": 3_000_000, "stock_eval": 976_860, "total_assets": 3_976_860,
    }
    order_log_repo.get_today_counts.return_value = {
        "buy_count": 1, "sell_count": 1, "fail_count": 0,
    }

    post_time = datetime(2026, 3, 11, 15, 30)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = post_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_post_market()

    report_repo.save_daily_report.assert_called_once()
    call_kwargs = report_repo.save_daily_report.call_args[1]
    assert call_kwargs["buy_count"] == 1
    assert call_kwargs["sell_count"] == 1
    report_repo.save_balance_snapshot.assert_called_once()


# ══════════════════════════════════════════════════════════════════════
# Scenario 2: 약세장
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bear_market_day(mock_settings):
    """약세장 시나리오:
    - KOSPI가 MA20 아래 → 모든 매수 시도 SKIP
    - 기존 수익 포지션이 take_profit 도달 → 매도
    """
    order_repo = AsyncMock()
    order_repo.get_unfilled_orders.return_value = []
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="TP001", error_message=None,
    )

    positions = [
        Position(
            stock_code="005930", quantity=10,
            avg_price=60000.0, profit_rate=16.0, current_price=69600,
        ),
    ]
    order_repo.get_balance.return_value = positions

    market_repo = AsyncMock()
    market_repo.reset_api_count = MagicMock()
    type(market_repo).api_call_count = PropertyMock(return_value=5)

    bearish_kospi = _kospi(current_price=2300)
    bearish_kospi_candles = _kospi_candles(ma20_avg=2500)

    market_repo.get_index_price.return_value = bearish_kospi

    async def _get_price(code, market_code="J"):
        return _sp(stock_code=code, current_price=69600)

    market_repo.get_current_price.side_effect = _get_price

    async def _get_chart(code, days=60):
        if code == "0001":
            return bearish_kospi_candles
        return _golden_cross_candles()

    market_repo.get_daily_chart.side_effect = _get_chart

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None
    report_repo = AsyncMock()

    mock_settings.watch_list_codes = ["000660", "035420"]

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        order_log_repo=order_log_repo,
        report_repo=report_repo,
    )

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    sell_calls = [
        c for c in order_repo.execute_order.call_args_list
        if c[0][1] == OrderType.SELL
    ]
    assert len(sell_calls) == 1

    buy_calls = [
        c for c in order_repo.execute_order.call_args_list
        if c[0][1] == OrderType.BUY
    ]
    assert len(buy_calls) == 0

    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.TAKE_PROFIT


# ══════════════════════════════════════════════════════════════════════
# Scenario 3: 한 스캔에 복수 매도
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multiple_sells_in_one_scan(mock_settings):
    """복수 매도 시나리오:
    - 3 포지션: stop_loss, trailing_stop, hold
    - 매도 2건, 보유 1건
    """
    order_repo = AsyncMock()
    order_repo.get_unfilled_orders.return_value = []
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="MULTI", error_message=None,
    )

    pos_stop_loss = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=-6.0, current_price=65800,
    )
    pos_trailing = Position(
        stock_code="000660", quantity=5,
        avg_price=80000.0, profit_rate=5.0, current_price=84000,
    )
    pos_hold = Position(
        stock_code="035420", quantity=8,
        avg_price=50000.0, profit_rate=3.0, current_price=51500,
    )

    order_repo.get_balance.return_value = [pos_stop_loss, pos_trailing, pos_hold]

    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=10)

    price_map = {
        "005930": _sp(stock_code="005930", current_price=65800),
        "000660": _sp(stock_code="000660", current_price=84000),
        "035420": _sp(stock_code="035420", current_price=51500),
    }
    market_repo.get_current_price.side_effect = lambda code, **kw: price_map.get(code, _sp())
    market_repo.get_daily_chart.return_value = []
    market_repo.reset_api_count = MagicMock()

    order_log_repo = AsyncMock()
    order_log_repo.get_first_buy_date.return_value = None
    report_repo = AsyncMock()

    mock_settings.watch_list_codes = []

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        order_log_repo=order_log_repo,
        report_repo=report_repo,
    )

    # trailing stop pre-setup: 000660 활성화 + 최고가 88000
    svc._trailing_activated.add("000660")
    svc._highest_prices["000660"] = 88000
    # 84000 < 88000 * (1 - 4/100) = 84480 → trailing stop

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    sell_calls = [
        c for c in order_repo.execute_order.call_args_list
        if c[0][1] == OrderType.SELL
    ]
    assert len(sell_calls) == 2

    reasons = [c[0][2] for c in order_log_repo.save_order.call_args_list]
    assert OrderReason.STOP_LOSS in reasons
    assert OrderReason.TRAILING_STOP in reasons

    report_repo.save_scan_log.assert_called_once()
    scan_result = report_repo.save_scan_log.call_args[0][0]
    assert scan_result.sell_count == 2


# ══════════════════════════════════════════════════════════════════════
# Scenario 4: 일일 매수 한도
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_max_daily_buy_limit(mock_settings):
    """이미 3종목을 매수한 상태에서 4번째 골든크로스는 SKIP."""
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = []
    order_repo.get_unfilled_orders.return_value = []

    market_repo = AsyncMock()
    market_repo.reset_api_count = MagicMock()
    type(market_repo).api_call_count = PropertyMock(return_value=3)

    gc_candles = _golden_cross_candles()
    kc = _kospi_candles(ma20_avg=2500)

    market_repo.get_index_price.return_value = _kospi(2600)

    async def _get_price(code, market_code="J"):
        return _sp(stock_code=code, current_price=10400)

    market_repo.get_current_price.side_effect = _get_price

    async def _get_chart(code, days=60):
        if code == "0001":
            return kc
        return gc_candles

    market_repo.get_daily_chart.side_effect = _get_chart

    order_log_repo = AsyncMock()
    order_log_repo.get_last_sell_time.return_value = None
    report_repo = AsyncMock()

    mock_settings.watch_list_codes = ["005930"]
    mock_settings.max_daily_buy_count = 3

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        order_log_repo=order_log_repo,
        report_repo=report_repo,
    )
    svc._daily_buy_count = 3

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.execute_order.assert_not_called()

    report_repo.save_scan_log.assert_called_once()
    scan_result = report_repo.save_scan_log.call_args[0][0]
    assert scan_result.buy_count == 0
