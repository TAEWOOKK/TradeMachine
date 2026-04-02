from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import constants as C
from app.config.settings import Settings
from app.model.domain import DailyCandle, MaResult


class IndicatorService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def calculate_ma(self, candles: list[DailyCandle]) -> MaResult:
        closes = [c.close for c in candles]
        short = C.MA_SHORT_PERIOD
        long = C.MA_LONG_PERIOD
        ma_short: list[float] = []
        ma_long: list[float] = []
        for i in range(len(closes) - long + 1):
            if i + short <= len(closes):
                ma_short.append(sum(closes[i:i + short]) / short)
            if i + long <= len(closes):
                ma_long.append(sum(closes[i:i + long]) / long)
        return MaResult(ma_short=ma_short, ma_long=ma_long, candles=candles)

    def check_golden_cross(self, ma: MaResult, current_price: int) -> bool:
        if len(ma.ma_short) < self._settings.signal_lookback_days + 1:
            return False
        if len(ma.ma_long) < self._settings.signal_lookback_days + 1:
            return False
        cross_day = -1
        for i in range(self._settings.signal_lookback_days):
            if (ma.ma_short[i + 1] < ma.ma_long[i + 1]
                    and ma.ma_short[i] >= ma.ma_long[i]):
                cross_day = i
                break
        if cross_day < 0:
            return False
        if cross_day < 1:
            return False
        for j in range(cross_day):
            if ma.ma_short[j] <= ma.ma_long[j]:
                return False
        if current_price <= ma.ma_long[0]:
            return False
        if len(ma.ma_long) > 3 and ma.ma_long[0] <= ma.ma_long[3]:
            return False
        return True

    async def check_dead_cross(self, stock_code: str, market_repo: object) -> bool:
        candles = await market_repo.get_daily_chart(stock_code, days=30)
        if len(candles) < C.MA_LONG_PERIOD:
            return False
        ma = self.calculate_ma(candles)
        sell_lookback = 5
        cross_day = -1
        for i in range(sell_lookback):
            if (i + 1 < len(ma.ma_short) and i + 1 < len(ma.ma_long)
                    and ma.ma_short[i + 1] > ma.ma_long[i + 1]
                    and ma.ma_short[i] <= ma.ma_long[i]):
                cross_day = i
                break
        if cross_day < 0:
            return False
        if cross_day < self._settings.sell_confirm_days:
            return False
        for j in range(cross_day):
            if ma.ma_short[j] >= ma.ma_long[j]:
                return False
        return True

    def find_cross_day_index(self, ma: MaResult) -> int | None:
        for i in range(self._settings.signal_lookback_days):
            if (i + 1 < len(ma.ma_short) and i + 1 < len(ma.ma_long)
                    and ma.ma_short[i + 1] < ma.ma_long[i + 1]
                    and ma.ma_short[i] >= ma.ma_long[i]):
                return i
        return None

    def check_existing_uptrend(
        self, ma: MaResult, candles: list[DailyCandle], current_price: int,
    ) -> bool:
        """상승추세 종목: 이미 올라가고 있는 종목만.

        조건:
        1. MA5 > MA20이 최근 3일 연속 유지
        2. 현재가 >= MA5 (모멘텀)
        3. MA20 상승 추세
        4. 괴리율 7% 이내
        """
        if len(ma.ma_short) < 5 or len(ma.ma_long) < 5:
            return False
        for i in range(3):
            if ma.ma_short[i] <= ma.ma_long[i]:
                return False
        if current_price < ma.ma_short[0]:
            return False
        if len(ma.ma_long) > 3 and ma.ma_long[0] <= ma.ma_long[3]:
            return False
        gap_pct = (ma.ma_short[0] - ma.ma_long[0]) / ma.ma_long[0] * 100
        if gap_pct > 7.0:
            return False
        return True

    def check_momentum_entry(
        self, ma: MaResult, current_price: int, rsi: float | None,
    ) -> bool:
        """모멘텀 단타: 이미 올라가고 있는 종목만 매수.

        조건:
        1. MA5 > MA20 (상승 추세)
        2. 현재가 >= MA5 (단기 평균 위 = 모멘텀)
        3. MA5 상승 중 (3일 전보다 높음)
        4. 괴리율 7% 이내 (너무 많이 오르지 않음)
        5. RSI 45-70 (약한 종목 제외, 과열 제외)
        """
        if len(ma.ma_short) < 5 or len(ma.ma_long) < 5:
            return False
        if ma.ma_short[0] <= ma.ma_long[0]:
            return False
        if current_price < ma.ma_short[0]:
            return False
        if len(ma.ma_short) >= 4 and ma.ma_short[0] <= ma.ma_short[3]:
            return False
        gap_pct = (ma.ma_short[0] - ma.ma_long[0]) / ma.ma_long[0] * 100
        if gap_pct > 7.0:
            return False
        if rsi is not None and (rsi < 45 or rsi > 70):
            return False
        return True

    def check_scalping_entry(
        self, ma: MaResult, current_price: int, rsi: float | None,
    ) -> bool:
        """단타 진입: 오르는 종목 + 상승 추세.

        조건 (change_rate 필터는 호출 전에 적용됨):
        1. MA5 > MA20 최근 3일 연속 유지
        2. 현재가 >= MA5 (모멘텀)
        3. RSI는 호출 후 별도 체크 (50~65)
        """
        if len(ma.ma_short) < 4 or len(ma.ma_long) < 4:
            return False
        for i in range(3):
            if ma.ma_short[i] <= ma.ma_long[i]:
                return False
        if current_price < ma.ma_short[0]:
            return False
        return True

    def calculate_rsi(self, candles: list[DailyCandle]) -> float | None:
        period = C.RSI_PERIOD
        if len(candles) < period + 1:
            return None
        gains: list[float] = []
        losses: list[float] = []
        for i in range(period):
            diff = candles[i].close - candles[i + 1].close
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def check_volume_confirmation(self, candles: list[DailyCandle], cross_day_index: int) -> bool:
        if cross_day_index >= len(candles):
            return False
        cross_volume = candles[cross_day_index].volume
        vol_period = min(20, len(candles) - 1)
        if vol_period < 5:
            return False
        avg_volume = sum(c.volume for c in candles[1:vol_period + 1]) / vol_period
        if avg_volume <= 0:
            return False
        return cross_volume >= avg_volume * self._settings.volume_confirm_ratio

    @staticmethod
    def market_elapsed_ratio() -> float:
        """장 시작(09:00)~종료(15:30) 중 현재 경과 비율. 최소 0.05(5%)."""
        now = datetime.now()
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        total = (market_close - market_open).total_seconds()
        elapsed = (now - market_open).total_seconds()
        return max(0.05, min(1.0, elapsed / total))

    def count_business_days(self, start: date, end: date) -> int:
        count = 0
        current = start
        while current < end:
            current += timedelta(days=1)
            if current.weekday() < 5 and current.strftime("%Y%m%d") not in C.KRX_HOLIDAYS:
                count += 1
        return count
