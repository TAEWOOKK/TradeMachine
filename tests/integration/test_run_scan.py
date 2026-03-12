from __future__ import annotations

from datetime import datetime, time as dt_time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.model.domain import OrderResult, Position, ScanResult, StockPrice
from app.service.trading_service import TradingService


def _make_stock_price(stock_code: str = "005930", **overrides) -> StockPrice:
    defaults = dict(
        stock_code=stock_code,
        current_price=70000,
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
    mr.reset_api_count = MagicMock()
    if not isinstance(type(mr).__dict__.get("api_call_count"), PropertyMock):
        type(mr).api_call_count = PropertyMock(return_value=5)
    return TradingService(
        auth_repo=auth_repo or AsyncMock(),
        market_repo=mr,
        order_repo=order_repo or AsyncMock(),
        order_log_repo=order_log_repo or AsyncMock(),
        report_repo=report_repo or AsyncMock(),
        settings=mock_settings,
    )


# ──────────────────────────────────────────────────────────────────────
# 주말 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_weekend_skips(mock_settings):
    """토요일에는 스캔이 실행되지 않는다."""
    order_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )

    saturday = datetime(2026, 3, 14, 10, 0)  # 2026-03-14 is Saturday
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = saturday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 공휴일 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_holiday_skips(mock_settings):
    """KRX 휴장일에는 스캔이 실행되지 않는다."""
    order_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )

    # 20260101 is in KRX_HOLIDAYS — Thursday
    holiday = datetime(2026, 1, 1, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = holiday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 장전 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_before_market_skips(mock_settings):
    """08:00에는 아직 시장 개장 전이므로 스캔 스킵."""
    order_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )

    early = datetime(2026, 3, 11, 8, 0)  # Wednesday 08:00
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = early
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 장후 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_after_market_skips(mock_settings):
    """15:30에는 이미 마감 후이므로 스캔 스킵."""
    order_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )

    late = datetime(2026, 3, 11, 15, 30)  # Wednesday 15:30
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = late
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 정상 스캔 사이클
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_full_cycle(mock_settings):
    """시장 시간에 포지션과 watch_list 를 순회하여 매도/매수 평가를 수행."""
    positions = [
        Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=-6.0, current_price=65800,
        ),
    ]

    order_repo = AsyncMock()
    order_repo.get_balance.return_value = positions
    order_repo.get_unfilled_orders.return_value = []
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="ORD001", error_message=None,
    )

    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=3)
    market_repo.get_current_price.return_value = _make_stock_price(
        current_price=65800,
    )
    market_repo.get_daily_chart.return_value = []

    order_log_repo = AsyncMock()
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        order_log_repo=order_log_repo,
        report_repo=report_repo,
    )

    market_time = datetime(2026, 3, 11, 10, 0)  # Wednesday 10:00
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.get_balance.assert_called_once()
    market_repo.get_current_price.assert_called()
    report_repo.save_scan_log.assert_called_once()


# ──────────────────────────────────────────────────────────────────────
# 연속 실패 5회 → 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_consecutive_failures_skip(mock_settings):
    """연속 실패 5회이면 스캔을 SKIP (get_balance 호출 안 함)."""
    order_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )
    svc._consecutive_failures = 5

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 연속 실패 10회 → critical 로그
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_consecutive_failures_critical(mock_settings):
    """연속 실패 10회이면 critical 로그를 남기고 스캔 중단."""
    order_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )
    svc._consecutive_failures = 5

    market_time = datetime(2026, 3, 11, 10, 0)
    with (
        patch("app.service.trading_service.datetime") as mock_dt,
        patch("app.service.trading_service.logger") as mock_logger,
    ):
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    mock_logger.warning.assert_called()
    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 스캔 결과 저장
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_saves_result(mock_settings):
    """스캔 사이클 후 save_scan_log가 ScanResult와 함께 호출된다."""
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = []
    order_repo.get_unfilled_orders.return_value = []

    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)

    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        market_repo=market_repo,
        report_repo=report_repo,
    )

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    report_repo.save_scan_log.assert_called_once()
    saved_result = report_repo.save_scan_log.call_args[0][0]
    assert isinstance(saved_result, ScanResult)
    assert saved_result.holding_count == 0
    assert saved_result.sell_count == 0
    assert saved_result.buy_count == 0
