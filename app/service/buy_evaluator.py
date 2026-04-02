from __future__ import annotations

import logging
from datetime import datetime

from app.config import constants as C
from app.config.settings import Settings
from app.config.stock_names import fmt as stock_fmt
from app.core.event_bus import BotEvent, EventBus, EventType
from app.model.domain import (
    AccountSummary, OrderReason, OrderType, Position,
)
from app.core.protocols import (
    MarketDataRepository,
    OrderLogRepository,
    OrderRepository,
    ReportRepository,
)
from app.service.indicator_service import IndicatorService
from app.service.sell_evaluator import stock_data

logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trading")


class BuyEvaluator:
    def __init__(
        self,
        settings: Settings,
        market_repo: MarketDataRepository,
        order_repo: OrderRepository,
        order_log_repo: OrderLogRepository,
        report_repo: ReportRepository,
        indicator: IndicatorService,
        event_bus: EventBus | None = None,
    ) -> None:
        self._settings = settings
        self._market = market_repo
        self._order = order_repo
        self._order_log = order_log_repo
        self._report = report_repo
        self._indicator = indicator
        self._event_bus = event_bus
        self.daily_buy_count: int = 0
        self.last_positions: list[Position] = []
        self.last_account_summary: AccountSummary | None = None

    def _emit(self, event_type: EventType, message: str, data: dict | None = None) -> None:
        if self._event_bus:
            self._event_bus.emit(BotEvent(type=event_type, message=message, data=data))

    async def evaluate_buy(
        self,
        stock_code: str,
        current_holding_count: int,
        holding_codes: set[str],
        unfilled_codes: set[str],
        market_ok: bool,
    ) -> str:
        name = stock_fmt(stock_code)

        if stock_code in holding_codes:
            return "SKIP"
        if stock_code in unfilled_codes:
            return "SKIP"
        if (self._settings.max_holding_count > 0
                and current_holding_count >= self._settings.max_holding_count):
            return "SKIP"
        if (self._settings.max_daily_buy_count > 0
                and self.daily_buy_count >= self._settings.max_daily_buy_count):
            return "SKIP"
        if not market_ok:
            return "SKIP"

        if self._settings.scalping_entry_minute > 0:
            now = datetime.now()
            market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now < market_open:
                return "SKIP"
            mins_since_open = (now - market_open).total_seconds() / 60
            if mins_since_open < self._settings.scalping_entry_minute:
                return "SKIP"

        price_data = await self._market.get_current_price(stock_code)

        if price_data.is_stopped or price_data.is_managed:
            return "SKIP"
        if price_data.is_caution or price_data.is_clearing:
            return "SKIP"
        if price_data.current_price <= 0:
            return "SKIP"
        if abs(price_data.change_rate) > C.ABNORMAL_CHANGE_RATE:
            return "SKIP"
        if price_data.current_price < C.MIN_STOCK_PRICE:
            return "SKIP"

        elapsed_ratio = self._indicator.market_elapsed_ratio()
        adj_value = int(C.MIN_TRADING_VALUE * elapsed_ratio)
        adj_volume = int(C.MIN_TRADING_VOLUME * elapsed_ratio)

        if price_data.trading_value < adj_value:
            return "SKIP"
        if price_data.volume < adj_volume:
            return "SKIP"
        if price_data.market_cap < C.MIN_MARKET_CAP:
            return "SKIP"
        if price_data.current_price == price_data.upper_limit:
            return "SKIP"

        cr = price_data.change_rate
        if cr < 0 or cr < self._settings.min_intraday_change:
            return "SKIP"
        if cr > self._settings.max_intraday_change:
            return "SKIP"

        last_sell = await self._order_log.get_last_sell_time(stock_code)
        if last_sell:
            hours_since = (datetime.now() - last_sell).total_seconds() / 3600
            if hours_since < self._settings.rebuy_cooldown_hours:
                return "SKIP"

        candles = await self._market.get_daily_chart(stock_code)
        if len(candles) < C.MA_LONG_PERIOD + self._settings.signal_lookback_days:
            return "SKIP"

        ma_result = self._indicator.calculate_ma(candles)

        ma5_now = ma_result.ma_short[0] if ma_result.ma_short else 0
        ma20_now = ma_result.ma_long[0] if ma_result.ma_long else 0
        rsi = self._indicator.calculate_rsi(candles)
        ma_data = stock_data(stock_code, price_data.current_price,
            ma5=round(ma5_now), ma20=round(ma20_now),
            rsi=round(rsi, 1) if rsi else None,
            change_rate=price_data.change_rate)

        if not self._indicator.check_scalping_entry(ma_result, price_data.current_price, rsi):
            return "SKIP"
        buy_reason = OrderReason.SCALPING_ENTRY

        if rsi is None:
            return "SKIP"
        if rsi < self._settings.rsi_scalping_min or rsi > self._settings.rsi_scalping_max:
            return "SKIP"

        price_with_fee = price_data.current_price * (1 + C.BUY_FEE_RATE)
        acct = self.last_account_summary or await self._order.get_account_summary()
        if acct and self.last_account_summary is None:
            self.last_account_summary = acct
        available = acct.total_cash if acct else 0
        if available <= 0:
            psbl = await self._order.get_available_cash(stock_code)
            if psbl > 0:
                available = psbl
                logger.info("잔고 0 → 매수가능금액 %s원 사용", f"{psbl:,}")
        if available <= 0:
            yesterday = await self._report.get_yesterday_report(datetime.now().strftime("%Y-%m-%d"))
            if yesterday and yesterday.get("total_cash", 0) >= price_with_fee:
                available = yesterday["total_cash"]
                logger.info("API 잔고 0 → 전일 리포트 total_cash %s원 사용", f"{available:,}")
        max_slots = (
            self._settings.max_holding_count
            if self._settings.max_holding_count > 0
            else max(5, len(self._settings.watch_list_codes))
        )
        current_holding = len(self.last_positions)
        empty_slots = max(1, max_slots - current_holding)
        invest_per_stock = int(available / empty_slots)
        if self._settings.max_investment_ratio < 1.0:
            invest_per_stock = min(invest_per_stock, int(available * self._settings.max_investment_ratio))
        quantity = int(invest_per_stock / price_with_fee)
        if quantity <= 0:
            if available >= price_with_fee:
                quantity = 1
            else:
                logger.warning(
                    "잔액부족: %s — 가용=%s원, 필요=%s원(1주+수수료), acct=%s",
                    stock_code, available, int(price_with_fee), acct,
                )
                self._emit(EventType.BUY_EVAL,
                    f"💰 {name} — 골든크로스 확인했으나 잔액 부족으로 매수 보류 (가용: {available:,}원)",
                    {**ma_data, "skip": "잔액부족", "available": available, "action": "매수보류"})
                return "SKIP"

        reason_label = {
            OrderReason.GOLDEN_CROSS: "골든크로스",
            OrderReason.UPTREND_ENTRY: "상승추세",
            OrderReason.MOMENTUM_ENTRY: "모멘텀 단타",
            OrderReason.SCALPING_ENTRY: "단타 (오르는 종목)",
        }.get(buy_reason, "매수")

        rsi_str = f", RSI:{rsi:.0f}" if rsi is not None else ""
        self._emit(EventType.BUY_EVAL,
            f"✨ {name} — {reason_label} 확인! 매수 주문 전송 중 "
            f"({quantity}주 × {price_data.current_price:,}원)",
            {**ma_data, "signal": reason_label, "action": "매수검토"})

        try:
            result = await self._order.execute_order(
                stock_code, OrderType.BUY, quantity, price=0,
            )
        except Exception as exc:
            logger.exception("매수 주문 실행 중 예외: %s %d주", stock_code, quantity)
            self._emit(EventType.ORDER_EXEC,
                f"❌ {name} 매수 주문 오류 — {type(exc).__name__}: {exc}",
                {"type": "BUY", "code": stock_code, "qty": quantity,
                 "price": price_data.current_price, "success": False,
                 "reason": buy_reason.value, "error": str(exc)})
            return "SKIP"

        await self._order_log.save_order(
            stock_code, OrderType.BUY, buy_reason,
            quantity, price_data.current_price, result, datetime.now().isoformat(),
        )
        if result.success:
            total = price_data.current_price * quantity
            new_pos = Position(
                stock_code=stock_code,
                quantity=quantity,
                avg_price=float(price_data.current_price),
                profit_rate=0.0,
                current_price=price_data.current_price,
            )
            if not any(p.stock_code == stock_code for p in self.last_positions):
                self.last_positions.append(new_pos)
            if self.last_account_summary:
                self.last_account_summary.total_cash = max(
                    0, self.last_account_summary.total_cash - total
                )
            self._emit(EventType.ORDER_EXEC,
                f"🎉 {name} 매수 완료! {quantity}주 × {price_data.current_price:,}원 = {total:,}원 "
                f"({reason_label})", {
                "type": "BUY", "code": stock_code, "qty": quantity,
                "price": price_data.current_price, "success": True,
                "reason": buy_reason.value, "order_no": result.order_no,
            })
        else:
            err_msg = result.error_message or "알 수 없는 오류"
            self._emit(EventType.ORDER_EXEC,
                f"❌ {name} 매수 주문 실패 — {err_msg}", {
                "type": "BUY", "code": stock_code, "qty": quantity,
                "price": price_data.current_price, "success": False,
                "reason": buy_reason.value, "error": err_msg,
            })
        trade_logger.info(
            "매수 %s: %s %d주 @ %d원 (%s)", "성공" if result.success else "실패",
            stock_code, quantity, price_data.current_price, buy_reason.value,
        )
        return "BOUGHT" if result.success else "SKIP"
