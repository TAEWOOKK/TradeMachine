from __future__ import annotations

import asyncio
import logging
import time as time_mod
from datetime import date, datetime, time as dt_time, timedelta

from app.config import constants as C
from app.config.settings import Settings
from app.config.stock_names import fmt as stock_fmt
from app.core.event_bus import BotEvent, EventBus, EventType
from app.model.domain import (
    DailyCandle, MaResult, OrderReason, OrderResult, OrderType, Position, ScanResult,
)
from app.repository.kis_auth_repository import KisAuthRepository
from app.repository.market_data_repository import MarketDataRepository
from app.repository.order_log_repository import OrderLogRepository
from app.repository.order_repository import OrderRepository
from app.repository.report_repository import ReportRepository

logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trading")

_REASON_KR = {
    OrderReason.STOP_LOSS: "손절 (손실 한도 초과)",
    OrderReason.TAKE_PROFIT: "익절 (목표 수익 달성)",
    OrderReason.TRAILING_STOP: "트레일링 스탑 (고점 대비 하락)",
    OrderReason.MAX_HOLDING: "보유 기간 초과",
    OrderReason.FRIDAY_CLOSE: "장마감 전량 청산 (레거시)",
    OrderReason.EOD_CLOSE: "장마감 전량 청산",
    OrderReason.DEAD_CROSS: "하락 신호 (데드크로스)",
    OrderReason.GOLDEN_CROSS: "상승 신호 (골든크로스)",
    OrderReason.UPTREND_ENTRY: "기존 상승추세 진입",
    OrderReason.SCALPING_ENTRY: "단타 진입 (오르는 종목)",
}


class TradingService:
    def __init__(
        self,
        auth_repo: KisAuthRepository,
        market_repo: MarketDataRepository,
        order_repo: OrderRepository,
        order_log_repo: OrderLogRepository,
        report_repo: ReportRepository,
        settings: Settings,
        event_bus: EventBus | None = None,
    ) -> None:
        self._auth = auth_repo
        self._market = market_repo
        self._order = order_repo
        self._order_log = order_log_repo
        self._report = report_repo
        self._settings = settings
        self._event_bus = event_bus
        self._consecutive_failures: int = 0
        self._daily_buy_count: int = 0
        self._scan_running: bool = False
        self._highest_prices: dict[str, int] = {}
        self._trailing_activated: set[str] = set()
        self._last_scan_result: ScanResult | None = None
        self._last_positions: list[Position] = []
        self._last_account_summary: dict[str, int] | None = None
        self._phase: str = "IDLE"

    def _emit(self, event_type: EventType, message: str, data: dict | None = None) -> None:
        if self._event_bus:
            self._event_bus.emit(BotEvent(type=event_type, message=message, data=data))

    # ── 상태 조회 프로퍼티 ──

    @property
    def status(self) -> dict:
        now = datetime.now()
        in_market = (
            now.weekday() < 5
            and now.strftime("%Y%m%d") not in C.KRX_HOLIDAYS
            and dt_time(9, 0) <= now.time() <= dt_time(15, 30)
        )
        acct = self._last_account_summary or {}
        return {
            "phase": self._phase,
            "scan_running": self._scan_running,
            "in_market_hours": in_market,
            "daily_buy_count": self._daily_buy_count,
            "max_daily_buy": self._settings.max_daily_buy_count,
            "consecutive_failures": self._consecutive_failures,
            "trailing_count": len(self._trailing_activated),
            "watch_list": self._settings.watch_list_codes,
            "paper_trading": self._settings.kis_is_paper_trading,
            "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "total_cash": acct.get("total_cash", 0),
            "total_assets": acct.get("total_assets", 0),
            "stock_eval": acct.get("stock_eval", 0),
        }

    @property
    def last_scan(self) -> ScanResult | None:
        return self._last_scan_result

    @property
    def positions(self) -> list[Position]:
        return self._last_positions

    async def refresh_holdings(self) -> None:
        """잔고·계좌 요약을 KIS API에서 다시 조회해 캐시를 갱신. 수동 매수/매도 후 호출."""
        try:
            positions = await self._order.get_balance()
        except Exception:
            logger.warning("잔고 갱신 실패 — 기존 캐시 유지")
            return
        self._last_positions = positions or []
        holding_codes = {p.stock_code for p in self._last_positions}
        self._highest_prices = {
            code: price for code, price in self._highest_prices.items()
            if code in holding_codes
        }
        self._trailing_activated &= holding_codes
        try:
            self._last_account_summary = await self._order.get_account_summary()
        except Exception:
            pass
        today = datetime.now().strftime("%Y-%m-%d")
        counts = await self._order_log.get_today_counts(today)
        self._daily_buy_count = counts["buy_count"]

    async def _backfill_missing_reports(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        last_report = await self._report.get_yesterday_report(
            "9999-12-31",
        )
        if not last_report:
            return

        last_date = datetime.strptime(last_report["report_date"], "%Y-%m-%d").date()
        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        gap_days = (today_date - last_date).days

        if gap_days <= 1:
            return

        logger.info("빠진 일일 리포트 %d일 감지 — 보정 시작 (%s ~ %s)", gap_days - 1, last_date, today_date)
        prev = last_report

        for offset in range(1, gap_days):
            fill_date = (last_date + timedelta(days=offset)).strftime("%Y-%m-%d")
            existing = await self._report.get_yesterday_report(
                (last_date + timedelta(days=offset + 1)).strftime("%Y-%m-%d"),
            )
            if existing and existing["report_date"] == fill_date:
                prev = existing
                continue

            realized = await self._order_log.get_today_realized_pnl(fill_date)
            counts = await self._order_log.get_today_counts(fill_date)
            prev_assets = prev.get("total_assets", 0)
            prev_cumulative = prev.get("cumulative_pnl", 0)
            daily_pnl = realized["total_pnl"]

            await self._report.save_daily_report(
                report_date=fill_date,
                buy_count=counts["buy_count"],
                sell_count=counts["sell_count"],
                unfilled=counts["fail_count"],
                holding_count=0,
                eval_amount=0,
                eval_profit=0,
                profit_rate=0.0,
                total_cash=prev_assets + daily_pnl,
                total_assets=prev_assets + daily_pnl,
                deposit_withdrawal=0,
                cumulative_pnl=prev_cumulative + daily_pnl,
            )
            prev = {
                "total_assets": prev_assets + daily_pnl,
                "total_cash": prev_assets + daily_pnl,
                "cumulative_pnl": prev_cumulative + daily_pnl,
            }
            logger.info("리포트 보정 완료: %s (실현손익: %d원)", fill_date, daily_pnl)

    async def recover_state(self) -> None:
        self._phase = "RECOVERING"
        self._emit(EventType.STATE_CHANGE, "상태 복구 중...")

        await self._backfill_missing_reports()

        today = datetime.now().strftime("%Y-%m-%d")
        counts = await self._order_log.get_today_counts(today)
        self._daily_buy_count = counts["buy_count"]

        try:
            positions = await self._order.get_balance()
        except Exception:
            logger.warning("상태 복구 중 잔고 조회 실패 — 빈 상태로 시작")
            positions = None

        self._last_positions = positions or []
        holding_codes = {p.stock_code for p in self._last_positions}

        saved_states = await self._order_log.load_trailing_states()
        db_highest: dict[str, int] = {}
        for st in saved_states:
            code = st["stock_code"]
            if code in holding_codes:
                db_highest[code] = st["highest_price"]
                if st["activated"]:
                    self._trailing_activated.add(code)

        for pos in self._last_positions:
            current = pos.current_price
            saved = db_highest.get(pos.stock_code, 0)
            self._highest_prices[pos.stock_code] = max(current, saved)
            if pos.profit_rate >= self._settings.trailing_stop_activate:
                self._trailing_activated.add(pos.stock_code)

        await self._order_log.cleanup_trailing_states(holding_codes)

        try:
            self._last_account_summary = await self._order.get_account_summary()
        except Exception:
            logger.warning("상태 복구 중 계좌 요약 조회 실패")
        self._phase = "IDLE"

        trailing_info = ""
        if self._trailing_activated:
            n = len(self._trailing_activated)
            if n <= 2:
                codes = ", ".join(
                    f"{stock_fmt(c)}(고점:{self._highest_prices.get(c, 0):,}원)"
                    for c in self._trailing_activated
                )
                trailing_info = f" | 트레일링: {codes}"
            else:
                trailing_info = f" | 트레일링 스탑 {n}종목 활성"

        holding = len(self._last_positions)
        max_buy = self._settings.max_daily_buy_count
        buy_info = f" | 오늘 매수 {self._daily_buy_count}/{max_buy}건" if max_buy > 0 and self._daily_buy_count > 0 else ""

        if holding > 0:
            names = ", ".join(stock_fmt(p.stock_code) for p in self._last_positions)
            msg = f"상태 복구 완료. 보유 {holding}종목: {names}{trailing_info}{buy_info}"
        else:
            msg = f"상태 복구 완료. 보유 종목 없음{buy_info}"
        trade_logger.info(msg)
        self._emit(EventType.STATE_CHANGE, msg)

    # ── run_scan ──

    async def run_scan(self) -> None:
        if self._scan_running:
            return
        self._scan_running = True
        self._phase = "SCANNING"

        scan_time = datetime.now().isoformat()
        start = time_mod.time()
        self._market.reset_api_count()

        positions: list[Position] = []
        sell_count = buy_count = skip_count = error_count = 0

        try:
            now = datetime.now()
            if now.weekday() >= 5:
                self._phase = "HOLIDAY"
                return
            if now.strftime("%Y%m%d") in C.KRX_HOLIDAYS:
                self._phase = "HOLIDAY"
                return

            if self._consecutive_failures >= C.MAX_CONSECUTIVE_FAILURES:
                logger.warning("연속 %d회 실패, 스캔 SKIP", self._consecutive_failures)
                self._phase = "FAILURE_SKIP"
                self._emit(EventType.ERROR,
                    f"⚠️ API 연결에 {self._consecutive_failures}번 연속 실패해서 분석을 일시 중단했어요. "
                    "장 시작 전 준비 때 자동으로 다시 시도합니다.")
                return

            current_time = now.time()
            if current_time < dt_time(9, 5):
                self._phase = "WAITING_MARKET"
                self._emit(EventType.SCAN_START,
                    "⏰ 장이 열렸지만 시초가 변동이 큰 시간이라 09:05부터 분석을 시작합니다")
                return
            if current_time > dt_time(15, 25):
                self._phase = "MARKET_CLOSED"
                return
            buy_allowed = current_time <= dt_time(15, 20)

            time_str = now.strftime("%H:%M")
            watch_count = len(self._settings.watch_list_codes)
            if buy_allowed:
                self._emit(EventType.SCAN_START,
                    f"🔍 {time_str} 종목 분석 시작 — 감시 {watch_count}종목 체크, 매수 가능 시간대")
            else:
                self._emit(EventType.SCAN_START,
                    f"🔍 {time_str} 종목 분석 시작 — 장 마감 임박으로 매도만 확인합니다")

            positions = await self._order.get_balance()
            if positions is None:
                self._consecutive_failures += 1
                self._emit(EventType.ERROR,
                    f"⚠️ 잔고 조회에 실패했어요 (연속 {self._consecutive_failures}회). "
                    "증권사 서버 상태를 확인 중입니다.")
                return

            self._last_positions = positions
            if self._last_account_summary is None or len(positions) > 0:
                try:
                    self._last_account_summary = await self._order.get_account_summary()
                except Exception:
                    pass
            unfilled_codes = await self._cleanup_unfilled_orders()

            # ── 매도 평가 ──
            for pos in positions:
                try:
                    result = await self._evaluate_sell(pos, unfilled_codes)
                    if result == "SOLD":
                        sell_count += 1
                    elif result == "SKIP":
                        skip_count += 1
                except Exception as exc:
                    logger.exception("매도 판단 중 오류: %s", pos.stock_code)
                    error_count += 1
                    name = stock_fmt(pos.stock_code)
                    self._emit(EventType.ERROR,
                        f"❌ {name} 매도 판단 중 오류 — {type(exc).__name__}: {exc}",
                        self._stock_data(pos.stock_code))

            if sell_count > 0:
                refreshed = await self._order.get_balance()
                if refreshed is not None:
                    positions = refreshed
                    self._last_positions = positions
                try:
                    self._last_account_summary = await self._order.get_account_summary()
                except Exception:
                    pass

            # ── 매수 평가 ──
            if buy_allowed:
                holding_codes = {p.stock_code for p in positions}
                current_holding = len(positions)

                market_ok = await self._check_market_condition()
                if not market_ok:
                    self._emit(EventType.BUY_EVAL,
                        "📉 시장 전체가 하락 추세라서 오늘은 새로운 매수를 하지 않습니다 (KOSPI < 20일 평균)")

                if buy_allowed and self._daily_buy_count >= self._settings.max_daily_buy_count:
                    self._emit(EventType.BUY_EVAL,
                        f"⏸️ 오늘 매수 한도({self._daily_buy_count}/{self._settings.max_daily_buy_count}건)를 모두 사용했어요")
                    buy_allowed = False
                if (self._settings.max_holding_count > 0
                        and buy_allowed and current_holding >= self._settings.max_holding_count):
                    self._emit(EventType.BUY_EVAL,
                        f"⏸️ 최대 보유 종목 수({self._settings.max_holding_count}개)에 도달했어요")
                    buy_allowed = False

                for code in self._settings.watch_list_codes:
                    try:
                        result = await self._evaluate_buy(
                            code, current_holding, holding_codes,
                            unfilled_codes, market_ok,
                        )
                        if result == "BOUGHT":
                            buy_count += 1
                            self._daily_buy_count += 1
                            current_holding += 1
                            holding_codes.add(code)
                        elif result == "SKIP":
                            skip_count += 1
                    except Exception as exc:
                        logger.exception("매수 판단 중 오류: %s", code)
                        error_count += 1
                        name = stock_fmt(code)
                        self._emit(EventType.ERROR,
                            f"❌ {name} 매수 판단 중 오류 — {type(exc).__name__}: {exc}",
                            self._stock_data(code))

            if buy_count > 0:
                refreshed = await self._order.get_balance()
                if refreshed is not None:
                    positions = refreshed
                    self._last_positions = positions
                try:
                    self._last_account_summary = await self._order.get_account_summary()
                except Exception:
                    pass

            self._consecutive_failures = 0

        except Exception:
            logger.exception("스캔 사이클 전체 오류")
            error_count += 1
            self._consecutive_failures += 1
            self._emit(EventType.ERROR,
                f"⚠️ 분석 중 예상치 못한 오류가 발생했어요 (연속 {self._consecutive_failures}회)")

        finally:
            self._scan_running = False
            if self._phase == "SCANNING":
                self._phase = "IDLE"

        elapsed = int((time_mod.time() - start) * 1000)
        scan_result = ScanResult(
            scan_time=scan_time,
            holding_count=len(positions),
            sell_count=sell_count,
            buy_count=buy_count,
            skip_count=skip_count,
            error_count=error_count,
            api_call_count=self._market.api_call_count,
            elapsed_ms=elapsed,
        )
        self._last_scan_result = scan_result

        parts = []
        if len(positions) > 0:
            parts.append(f"보유 {len(positions)}종목 확인")
        if sell_count > 0:
            parts.append(f"{sell_count}건 매도")
        if buy_count > 0:
            parts.append(f"{buy_count}건 매수")
        if error_count > 0:
            parts.append(f"오류 {error_count}건")
        if not parts:
            parts.append("매수/매도 조건에 해당하는 종목이 없었어요")

        summary = "✅ 분석 완료 — " + ", ".join(parts)
        sec = elapsed / 1000
        summary += f" ({sec:.1f}초 소요)"

        trade_logger.info(
            "스캔 완료 — 보유:%d 매도:%d 매수:%d 스킵:%d 오류:%d (%dms)",
            scan_result.holding_count, sell_count, buy_count,
            skip_count, error_count, elapsed,
        )
        self._emit(EventType.SCAN_END, summary, {
            "holding": scan_result.holding_count, "sell": sell_count,
            "buy": buy_count, "skip": skip_count, "error": error_count,
            "elapsed_ms": elapsed, "api_calls": scan_result.api_call_count,
        })
        await self._report.save_scan_log(scan_result)

    # ── _evaluate_sell ──

    async def _evaluate_sell(self, pos: Position, unfilled_codes: set[str]) -> str:
        price_data = await self._market.get_current_price(pos.stock_code)
        name = stock_fmt(pos.stock_code)

        if price_data.current_price > 0:
            pos.current_price = price_data.current_price

        if price_data.current_price <= 0:
            return "SKIP"
        if price_data.is_stopped:
            self._emit(EventType.SELL_EVAL,
                f"⏸️ {name} — 거래 정지 상태라 매도 판단을 건너뜁니다",
                self._stock_data(pos.stock_code, price_data.current_price,
                    qty=pos.quantity, avg_price=int(pos.avg_price), skip="거래정지"))
            return "SKIP"
        if abs(price_data.change_rate) > C.ABNORMAL_CHANGE_RATE:
            self._emit(EventType.SELL_EVAL,
                f"⚠️ {name} — 가격이 비정상적으로 변동({price_data.change_rate:+.1f}%)해서 매도를 보류합니다",
                self._stock_data(pos.stock_code, price_data.current_price,
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
            sold = await self._execute_sell(pos, OrderReason.STOP_LOSS, cur)
            emoji = "🔴" if sold else "❌"
            self._emit(EventType.SELL_EVAL,
                f"{emoji} {name} — 손실이 {real_profit:.1f}%에 달해 {'손절 매도했습니다' if sold else '손절 시도했으나 실패했어요'} "
                f"(기준: {self._settings.stop_loss_rate}%)",
                self._stock_data(code, cur, **sell_base, action="손절매도" if sold else "손절실패"))
            return "SOLD" if sold else "HOLD"

        # P1: 익절
        if real_profit >= self._settings.take_profit_rate:
            sold = await self._execute_sell(pos, OrderReason.TAKE_PROFIT, cur)
            emoji = "🟢" if sold else "❌"
            self._emit(EventType.SELL_EVAL,
                f"{emoji} {name} — 수익 {real_profit:.1f}% 달성! {'목표 수익에 도달해 매도했습니다' if sold else '매도 시도했으나 실패했어요'}",
                self._stock_data(code, cur, **sell_base, action="익절매도" if sold else "익절실패"))
            return "SOLD" if sold else "HOLD"

        # P2: 트레일링 스탑
        prev_highest = self._highest_prices.get(code, 0)
        new_highest = max(prev_highest, cur)
        self._highest_prices[code] = new_highest

        was_activated = code in self._trailing_activated
        if real_profit >= self._settings.trailing_stop_activate:
            self._trailing_activated.add(code)

        if new_highest != prev_highest or (not was_activated and code in self._trailing_activated):
            await self._order_log.save_trailing_state(
                code, new_highest, code in self._trailing_activated,
            )

        if code in self._trailing_activated:
            highest = self._highest_prices[code]
            threshold = highest * (1 - self._settings.trailing_stop_rate / 100)
            if cur <= threshold:
                sold = await self._execute_sell(pos, OrderReason.TRAILING_STOP, cur)
                if sold:
                    await self._cleanup_trailing(code)
                emoji = "📉" if sold else "❌"
                self._emit(EventType.SELL_EVAL,
                    f"{emoji} {name} — 고점({highest:,}원) 대비 하락해서 "
                    f"{'트레일링 스탑 매도했습니다' if sold else '매도 시도 실패'} (현재 {cur:,}원)",
                    self._stock_data(code, cur, **sell_base,
                        highest=highest, action="트레일링매도" if sold else "트레일링실패"))
                return "SOLD" if sold else "HOLD"

        # P3: 보유 기간 초과
        buy_date = await self._order_log.get_first_buy_date(code)
        if buy_date:
            biz_days = self._count_business_days(
                datetime.strptime(buy_date, "%Y-%m-%d").date(),
                datetime.now().date(),
            )
            if biz_days > self._settings.max_holding_days:
                sold = await self._execute_sell(pos, OrderReason.MAX_HOLDING, cur)
                if sold:
                    await self._cleanup_trailing(code)
                emoji = "📅" if sold else "❌"
                self._emit(EventType.SELL_EVAL,
                    f"{emoji} {name} — {biz_days}일째 보유 중 (한도: {self._settings.max_holding_days}일). "
                    f"{'기간 초과로 매도했습니다' if sold else '매도 시도 실패'}",
                    self._stock_data(code, cur, **sell_base,
                        days_held=biz_days, action="기간초과매도" if sold else "기간초과실패"))
                return "SOLD" if sold else "HOLD"

        # P4: 데드크로스
        if await self._check_dead_cross(pos.stock_code):
            sold = await self._execute_sell(pos, OrderReason.DEAD_CROSS, cur)
            if sold:
                await self._cleanup_trailing(code)
            emoji = "📉" if sold else "❌"
            self._emit(EventType.SELL_EVAL,
                f"{emoji} {name} — 하락 신호(데드크로스) 감지. "
                f"{'매도 완료' if sold else '매도 시도 실패'}",
                self._stock_data(code, cur, **sell_base,
                    signal="데드크로스", action="매도완료" if sold else "매도실패"))
            return "SOLD" if sold else "HOLD"

        # 매도 조건 없음 → 계속 보유 (로그 생략)
        return "HOLD"

    # ── _evaluate_buy ──

    def _stock_data(self, code: str, price: int = 0, **extra: object) -> dict:
        from app.config.stock_names import get_name
        d: dict = {"code": code, "name": get_name(code)}
        if price:
            d["price"] = price
        d.update(extra)
        return d

    async def _evaluate_buy(
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
        if self._daily_buy_count >= self._settings.max_daily_buy_count:
            return "SKIP"
        if not market_ok:
            return "SKIP"

        # 단타: 장 초반 매수 보류 (변동성 완화 대기)
        if self._settings.scalping_entry_minute > 0:
            now = datetime.now()
            market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now < market_open:
                return "SKIP"
            mins_since_open = (now - market_open).total_seconds() / 60
            if mins_since_open < self._settings.scalping_entry_minute:
                self._emit(EventType.BUY_EVAL,
                    f"⏰ {stock_fmt(stock_code)} — 장 시작 후 {self._settings.scalping_entry_minute}분 경과 전이라 매수 보류",
                    self._stock_data(stock_code, 0, skip="장초반대기"))
                return "SKIP"

        price_data = await self._market.get_current_price(stock_code)

        if price_data.is_stopped or price_data.is_managed:
            self._emit(EventType.BUY_EVAL,
                f"🚫 {name} — 거래정지 또는 관리종목이라 매수 대상에서 제외합니다",
                self._stock_data(stock_code, price_data.current_price, skip="관리종목/거래정지"))
            return "SKIP"
        if price_data.is_caution or price_data.is_clearing:
            self._emit(EventType.BUY_EVAL,
                f"🚫 {name} — 투자유의/정리매매 종목이라 매수하지 않습니다",
                self._stock_data(stock_code, price_data.current_price, skip="투자유의/정리매매"))
            return "SKIP"
        if price_data.current_price <= 0:
            return "SKIP"
        if abs(price_data.change_rate) > C.ABNORMAL_CHANGE_RATE:
            self._emit(EventType.BUY_EVAL,
                f"⚠️ {name} — 가격 변동이 비정상적({price_data.change_rate:+.1f}%)이라 매수를 보류합니다",
                self._stock_data(stock_code, price_data.current_price, skip="비정상변동", change_rate=price_data.change_rate))
            return "SKIP"

        if price_data.current_price < C.MIN_STOCK_PRICE:
            self._emit(EventType.BUY_EVAL,
                f"📊 {name} ({price_data.current_price:,}원) — 최소 주가 미달로 제외",
                self._stock_data(stock_code, price_data.current_price, skip="최소주가미달"))
            return "SKIP"
        elapsed_ratio = self._market_elapsed_ratio()
        adj_value = int(C.MIN_TRADING_VALUE * elapsed_ratio)
        adj_volume = int(C.MIN_TRADING_VOLUME * elapsed_ratio)

        if price_data.trading_value < adj_value:
            self._emit(EventType.BUY_EVAL,
                f"📊 {name} ({price_data.current_price:,}원) — 거래대금 부족으로 제외",
                self._stock_data(stock_code, price_data.current_price, skip="거래대금부족"))
            return "SKIP"
        if price_data.volume < adj_volume:
            self._emit(EventType.BUY_EVAL,
                f"📊 {name} ({price_data.current_price:,}원) — 거래량 부족으로 제외",
                self._stock_data(stock_code, price_data.current_price, skip="거래량부족"))
            return "SKIP"
        if price_data.market_cap < C.MIN_MARKET_CAP:
            self._emit(EventType.BUY_EVAL,
                f"📊 {name} ({price_data.current_price:,}원) — 시가총액 미달로 제외",
                self._stock_data(stock_code, price_data.current_price, skip="시총미달"))
            return "SKIP"
        if price_data.current_price == price_data.upper_limit:
            self._emit(EventType.BUY_EVAL,
                f"⚠️ {name} — 상한가에 도달해 매수하지 않습니다",
                self._stock_data(stock_code, price_data.current_price, skip="상한가"))
            return "SKIP"

        # 단타: 전일 종가 대비 등락률(prdy_ctrt) — 하락 제외, 소폭 상승~과열 전 구간만
        cr = price_data.change_rate
        if cr < 0:
            self._emit(EventType.BUY_EVAL,
                f"📉 {name} — 전일대비 {cr:+.1f}% 하락 중이라 매수 보류",
                self._stock_data(stock_code, price_data.current_price, skip="하락중", change_rate=cr))
            return "SKIP"
        if cr < self._settings.min_intraday_change:
            self._emit(EventType.BUY_EVAL,
                f"📉 {name} — 전일대비 {cr:+.1f}%로 상승 미흡 (최소 {self._settings.min_intraday_change}%)",
                self._stock_data(stock_code, price_data.current_price, skip="상승미흡", change_rate=cr))
            return "SKIP"
        if cr > self._settings.max_intraday_change:
            self._emit(EventType.BUY_EVAL,
                f"🔥 {name} — 전일대비 {cr:+.1f}%로 이미 과열 (최대 {self._settings.max_intraday_change}%)",
                self._stock_data(stock_code, price_data.current_price, skip="과열", change_rate=cr))
            return "SKIP"

        last_sell = await self._order_log.get_last_sell_time(stock_code)
        if last_sell:
            hours_since = (datetime.now() - last_sell).total_seconds() / 3600
            if hours_since < self._settings.rebuy_cooldown_hours:
                remaining = self._settings.rebuy_cooldown_hours - hours_since
                self._emit(EventType.BUY_EVAL,
                    f"⏳ {name} — 최근에 매도한 종목이라 {remaining:.0f}시간 후 재매수 가능합니다",
                    self._stock_data(stock_code, price_data.current_price, skip="재매수쿨다운", remaining_hours=round(remaining, 1)))
                return "SKIP"

        candles = await self._market.get_daily_chart(stock_code)
        if len(candles) < C.MA_LONG_PERIOD + self._settings.signal_lookback_days:
            return "SKIP"

        ma_result = self._calculate_ma(candles)

        ma5_now = ma_result.ma_short[0] if ma_result.ma_short else 0
        ma20_now = ma_result.ma_long[0] if ma_result.ma_long else 0
        rsi = self._calculate_rsi(candles)
        ma_data = self._stock_data(stock_code, price_data.current_price,
            ma5=round(ma5_now), ma20=round(ma20_now),
            rsi=round(rsi, 1) if rsi else None,
            change_rate=price_data.change_rate)

        # 단타: SCALPING_ENTRY만 사용 (오르는 종목 + 상승 추세 + RSI)
        if self._check_scalping_entry(ma_result, price_data.current_price, rsi):
            buy_reason = OrderReason.SCALPING_ENTRY
        else:
            rel = ">" if ma5_now > ma20_now else "<"
            gap_pct = round((ma5_now - ma20_now) / ma20_now * 100, 2) if ma20_now > 0 else 0
            self._emit(EventType.BUY_EVAL,
                f"📊 {name} ({price_data.current_price:,}원) — 단타 조건 미충족 "
                f"(MA5:{ma5_now:,.0f} {rel} MA20:{ma20_now:,.0f}, 괴리율:{gap_pct:+.2f}%, RSI:{(f'{rsi:.0f}' if rsi is not None else '-')})",
                {**ma_data, "skip": "단타조건미충족", "ma_gap_pct": gap_pct})
            return "SKIP"

        # RSI 범위 확인 (단타: 50~65, RSI 없으면 SKIP)
        if rsi is None:
            self._emit(EventType.BUY_EVAL,
                f"📊 {name} — RSI 계산 불가 (데이터 부족)로 매수 보류",
                {**ma_data, "skip": "RSI불가"})
            return "SKIP"
        if rsi < self._settings.rsi_scalping_min:
            self._emit(EventType.BUY_EVAL,
                f"❄️ {name} — RSI {rsi:.0f}로 모멘텀 부족 (최소 {self._settings.rsi_scalping_min})",
                {**ma_data, "skip": "RSI부족", "rsi": rsi})
            return "SKIP"
        if rsi > self._settings.rsi_scalping_max:
            self._emit(EventType.BUY_EVAL,
                f"🔥 {name} — RSI {rsi:.0f}로 과열 (최대 {self._settings.rsi_scalping_max})",
                {**ma_data, "skip": "RSI과열", "rsi": rsi})
            return "SKIP"

        price_with_fee = price_data.current_price * (1 + C.BUY_FEE_RATE)
        acct = self._last_account_summary or await self._order.get_account_summary()
        if acct and self._last_account_summary is None:
            self._last_account_summary = acct
        available = acct.get("total_cash", 0) if acct else 0
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
        n_slots = (
            self._settings.max_holding_count
            if self._settings.max_holding_count > 0
            else max(5, len(self._settings.watch_list_codes))
        )
        invest_per_stock = int(available / n_slots)
        if self._settings.max_investment_ratio < 1.0:
            invest_per_stock = min(invest_per_stock, int(available * self._settings.max_investment_ratio))
        quantity = int(invest_per_stock / price_with_fee)
        if quantity <= 0:
            if available >= price_with_fee:
                quantity = 1
            else:
                logger.warning(
                    "잔액부족: %s — 가용=%s원, 필요=%s원(1주+수수료), acct=%s",
                    stock_code, available, int(price_with_fee),
                    {k: v for k, v in (acct or {}).items()},
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

    # ── market condition check ──

    async def _check_market_condition(self) -> bool:
        if not self._settings.enable_market_filter:
            return True
        try:
            kospi_data = await self._market.get_index_price("0001")
            kospi_candles = await self._market.get_daily_chart("0001", days=25)
            if len(kospi_candles) >= 20:
                kospi_ma20 = sum(c.close for c in kospi_candles[:20]) / 20
                if kospi_data.current_price < kospi_ma20:
                    return False
        except Exception:
            logger.warning("KOSPI 시장 필터 조회 실패 — 매수 허용으로 기본값 적용")
        return True

    # ── indicators ──

    def _calculate_ma(self, candles: list[DailyCandle]) -> MaResult:
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

    def _check_golden_cross(self, ma: MaResult, current_price: int) -> bool:
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

    async def _check_dead_cross(self, stock_code: str) -> bool:
        candles = await self._market.get_daily_chart(stock_code, days=30)
        if len(candles) < C.MA_LONG_PERIOD:
            return False
        ma = self._calculate_ma(candles)
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

    def _find_cross_day_index(self, ma: MaResult) -> int | None:
        for i in range(self._settings.signal_lookback_days):
            if (i + 1 < len(ma.ma_short) and i + 1 < len(ma.ma_long)
                    and ma.ma_short[i + 1] < ma.ma_long[i + 1]
                    and ma.ma_short[i] >= ma.ma_long[i]):
                return i
        return None

    def _check_existing_uptrend(
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

    def _check_momentum_entry(
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

    def _check_scalping_entry(
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

    def _calculate_rsi(self, candles: list[DailyCandle]) -> float | None:
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

    def _check_volume_confirmation(self, candles: list[DailyCandle], cross_day_index: int) -> bool:
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

    async def _cleanup_trailing(self, code: str) -> None:
        self._highest_prices.pop(code, None)
        self._trailing_activated.discard(code)
        await self._order_log.delete_trailing_state(code)

    @staticmethod
    def _market_elapsed_ratio() -> float:
        """장 시작(09:00)~종료(15:30) 중 현재 경과 비율. 최소 0.05(5%)."""
        now = datetime.now()
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        total = (market_close - market_open).total_seconds()
        elapsed = (now - market_open).total_seconds()
        return max(0.05, min(1.0, elapsed / total))

    def _count_business_days(self, start: date, end: date) -> int:
        count = 0
        current = start
        while current < end:
            current += timedelta(days=1)
            if current.weekday() < 5 and current.strftime("%Y%m%d") not in C.KRX_HOLIDAYS:
                count += 1
        return count

    # ── _execute_sell ──

    async def _execute_sell(self, pos: Position, reason: OrderReason, price: int = 0) -> bool:
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

    # ── run_pre_market ──

    async def run_pre_market(self) -> None:
        try:
            if datetime.now().strftime("%Y%m%d") in C.KRX_HOLIDAYS:
                return
            self._phase = "PRE_MARKET"
            self._emit(EventType.PRE_MARKET, "🌅 장 시작 준비 중... 토큰 갱신, 잔고 확인, 미체결 정리")

            await self._auth.get_token()
            self._daily_buy_count = 0
            self._consecutive_failures = 0

            positions = await self._order.get_balance()
            holding_codes = {p.stock_code for p in positions} if positions else set()
            self._last_positions = positions or []
            self._highest_prices = {
                code: price for code, price in self._highest_prices.items()
                if code in holding_codes
            }
            self._trailing_activated &= holding_codes
            await self._order_log.cleanup_trailing_states(holding_codes)
            await self._cleanup_unfilled_orders()

            try:
                self._last_account_summary = await self._order.get_account_summary()
            except Exception:
                pass

            if holding_codes:
                names = ", ".join(stock_fmt(c) for c in holding_codes)
                msg = f"🌅 장 시작 준비 완료! 보유 {len(holding_codes)}종목: {names}"
            else:
                msg = "🌅 장 시작 준비 완료! 보유 종목 없음 — 새로운 매수 기회를 탐색합니다"
            trade_logger.info(msg)
            self._emit(EventType.PRE_MARKET, msg)
            self._phase = "WAITING_MARKET"
        except Exception:
            logger.exception("run_pre_market 오류")
            self._emit(EventType.ERROR, "⚠️ 장 시작 준비 중 오류 발생")
            self._phase = "WAITING_MARKET"

    # ── run_eod_close (매 영업일 장마감 전량 청산) ──

    async def run_eod_close(self) -> None:
        """매 영업일 15:28 보유 종목 전량 청산. 야간·익일 시가 갭 리스크 회피."""
        if not self._settings.eod_close_enabled:
            return
        now = datetime.now()
        today_ymd = now.strftime("%Y%m%d")
        if today_ymd in C.KRX_HOLIDAYS:
            return

        self._phase = "EOD_CLOSE"
        self._emit(EventType.PRE_MARKET, "🌆 장마감 임박 — 야간 갭 방지 전량 청산 중...")

        positions = await self._order.get_balance() or []
        if not positions:
            msg = "🌆 장마감 청산 완료 — 보유 종목 없음"
            trade_logger.info(msg)
            self._emit(EventType.PRE_MARKET, msg)
            self._phase = "IDLE"
            return

        sell_count = 0
        for pos in positions:
            try:
                price_data = await self._market.get_current_price(pos.stock_code)
                cur = price_data.current_price if price_data.current_price > 0 else int(pos.avg_price)
                sold = await self._execute_sell(pos, OrderReason.EOD_CLOSE, cur)
                if sold:
                    sell_count += 1
                    await self._cleanup_trailing(pos.stock_code)
            except Exception:
                logger.exception("장마감 청산 중 %s 매도 오류", pos.stock_code)

        if sell_count > 0:
            await self.refresh_holdings()

        msg = f"🌆 장마감 청산 완료 — {sell_count}/{len(positions)}종목 매도 (익일 갭 리스크 방지)"
        trade_logger.info(msg)
        self._emit(EventType.PRE_MARKET, msg, {"sold": sell_count, "total": len(positions)})
        self._phase = "IDLE"

    async def run_friday_close(self) -> None:
        """[레거시 호환] run_eod_close와 동일."""
        await self.run_eod_close()

    # ── run_post_market ──

    async def run_post_market(self) -> None:
        try:
            if datetime.now().strftime("%Y%m%d") in C.KRX_HOLIDAYS:
                return
            self._phase = "POST_MARKET"
            self._emit(EventType.POST_MARKET, "🌙 장 마감 — 오늘 거래 결산 중...")

            today = datetime.now().strftime("%Y-%m-%d")
            positions = await self._order.get_balance() or []
            self._last_positions = positions
            await self._cleanup_unfilled_orders()

            total_eval = sum(p.current_price * p.quantity for p in positions)
            total_cost = sum(p.avg_price * p.quantity for p in positions)
            total_profit = total_eval - total_cost
            rate = (total_profit / total_cost * 100) if total_cost > 0 else 0.0

            counts = await self._order_log.get_today_counts(today)

            account = await self._order.get_account_summary()
            total_cash = account["total_cash"] if account else 0
            total_assets = account["total_assets"] if account else total_eval

            yesterday = await self._report.get_yesterday_report(today)
            deposit_withdrawal = 0
            if yesterday and yesterday.get("total_assets", 0) > 0:
                prev_assets = yesterday["total_assets"]
                asset_change = total_assets - prev_assets
                trading_pnl = int(total_profit) - yesterday.get("eval_profit", 0)
                deposit_withdrawal = asset_change - trading_pnl
                if abs(deposit_withdrawal) < 1000:
                    deposit_withdrawal = 0

            prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
            realized = await self._order_log.get_today_realized_pnl(today)
            cumulative_pnl = prev_cumulative + realized["total_pnl"]

            if deposit_withdrawal != 0:
                dw_type = "입금" if deposit_withdrawal > 0 else "출금"
                await self._report.save_capital_event(
                    today, deposit_withdrawal,
                    f"자동 감지: {dw_type} {abs(deposit_withdrawal):,}원",
                )
                logger.info("입출금 자동 감지: %s %s원", dw_type, f"{abs(deposit_withdrawal):,}")

            await self._report.save_daily_report(
                report_date=today,
                buy_count=counts["buy_count"],
                sell_count=counts["sell_count"],
                unfilled=counts["fail_count"],
                holding_count=len(positions),
                eval_amount=total_eval,
                eval_profit=int(total_profit),
                profit_rate=round(rate, 2),
                total_cash=total_cash,
                total_assets=total_assets,
                deposit_withdrawal=deposit_withdrawal,
                cumulative_pnl=cumulative_pnl,
            )
            await self._report.save_balance_snapshot(today, positions)

            profit_sign = "+" if total_profit >= 0 else ""
            parts = [f"보유 {len(positions)}종목"]
            if counts["buy_count"]:
                parts.append(f"매수 {counts['buy_count']}건")
            if counts["sell_count"]:
                parts.append(f"매도 {counts['sell_count']}건")
            parts.append(f"총자산 {total_assets:,}원")
            if total_eval > 0:
                parts.append(f"손익 {profit_sign}{int(total_profit):,}원 ({profit_sign}{rate:.2f}%)")
            if deposit_withdrawal != 0:
                dw_sign = "+" if deposit_withdrawal > 0 else ""
                parts.append(f"입출금 {dw_sign}{deposit_withdrawal:,}원")

            msg = "🌙 오늘 거래 결산 완료 — " + ", ".join(parts)
            trade_logger.info(msg)
            self._emit(EventType.POST_MARKET, msg, {
                "holding": len(positions), "eval": total_eval,
                "profit": int(total_profit), "rate": round(rate, 2),
                "total_cash": total_cash, "total_assets": total_assets,
                "cumulative_pnl": cumulative_pnl,
            })

            deleted = await self._report.cleanup_old_scan_logs(days=90)
            if deleted:
                logger.info("90일 이전 스캔 로그 %d건 정리 완료", deleted)
            evt_deleted = await self._report.cleanup_old_bot_events(days=30)
            if evt_deleted:
                logger.info("30일 이전 이벤트 로그 %d건 정리 완료", evt_deleted)

            self._phase = "MARKET_CLOSED"
        except Exception:
            logger.exception("run_post_market 오류")
            self._emit(EventType.ERROR, "⚠️ 장 마감 처리 중 오류 발생")
            self._phase = "MARKET_CLOSED"

    # ── _cleanup_unfilled_orders ──

    async def _cleanup_unfilled_orders(self) -> set[str]:
        unfilled = await self._order.get_unfilled_orders()
        unfilled_codes: set[str] = set()
        for order in unfilled:
            if order["side"] == "BUY":
                await self._order.cancel_order(
                    order["order_no"], order["quantity"],
                )
            unfilled_codes.add(order["stock_code"])
        return unfilled_codes
