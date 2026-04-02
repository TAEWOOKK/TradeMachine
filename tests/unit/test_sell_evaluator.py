from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model.domain import OrderResult, Position, StockPrice
from app.service.indicator_service import IndicatorService
from app.service.market_guard import MarketGuard
from app.service.sell_evaluator import SellEvaluator


def _price(current: int, change_rate: float = 1.0) -> StockPrice:
    return StockPrice(
        stock_code="005930",
        current_price=current,
        upper_limit=90000,
        lower_limit=50000,
        change_rate=change_rate,
        volume=10_000_000,
        trading_value=5_000_000_000_000,
        market_cap=400_000_000_000_000,
        is_stopped=False,
        is_managed=False,
        is_caution=False,
        is_clearing=False,
    )


@pytest.fixture
def sell_ctx(mock_settings):
    market_repo = AsyncMock()
    order_repo = AsyncMock()
    order_log_repo = AsyncMock()
    indicator = MagicMock(spec=IndicatorService)
    indicator.check_dead_cross = AsyncMock(return_value=False)
    indicator.count_business_days = MagicMock(return_value=1)
    market_guard = MagicMock(spec=MarketGuard)

    evaluator = SellEvaluator(
        settings=mock_settings,
        market_repo=market_repo,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
        indicator=indicator,
        market_guard=market_guard,
    )

    order_repo.execute_order = AsyncMock(
        return_value=OrderResult(success=True, order_no="ORD001", error_message=None),
    )
    order_log_repo.save_order = AsyncMock()
    order_log_repo.save_trailing_state = AsyncMock()
    order_log_repo.delete_trailing_state = AsyncMock()
    order_log_repo.get_first_buy_date = AsyncMock(return_value=None)

    return {
        "evaluator": evaluator,
        "market_repo": market_repo,
        "order_repo": order_repo,
        "order_log_repo": order_log_repo,
        "indicator": indicator,
        "market_guard": market_guard,
    }


class TestStopLoss:
    async def test_stop_loss_triggers(self, sell_ctx):
        """수익률이 stop_loss_rate 이하면 SOLD."""
        ev = sell_ctx["evaluator"]
        sell_ctx["market_repo"].get_current_price = AsyncMock(
            return_value=_price(66000),
        )
        pos = Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=-5.7, current_price=66000,
        )
        result = await ev.evaluate_sell(pos, unfilled_codes=set())
        assert result == "SOLD"
        sell_ctx["market_guard"].record_stop_loss.assert_called_once()


class TestTakeProfit:
    async def test_take_profit_triggers(self, sell_ctx):
        """수익률이 take_profit_rate 이상이면 SOLD."""
        ev = sell_ctx["evaluator"]
        sell_ctx["market_repo"].get_current_price = AsyncMock(
            return_value=_price(81000),
        )
        pos = Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=15.7, current_price=81000,
        )
        result = await ev.evaluate_sell(pos, unfilled_codes=set())
        assert result == "SOLD"
        sell_ctx["market_guard"].record_profit.assert_called_once()


class TestTrailingStop:
    async def test_trailing_stop_activates_and_triggers(self, sell_ctx):
        """트레일링 스탑: 활성화 후 고점 대비 하락 → SOLD."""
        ev = sell_ctx["evaluator"]
        ev.highest_prices["005930"] = 80000
        ev.trailing_activated.add("005930")

        sell_ctx["market_repo"].get_current_price = AsyncMock(
            return_value=_price(76000),
        )
        pos = Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=8.6, current_price=76000,
        )
        result = await ev.evaluate_sell(pos, unfilled_codes=set())
        assert result == "SOLD"


class TestHold:
    async def test_hold_when_no_conditions_met(self, sell_ctx):
        """손절/익절/트레일링/기간초과/데드크로스 없으면 HOLD."""
        ev = sell_ctx["evaluator"]
        sell_ctx["market_repo"].get_current_price = AsyncMock(
            return_value=_price(72000),
        )
        pos = Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=2.86, current_price=72000,
        )
        result = await ev.evaluate_sell(pos, unfilled_codes=set())
        assert result == "HOLD"
