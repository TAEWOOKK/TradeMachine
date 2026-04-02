from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model.domain import Position, StockPrice
from app.service.market_guard import MarketGuard


def _make_index(change_rate: float, current_price: int = 2500) -> StockPrice:
    return StockPrice(
        stock_code="0001",
        current_price=current_price,
        upper_limit=0,
        lower_limit=0,
        change_rate=change_rate,
        volume=0,
        trading_value=0,
        market_cap=0,
        is_stopped=False,
        is_managed=False,
        is_caution=False,
        is_clearing=False,
    )


@pytest.fixture
def market_repo():
    return AsyncMock()


@pytest.fixture
def guard(mock_settings, market_repo):
    return MarketGuard(settings=mock_settings, market_repo=market_repo)


class TestCheckBuyAllowed:
    async def test_buy_allowed_normal_market(self, guard, market_repo):
        """KOSPI 정상 → (True, '')."""
        market_repo.get_index_price = AsyncMock(return_value=_make_index(0.5))
        market_repo.get_daily_chart = AsyncMock(return_value=[])
        allowed, reason = await guard.check_buy_allowed()
        assert allowed is True
        assert reason == ""

    async def test_buy_blocked_kospi_down(self, guard, market_repo):
        """KOSPI -1.5% → (False, reason)."""
        market_repo.get_index_price = AsyncMock(return_value=_make_index(-1.5))
        allowed, reason = await guard.check_buy_allowed()
        assert allowed is False
        assert reason != ""

    async def test_buy_blocked_consecutive_stop_loss(self, guard, market_repo):
        """연속 손절 2회 → (False, reason)."""
        guard.record_stop_loss()
        guard.record_stop_loss()
        allowed, reason = await guard.check_buy_allowed()
        assert allowed is False
        assert "연속 손절" in reason


class TestRecordAndReset:
    def test_record_profit_resets_counter(self, guard):
        """수익 기록 시 연속 손절 카운터 0으로 리셋."""
        guard.record_stop_loss()
        guard.record_stop_loss()
        assert guard.consecutive_stop_loss == 2
        guard.record_profit()
        assert guard.consecutive_stop_loss == 0

    def test_reset_daily(self, guard):
        """일일 리셋 시 연속 손절 카운터 0으로 초기화."""
        guard.record_stop_loss()
        guard.record_stop_loss()
        guard.reset_daily()
        assert guard.consecutive_stop_loss == 0


class TestCheckEmergencySell:
    async def test_emergency_sell_detected(self, guard, market_repo):
        """KOSPI -2% → (True, -2.0)."""
        market_repo.get_index_price = AsyncMock(return_value=_make_index(-2.0))
        positions = [
            Position(stock_code="005930", quantity=10, avg_price=70000.0,
                     profit_rate=0.0, current_price=70000),
        ]
        is_emergency, rate = await guard.check_emergency_sell(positions)
        assert is_emergency is True
        assert rate <= -1.5

    async def test_no_emergency_normal_market(self, guard, market_repo):
        """KOSPI -0.5% → (False, 0.0)."""
        market_repo.get_index_price = AsyncMock(return_value=_make_index(-0.5))
        positions = [
            Position(stock_code="005930", quantity=10, avg_price=70000.0,
                     profit_rate=0.0, current_price=70000),
        ]
        is_emergency, rate = await guard.check_emergency_sell(positions)
        assert is_emergency is False
