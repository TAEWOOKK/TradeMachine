from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.config import constants as C
from app.config.settings import Settings
from app.config.stock_names import fmt as stock_fmt
from app.core.event_bus import BotEvent, EventBus, EventType
from app.model.domain import OrderReason, OrderType, Position
from app.core.protocols import (
    MarketDataRepository,
    OrderLogRepository,
    OrderRepository,
)
from app.service.indicator_service import IndicatorService
from app.service.market_guard import MarketGuard

logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trading")

_REASON_KR = {
    OrderReason.STOP_LOSS: "손절 (손실 한도 초과)",
    OrderReason.TAKE_PROFIT: "익절 (목표 수익 달성)",
    OrderReason.TRAILING_STOP: "트레일링 스탑 (고점 대비 하락)",
    OrderReason.MAX_HOLDING: "보유 기간 초과",
    OrderReason.FRIDAY_CLOSE: "장마감 전량 청산",
    OrderReason.EOD_CLOSE: "장마감 전량 청산",
    OrderReason.DEAD_CROSS: "하락 신호 (데드크로스)",
    OrderReason.GOLDEN_CROSS: "상승 신호 (골든크로스)",
    OrderReason.UPTREND_ENTRY: "기존 상승추세 진입",
    OrderReason.SCALPING_ENTRY: "단타 진입 (오르는 종목)",
}


def stock_data(code: str, price: int = 0, **extra: object) -> dict:
    from app.config.stock_names import get_name
    d: dict = {"code": code, "name": get_name(code)}
    if price:
        d["price"] = price
    d.update(extra)
    return d


class SellEvaluator:
    def __init__(
        self,
        settings: Settings,
        market_repo: MarketDataRepository,
        order_repo: OrderRepository,
        order_log_repo: OrderLogRepository,
        indicator: IndicatorService,
        market_guard: MarketGuard,
        event_bus: EventBus | None = None,
    ) -> None:
        self._settings = settings
        self._market = market_repo
        self._order = order_repo
        self._order_log = order_log_repo
        self._indicator = indicator
        self._market_guard = market_guard
        self._event_bus = event_bus
        self.highest_prices: dict[str, int] = {}
        self.trailing_activated: set[str] = set()

    def _emit(self, event_type: EventType, message: str, data: dict | None = None) -> None:
        if self._event_bus:
            self._event_bus.emit(BotEvent(type=event_type, message=message, data=data))

    async def evaluate_sell(self, pos: Position, unfilled_codes: set[str]) -> str:
        price_data = await self._market.get_current_price(pos.stock_code)
        name = stock_fmt(pos.stock_code)

        if price_data.current_price > 0:
            pos.current_price = price_data.current_price

        if price_data.current_price <= 0:
            return "SKIP"
        if price_data.is_stopped:
            self._emit(EventType.SELL_EVAL,
                f"⏸️ {name} — 거래 정지 상태라 매도 판단을 건너뜁니다",
                stock_data(pos.stock_code, price_data.current_price,
                    qty=pos.quantity, avg_price=int(pos.avg_price), skip="거래정지"))
            return "SKIP"
        if abs(price_data.change_rate) > C.ABNORMAL_CHANGE_RATE:
            self._emit(EventType.SELL_EVAL,
                f"⚠️ {name} — 가격이 비정상적으로 변동({price_data.change_rate:+.1f}%)해서 매도를 보류합니다",
                stock_data(pos.stock_code, price_data.current_price,
                    qty=pos.quantity, avg_price=int(pos.avg_price),
                    change_rate=round(price_data.change_rate, 1), skip="비정상변동"))
            return "SKIP"
        if pos.stock_code in unfilled_codes:
            return "SKIP"

        code = pos.stock_code
        cur = price_data.current_price
        real_profit = (
            (cur - pos.avg_price) / pos.avg_price * 100
            if pos.avg_price > 0 else pos.profit_rate
        )

        sell_base = dict(
            qty=pos.quantity, avg_price=int(pos.avg_price), profit=round(real_profit, 2),
        )

        # P0: 손절
        if real_profit <= self._settings.stop_loss_rate:
            sold = await self.execute_sell(pos, OrderReason.STOP_LOSS, cur)
            if sold:
                self._market_guard.record_stop_loss()
            emoji = "🔴" if sold else "❌"
            self._emit(EventType.SELL_EVAL,
                f"{emoji} {name} — 손실이 {real_profit:.1f}%에 달해 {'손절 매도했습니다' if sold else '손절 시도했으나 실패했어요'} "
                f"(기준: {self._settings.stop_loss_rate}%, 연속 손절 {self._market_guard.consecutive_stop_loss}회)",
                stock_data(code, cur, **sell_base, action="손절매도" if sold else "손절실패"))
            return "SOLD" if sold else "HOLD"

        # P1: 익절
        if real_profit >= self._settings.take_profit_rate:
            sold = await self.execute_sell(pos, OrderReason.TAKE_PROFIT, cur)
            if sold:
                self._market_guard.record_profit()
            emoji = "🟢" if sold else "❌"
            self._emit(EventType.SELL_EVAL,
                f"{emoji} {name} — 수익 {real_profit:.1f}% 달성! {'목표 수익에 도달해 매도했습니다' if sold else '매도 시도했으나 실패했어요'}",
                stock_data(code, cur, **sell_base, action="익절매도" if sold else "익절실패"))
            return "SOLD" if sold else "HOLD"

        # P2: 트레일링 스탑
        prev_highest = self.highest_prices.get(code, 0)
        new_highest = max(prev_highest, cur)
        self.highest_prices[code] = new_highest

        was_activated = code in self.trailing_activated
        if real_profit >= self._settings.trailing_stop_activate:
            self.trailing_activated.add(code)

        if new_highest != prev_highest or (not was_activated and code in self.trailing_activated):
            await self._order_log.save_trailing_state(
                code, new_highest, code in self.trailing_activated,
            )

        if code in self.trailing_activated:
            highest = self.highest_prices[code]
            threshold = highest * (1 - self._settings.trailing_stop_rate / 100)
            if cur <= threshold:
                sold = await self.execute_sell(pos, OrderReason.TRAILING_STOP, cur)
                if sold:
                    await self.cleanup_trailing(code)
                    if real_profit > 0:
                        self._market_guard.record_profit()
                emoji = "📉" if sold else "❌"
                self._emit(EventType.SELL_EVAL,
                    f"{emoji} {name} — 고점({highest:,}원) 대비 하락해서 "
                    f"{'트레일링 스탑 매도했습니다' if sold else '매도 시도 실패'} (현재 {cur:,}원)",
                    stock_data(code, cur, **sell_base,
                        highest=highest, action="트레일링매도" if sold else "트레일링실패"))
                return "SOLD" if sold else "HOLD"

        # P3: 보유 기간 초과
        buy_date = await self._order_log.get_first_buy_date(code)
        if buy_date:
            biz_days = self._indicator.count_business_days(
                datetime.strptime(buy_date, "%Y-%m-%d").date(),
                datetime.now().date(),
            )
            if biz_days > self._settings.max_holding_days:
                sold = await self.execute_sell(pos, OrderReason.MAX_HOLDING, cur)
                if sold:
                    await self.cleanup_trailing(code)
                emoji = "📅" if sold else "❌"
                self._emit(EventType.SELL_EVAL,
                    f"{emoji} {name} — {biz_days}일째 보유 중 (한도: {self._settings.max_holding_days}일). "
                    f"{'기간 초과로 매도했습니다' if sold else '매도 시도 실패'}",
                    stock_data(code, cur, **sell_base,
                        days_held=biz_days, action="기간초과매도" if sold else "기간초과실패"))
                return "SOLD" if sold else "HOLD"

        # P4: 데드크로스
        if await self._indicator.check_dead_cross(pos.stock_code, self._market):
            sold = await self.execute_sell(pos, OrderReason.DEAD_CROSS, cur)
            if sold:
                await self.cleanup_trailing(code)
            emoji = "📉" if sold else "❌"
            self._emit(EventType.SELL_EVAL,
                f"{emoji} {name} — 하락 신호(데드크로스) 감지. "
                f"{'매도 완료' if sold else '매도 시도 실패'}",
                stock_data(code, cur, **sell_base,
                    signal="데드크로스", action="매도완료" if sold else "매도실패"))
            return "SOLD" if sold else "HOLD"

        return "HOLD"

    async def execute_sell(self, pos: Position, reason: OrderReason, price: int = 0) -> bool:
        result = await self._order.execute_order(
            pos.stock_code, OrderType.SELL, pos.quantity,
        )
        if not result.success and reason in (OrderReason.STOP_LOSS, OrderReason.EOD_CLOSE):
            await asyncio.sleep(1)
            result = await self._order.execute_order(
                pos.stock_code, OrderType.SELL, pos.quantity,
            )

        await self._order_log.save_order(
            pos.stock_code, OrderType.SELL, reason,
            pos.quantity, price, result, datetime.now().isoformat(),
        )
        name = stock_fmt(pos.stock_code)
        reason_kr = _REASON_KR.get(reason, reason.value)
        if result.success:
            total = price * pos.quantity
            self._emit(EventType.ORDER_EXEC,
                f"💰 {name} 매도 완료 — {pos.quantity}주 × {price:,}원 = {total:,}원 ({reason_kr})", {
                "type": "SELL", "code": pos.stock_code, "qty": pos.quantity,
                "reason": reason.value, "price": price, "success": True,
            })
        else:
            self._emit(EventType.ORDER_EXEC,
                f"❌ {name} 매도 실패 — {result.error_message or '알 수 없는 오류'} ({reason_kr})", {
                "type": "SELL", "code": pos.stock_code, "qty": pos.quantity,
                "reason": reason.value, "price": price, "success": False,
            })
        trade_logger.info(
            "매도 %s: %s %d주 (%s)", "성공" if result.success else "실패",
            pos.stock_code, pos.quantity, reason.value,
        )
        return result.success

    async def cleanup_trailing(self, code: str) -> None:
        self.highest_prices.pop(code, None)
        self.trailing_activated.discard(code)
        await self._order_log.delete_trailing_state(code)
