"""get_balance() None 반환 시 consecutive_failures 증가 검증."""
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, PropertyMock, patch

from app.service.trading_service import TradingService


def _make_settings():
    s = AsyncMock()
    s.watch_list_codes = ["005930"]
    s.stop_loss_rate = -5.0
    s.take_profit_rate = 15.0
    s.trailing_stop_activate = 8.0
    s.trailing_stop_rate = 4.0
    s.max_holding_days = 20
    s.max_holding_count = 5
    s.max_daily_buy_count = 3
    s.enable_market_filter = False
    return s


def _build_service(settings, order_repo=None, market_repo=None, report_repo=None):
    from unittest.mock import MagicMock

    mr = market_repo or AsyncMock()
    mr.reset_api_count = MagicMock()
    if not isinstance(type(mr).__dict__.get("api_call_count"), PropertyMock):
        type(mr).api_call_count = PropertyMock(return_value=0)

    return TradingService(
        auth_repo=AsyncMock(),
        market_repo=mr,
        order_repo=order_repo or AsyncMock(),
        order_log_repo=AsyncMock(),
        report_repo=report_repo or AsyncMock(),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_balance_none_increments_failures():
    """get_balance()가 None 반환 시 _consecutive_failures 증가."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = None
    report_repo = AsyncMock()

    svc = _build_service(settings, order_repo=order_repo, report_repo=report_repo)
    assert svc._consecutive_failures == 0

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    assert svc._consecutive_failures == 1


@pytest.mark.asyncio
async def test_success_after_failure_resets_counter():
    """실패 후 성공 시 _consecutive_failures 리셋."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = []
    order_repo.get_unfilled_orders.return_value = []
    report_repo = AsyncMock()
    market_repo = AsyncMock()
    type(market_repo).api_call_count = PropertyMock(return_value=0)

    svc = _build_service(settings, order_repo=order_repo,
                          market_repo=market_repo, report_repo=report_repo)
    svc._consecutive_failures = 3

    market_time = datetime(2026, 3, 11, 10, 0)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = market_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_scan()

    assert svc._consecutive_failures == 0
