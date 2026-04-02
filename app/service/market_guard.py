from __future__ import annotations

import logging

from app.config import constants as C
from app.config.settings import Settings
from app.model.domain import Position
from app.core.protocols import MarketDataRepository

logger = logging.getLogger(__name__)


class MarketGuard:
    def __init__(self, settings: Settings, market_repo: MarketDataRepository) -> None:
        self._settings = settings
        self._market = market_repo
        self._consecutive_stop_loss: int = 0

    async def check_buy_allowed(self) -> tuple[bool, str]:
        """KOSPI 시장 조건 확인. (allowed, block_reason) 반환."""
        if self._consecutive_stop_loss >= C.CONSECUTIVE_STOP_LOSS_LIMIT:
            return False, f"연속 손절 {self._consecutive_stop_loss}회 도달"
        if not self._settings.enable_market_filter:
            return True, ""
        try:
            kospi_data = await self._market.get_index_price("0001")

            if kospi_data.change_rate <= C.KOSPI_BUY_BLOCK_RATE:
                return False, "시장 하락 추세 (KOSPI 기준)"

            kospi_candles = await self._market.get_daily_chart("0001", days=25)
            if len(kospi_candles) >= 20:
                kospi_ma20 = sum(c.close for c in kospi_candles[:20]) / 20
                if kospi_data.current_price < kospi_ma20:
                    return False, "시장 하락 추세 (KOSPI 기준)"
        except Exception:
            logger.warning("KOSPI 시장 필터 조회 실패 — 매수 허용으로 기본값 적용")
        return True, ""

    async def check_emergency_sell(self, positions: list[Position]) -> tuple[bool, float]:
        """KOSPI 급락 여부 확인. (is_emergency, change_rate) 반환."""
        if not positions:
            return False, 0.0
        try:
            kospi = await self._market.get_index_price("0001")
            if kospi.change_rate <= C.KOSPI_EMERGENCY_RATE:
                return True, kospi.change_rate
        except Exception:
            logger.warning("KOSPI 긴급 체크 실패 — 일반 매도 평가로 진행")
        return False, 0.0

    @property
    def consecutive_stop_loss(self) -> int:
        return self._consecutive_stop_loss

    @consecutive_stop_loss.setter
    def consecutive_stop_loss(self, value: int) -> None:
        self._consecutive_stop_loss = value

    def record_stop_loss(self) -> None:
        self._consecutive_stop_loss += 1

    def record_profit(self) -> None:
        self._consecutive_stop_loss = 0

    def reset_daily(self) -> None:
        self._consecutive_stop_loss = 0
