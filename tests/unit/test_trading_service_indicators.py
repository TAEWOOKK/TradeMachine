from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model.domain import DailyCandle, MaResult
from app.service.indicator_service import IndicatorService


@pytest.fixture
def service(mock_settings):
    return IndicatorService(settings=mock_settings)


def _make_candles(closes: list[int]) -> list[DailyCandle]:
    return [
        DailyCandle(
            date=f"2026{i:04d}",
            close=c,
            open=c - 50,
            high=c + 100,
            low=c - 100,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


# ── calculate_ma ──


class TestCalculateMa:
    def test_calculate_ma_basic(self, service):
        closes = [10000 + i * 10 for i in range(30)]
        candles = _make_candles(closes)
        result = service.calculate_ma(candles)

        assert len(result.ma_short) == 11
        assert len(result.ma_long) == 11

        expected_short_0 = sum(closes[:5]) / 5
        assert result.ma_short[0] == pytest.approx(expected_short_0)

        expected_long_0 = sum(closes[:20]) / 20
        assert result.ma_long[0] == pytest.approx(expected_long_0)

    def test_calculate_ma_insufficient_data(self, service):
        candles = _make_candles([10000] * 15)
        result = service.calculate_ma(candles)
        assert len(result.ma_short) == 0
        assert len(result.ma_long) == 0


# ── check_golden_cross ──


class TestCheckGoldenCross:
    def test_check_golden_cross_true(self, service):
        """Cross at day 4, confirmed for 4 days, price above MA20, MA20 rising."""
        ma = MaResult(
            ma_short=[110, 108, 106, 104, 102, 96, 94, 92],
            ma_long=[103, 102, 101, 100, 99, 99, 99, 99],
            candles=[],
        )
        assert service.check_golden_cross(ma, current_price=115) is True

    def test_check_golden_cross_false_no_cross(self, service):
        """MA(5) always below MA(20) — no crossover detected."""
        ma = MaResult(
            ma_short=[90, 88, 86, 84, 82, 80, 78, 76],
            ma_long=[100, 100, 100, 100, 100, 100, 100, 100],
            candles=[],
        )
        assert service.check_golden_cross(ma, current_price=115) is False

    def test_check_golden_cross_true_one_day_confirmed(self, service):
        """Cross at day 1, 1일 확인으로 통과 (완화된 조건)."""
        ma = MaResult(
            ma_short=[105, 100, 96, 94, 92, 90, 88, 86],
            ma_long=[100, 99, 99, 99, 99, 99, 99, 99],
            candles=[],
        )
        assert service.check_golden_cross(ma, current_price=115) is True

    def test_check_golden_cross_false_price_below_ma20(self, service):
        """Valid cross & confirmation, but current_price < MA(20)."""
        ma = MaResult(
            ma_short=[110, 108, 106, 104, 102, 96, 94, 92],
            ma_long=[103, 102, 101, 100, 99, 99, 99, 99],
            candles=[],
        )
        assert service.check_golden_cross(ma, current_price=90) is False

    def test_check_golden_cross_false_ma20_declining(self, service):
        """Valid cross & confirmation & price ok, but MA(20) is declining."""
        ma = MaResult(
            ma_short=[110, 108, 106, 104, 102, 96, 94, 92],
            ma_long=[99, 100, 101, 102, 98, 98, 98, 98],
            candles=[],
        )
        assert service.check_golden_cross(ma, current_price=115) is False


# ── calculate_rsi ──


class TestCalculateRsi:
    def test_calculate_rsi_bullish(self, service):
        """Monotonically decreasing closes (recent highest) → all gains → RSI ≈ 100."""
        closes = [15000 - i * 100 for i in range(15)]  # 15000, 14900, …, 13600
        candles = _make_candles(closes)
        rsi = service.calculate_rsi(candles)
        assert rsi == pytest.approx(100.0)

    def test_calculate_rsi_bearish(self, service):
        """Monotonically increasing closes (recent lowest) → all losses → RSI ≈ 0."""
        closes = [13600 + i * 100 for i in range(15)]  # 13600, 13700, …, 15000
        candles = _make_candles(closes)
        rsi = service.calculate_rsi(candles)
        assert rsi == pytest.approx(0.0)

    def test_calculate_rsi_balanced(self, service):
        """Alternating up/down with equal magnitude → RSI ≈ 50."""
        closes = [100 if i % 2 == 0 else 99 for i in range(15)]
        candles = _make_candles(closes)
        rsi = service.calculate_rsi(candles)
        assert rsi == pytest.approx(50.0)

    def test_calculate_rsi_insufficient_data(self, service):
        candles = _make_candles([10000] * 10)
        assert service.calculate_rsi(candles) is None


# ── check_volume_confirmation ──


class TestCheckVolumeConfirmation:
    def _candles_with_volume(self, cross_idx: int, cross_vol: int, base_vol: int, count: int = 25):
        return [
            DailyCandle(
                date=f"2026{i:04d}",
                close=10000,
                open=9950,
                high=10100,
                low=9900,
                volume=cross_vol if i == cross_idx else base_vol,
            )
            for i in range(count)
        ]

    def test_check_volume_confirmation_passes(self, service):
        candles = self._candles_with_volume(
            cross_idx=3, cross_vol=3_000_000, base_vol=1_000_000,
        )
        assert service.check_volume_confirmation(candles, 3) is True

    def test_check_volume_confirmation_fails(self, service):
        candles = self._candles_with_volume(
            cross_idx=3, cross_vol=1_000_000, base_vol=1_000_000,
        )
        assert service.check_volume_confirmation(candles, 3) is False


# ── count_business_days ──


class TestCountBusinessDays:
    def test_count_business_days(self, service):
        start = date(2026, 3, 9)   # Monday
        end = date(2026, 3, 16)    # Monday
        assert service.count_business_days(start, end) == 5

    def test_count_business_days_with_holidays(self, service):
        start = date(2026, 4, 27)  # Monday
        end = date(2026, 5, 4)     # Monday — 20260501 is KRX holiday
        assert service.count_business_days(start, end) == 4


# ── find_cross_day_index ──


class TestFindCrossDayIndex:
    def test_find_cross_day_index_found(self, service):
        """Cross at index 3: ma_short[4]<ma_long[4] and ma_short[3]>=ma_long[3]."""
        ma = MaResult(
            ma_short=[110, 108, 106, 104, 96, 94, 92, 90],
            ma_long=[100, 100, 100, 100, 100, 100, 100, 100],
            candles=[],
        )
        assert service.find_cross_day_index(ma) == 3

    def test_find_cross_day_index_not_found(self, service):
        ma = MaResult(
            ma_short=[90, 88, 86, 84, 82, 80, 78, 76],
            ma_long=[100, 100, 100, 100, 100, 100, 100, 100],
            candles=[],
        )
        assert service.find_cross_day_index(ma) is None


# ── check_dead_cross ──


class TestCheckDeadCross:
    @pytest.mark.asyncio
    async def test_check_dead_cross(self, service, sample_candles_dead_cross):
        market_repo = AsyncMock()
        market_repo.get_daily_chart = AsyncMock(
            return_value=sample_candles_dead_cross,
        )
        result = await service.check_dead_cross("005930", market_repo)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_dead_cross_insufficient_data(self, service):
        short_candles = _make_candles([10000] * 10)
        market_repo = AsyncMock()
        market_repo.get_daily_chart = AsyncMock(return_value=short_candles)
        result = await service.check_dead_cross("005930", market_repo)
        assert result is False
