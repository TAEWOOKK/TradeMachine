"""매도 실패 시 상태 유지 검증 — _execute_sell 반환값 활용."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.model.domain import OrderResult, OrderReason, Position, StockPrice
from app.service.trading_service import TradingService


def _make_settings():
    s = AsyncMock()
    s.stop_loss_rate = -5.0
    s.take_profit_rate = 15.0
    s.trailing_stop_activate = 8.0
    s.trailing_stop_rate = 4.0
    s.max_holding_days = 20
    s.sell_confirm_days = 2
    s.signal_lookback_days = 7
    s.watch_list_codes = []
    return s


def _build_service(settings, **kwargs):
    return TradingService(
        auth_repo=kwargs.get("auth_repo", AsyncMock()),
        market_repo=kwargs.get("market_repo", AsyncMock()),
        order_repo=kwargs.get("order_repo", AsyncMock()),
        order_log_repo=kwargs.get("order_log_repo", AsyncMock()),
        report_repo=kwargs.get("report_repo", AsyncMock()),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_trailing_stop_failure_preserves_state():
    """트레일링 스탑 매도 실패 시 _trailing_activated/highest_prices 유지."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=False, order_no=None, error_message="주문 실패",
    )
    order_log_repo = AsyncMock()
    order_log_repo.get_first_buy_date.return_value = None

    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = StockPrice(
        stock_code="005930", current_price=9400, upper_limit=10500,
        lower_limit=7500, change_rate=-6.0, volume=100000,
        trading_value=940000000, market_cap=1000000000000,
        is_stopped=False, is_managed=False,
        is_caution=False, is_clearing=False,
    )

    svc = _build_service(
        settings, order_repo=order_repo,
        order_log_repo=order_log_repo, market_repo=market_repo,
    )

    svc._trailing_activated.add("005930")
    svc._highest_prices["005930"] = 10000

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=9000.0, profit_rate=10.0, current_price=9400,
    )

    result = await svc._evaluate_sell(pos, unfilled_codes=set())

    assert result == "HOLD"
    assert "005930" in svc._trailing_activated
    assert "005930" in svc._highest_prices


@pytest.mark.asyncio
async def test_stop_loss_failure_returns_hold():
    """손절 매도 실패(재시도 포함) 시 HOLD 반환."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=False, order_no=None, error_message="주문 실패",
    )

    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = StockPrice(
        stock_code="005930", current_price=9000, upper_limit=10500,
        lower_limit=7500, change_rate=-10.0, volume=100000,
        trading_value=900000000, market_cap=1000000000000,
        is_stopped=False, is_managed=False,
        is_caution=False, is_clearing=False,
    )

    svc = _build_service(
        settings, order_repo=order_repo, market_repo=market_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=10000.0, profit_rate=-10.0, current_price=9000,
    )

    result = await svc._evaluate_sell(pos, unfilled_codes=set())

    assert result == "HOLD"
    assert order_repo.execute_order.call_count == 2
