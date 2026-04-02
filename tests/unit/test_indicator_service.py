from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

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


class TestCalculateMa:
    def test_calculate_ma_basic(self, service):
        closes = [100 + i for i in range(30)]
        candles = _make_candles(closes)
        result = service.calculate_ma(candles)

        expected_ma5_0 = sum(closes[:5]) / 5
        expected_ma20_0 = sum(closes[:20]) / 20
        assert result.ma_short[0] == pytest.approx(expected_ma5_0)
        assert result.ma_long[0] == pytest.approx(expected_ma20_0)
        assert len(result.ma_short) > 0
        assert len(result.ma_long) > 0


class TestCalculateRsi:
    def test_calculate_rsi_overbought(self, service):
        """최근 가격이 계속 상승 → RSI > 70 (과매수)."""
        closes = [15000 - i * 50 for i in range(15)]
        candles = _make_candles(closes)
        rsi = service.calculate_rsi(candles)
        assert rsi is not None
        assert rsi > 70

    def test_calculate_rsi_oversold(self, service):
        """최근 가격이 계속 하락 → RSI < 30 (과매도)."""
        closes = [10000 + i * 50 for i in range(15)]
        candles = _make_candles(closes)
        rsi = service.calculate_rsi(candles)
        assert rsi is not None
        assert rsi < 30


class TestCheckScalpingEntry:
    def test_check_scalping_entry_passes(self, service):
        """MA5 > MA20 3일 연속 + 현재가 >= MA5 → True."""
        ma = MaResult(
            ma_short=[110, 108, 106, 104],
            ma_long=[100, 100, 100, 100],
            candles=[],
        )
        assert service.check_scalping_entry(ma, current_price=115, rsi=55.0) is True

    def test_check_scalping_entry_fails_ma(self, service):
        """MA5 < MA20 → False."""
        ma = MaResult(
            ma_short=[90, 88, 86, 84],
            ma_long=[100, 100, 100, 100],
            candles=[],
        )
        assert service.check_scalping_entry(ma, current_price=95, rsi=55.0) is False


class TestMarketElapsedRatio:
    def test_market_elapsed_ratio_midday(self):
        """12:15 → 약 0.5 (장 중간)."""
        fake_now = datetime(2026, 4, 1, 12, 15, 0)
        with patch("app.service.indicator_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ratio = IndicatorService.market_elapsed_ratio()
        assert 0.4 < ratio < 0.6

    def test_market_elapsed_ratio_before_open(self):
        """08:00 → 장 시작 전이므로 최솟값 0.05."""
        fake_now = datetime(2026, 4, 1, 8, 0, 0)
        with patch("app.service.indicator_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ratio = IndicatorService.market_elapsed_ratio()
        assert ratio == pytest.approx(0.05)

    def test_market_elapsed_ratio_after_close(self):
        """16:00 → 장 종료 후이므로 최댓값 1.0."""
        fake_now = datetime(2026, 4, 1, 16, 0, 0)
        with patch("app.service.indicator_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            ratio = IndicatorService.market_elapsed_ratio()
        assert ratio == pytest.approx(1.0)
