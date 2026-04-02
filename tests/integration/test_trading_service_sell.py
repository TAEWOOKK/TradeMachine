from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_stock_price(
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
# P0: 손절
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_loss_triggers_sell(mock_settings):
    """profit_rate=-6.0이면 stop_loss_rate(-5.0) 이하이므로 즉시 매도."""
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="SL001", error_message=None,
    )
    order_log_repo = AsyncMock()
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=65800)

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=-6.0, current_price=65800,
    )

    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "SOLD"
    order_repo.execute_order.assert_called_once_with(
        "005930", OrderType.SELL, 10,
    )
    order_log_repo.save_order.assert_called_once()
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.STOP_LOSS


# ──────────────────────────────────────────────────────────────────────
# P1: 익절
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_take_profit_triggers_sell(mock_settings):
    """profit_rate=16.0이면 take_profit_rate(15.0) 이상이므로 익절 매도."""
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="TP001", error_message=None,
    )
    order_log_repo = AsyncMock()
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=81200)

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=16.0, current_price=81200,
    )

    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "SOLD"
    order_repo.execute_order.assert_called_once_with(
        "005930", OrderType.SELL, 10,
    )
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.TAKE_PROFIT


# ──────────────────────────────────────────────────────────────────────
# P2: 트레일링 스탑
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trailing_stop_triggers_sell(mock_settings):
    """1차 호출에서 trailing 활성화(profit_rate=9.0), highest를 11000으로 갱신.
    2차 호출에서 가격이 10500으로 하락(11000 대비 4.5% 하락) → trailing stop 매도."""
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="TS001", error_message=None,
    )
    order_log_repo = AsyncMock()
    order_log_repo.get_first_buy_date.return_value = None
    market_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    # 1차: profit_rate=9.0 → trailing_stop_activate(8.0)보다 높으므로 활성화
    market_repo.get_current_price.return_value = _make_stock_price(current_price=10900)
    market_repo.get_daily_chart.return_value = []

    pos_first = Position(
        stock_code="005930", quantity=10,
        avg_price=10000.0, profit_rate=9.0, current_price=10900,
    )
    result1 = await svc.sell_evaluator.evaluate_sell(pos_first, unfilled_codes=set())
    assert result1 == "HOLD"
    assert "005930" in svc._trailing_activated

    # highest를 11000으로 수동 갱신 (시뮬레이션)
    svc._highest_prices["005930"] = 11000

    # 2차: 가격 10500 → 11000 * (1 - 4/100) = 10560보다 낮으므로 trailing stop
    market_repo.get_current_price.return_value = _make_stock_price(current_price=10500)
    pos_second = Position(
        stock_code="005930", quantity=10,
        avg_price=10000.0, profit_rate=5.0, current_price=10500,
    )
    result2 = await svc.sell_evaluator.evaluate_sell(pos_second, unfilled_codes=set())

    assert result2 == "SOLD"
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.TRAILING_STOP


# ──────────────────────────────────────────────────────────────────────
# P3: 최대 보유일 초과
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_holding_days_triggers_sell(mock_settings):
    """최초 매수일이 25영업일 전이면 max_holding_days(20) 초과로 매도."""
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="MH001", error_message=None,
    )
    order_log_repo = AsyncMock()

    buy_date = datetime.now() - timedelta(days=40)
    order_log_repo.get_first_buy_date.return_value = buy_date.strftime("%Y-%m-%d")

    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=71000)
    market_repo.get_daily_chart.return_value = []

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=1.4, current_price=71000,
    )
    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "SOLD"
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.MAX_HOLDING


# ──────────────────────────────────────────────────────────────────────
# P4: 데드크로스
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_cross_triggers_sell(mock_settings, sample_candles_dead_cross):
    """데드크로스 패턴 일봉 데이터를 제공하면 DEAD_CROSS 매도."""
    order_repo = AsyncMock()
    order_repo.execute_order.return_value = OrderResult(
        success=True, order_no="DC001", error_message=None,
    )
    order_log_repo = AsyncMock()
    order_log_repo.get_first_buy_date.return_value = None

    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=9200)
    market_repo.get_daily_chart.return_value = sample_candles_dead_cross

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=9000.0, profit_rate=2.2, current_price=9200,
    )
    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "SOLD"
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.DEAD_CROSS


# ──────────────────────────────────────────────────────────────────────
# HOLD / SKIP 시나리오
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_sell_on_hold(mock_settings):
    """profit_rate=3.0이고 데드크로스도 없으면 HOLD 반환."""
    order_log_repo = AsyncMock()
    order_log_repo.get_first_buy_date.return_value = None

    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=72100)
    market_repo.get_daily_chart.return_value = []  # 캔들 부족 → 데드크로스 불가

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_log_repo=order_log_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=3.0, current_price=72100,
    )
    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "HOLD"


@pytest.mark.asyncio
async def test_skip_if_stopped(mock_settings):
    """거래 정지 종목은 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(
        current_price=70000, is_stopped=True,
    )

    svc = _build_service(mock_settings, market_repo=market_repo)

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=-6.0, current_price=70000,
    )
    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "SKIP"


@pytest.mark.asyncio
async def test_skip_if_unfilled(mock_settings):
    """미체결 목록에 있는 종목은 SKIP."""
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=70000)

    svc = _build_service(mock_settings, market_repo=market_repo)

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=-6.0, current_price=70000,
    )
    result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes={"005930"})

    assert result == "SKIP"


# ──────────────────────────────────────────────────────────────────────
# 손절 실패 시 1회 재시도
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_loss_retries_on_failure(mock_settings):
    """손절 첫 execute_order 실패 → 1회 재시도."""
    order_repo = AsyncMock()
    order_repo.execute_order.side_effect = [
        OrderResult(success=False, order_no=None, error_message="timeout"),
        OrderResult(success=True, order_no="SL_RETRY", error_message=None),
    ]
    order_log_repo = AsyncMock()
    market_repo = AsyncMock()
    market_repo.get_current_price.return_value = _make_stock_price(current_price=65000)

    svc = _build_service(
        mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
    )

    pos = Position(
        stock_code="005930", quantity=10,
        avg_price=70000.0, profit_rate=-6.0, current_price=65000,
    )

    with patch("app.service.sell_evaluator.asyncio.sleep", new_callable=AsyncMock):
        result = await svc.sell_evaluator.evaluate_sell(pos, unfilled_codes=set())

    assert result == "SOLD"
    assert order_repo.execute_order.call_count == 2
    saved_reason = order_log_repo.save_order.call_args[0][2]
    assert saved_reason == OrderReason.STOP_LOSS
