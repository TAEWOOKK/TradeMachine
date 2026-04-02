from __future__ import annotations

import asyncio
import logging
import time as time_mod
from datetime import datetime, time as dt_time, timedelta

from app.config import constants as C
from app.config.settings import Settings
from app.config.stock_names import fmt as stock_fmt
from app.core.event_bus import BotEvent, EventBus, EventType
from app.model.domain import (
    AccountSummary, OrderReason, Position, ScanResult,
)
from app.core.protocols import (
    AuthRepository,
    MarketDataRepository,
    OrderLogRepository,
    OrderRepository,
    ReportRepository,
)
from app.service.buy_evaluator import BuyEvaluator
from app.service.indicator_service import IndicatorService
from app.service.market_guard import MarketGuard
from app.service.sell_evaluator import SellEvaluator, stock_data

logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trading")


class TradingService:
    def __init__(
        self,
        auth_repo: AuthRepository,
        market_repo: MarketDataRepository,
        order_repo: OrderRepository,
        order_log_repo: OrderLogRepository,
        report_repo: ReportRepository,
        settings: Settings,
        event_bus: EventBus | None = None,
        *,
        indicator: IndicatorService | None = None,
        market_guard: MarketGuard | None = None,
        sell_evaluator: SellEvaluator | None = None,
        buy_evaluator: BuyEvaluator | None = None,
    ) -> None:
        self._auth = auth_repo
        self._market = market_repo
        self._order = order_repo
        self._order_log = order_log_repo
        self._report = report_repo
        self._settings = settings
        self._event_bus = event_bus
        self._consecutive_failures: int = 0
        self._scan_running: bool = False
        self._last_scan_result: ScanResult | None = None
        self._last_positions: list[Position] = []
        self._last_account_summary: AccountSummary | None = None
        self._phase: str = "IDLE"

        self.indicator = indicator or IndicatorService(settings)
        self.market_guard = market_guard or MarketGuard(settings, market_repo)
        self.sell_evaluator = sell_evaluator or SellEvaluator(
            settings, market_repo, order_repo, order_log_repo,
            self.indicator, self.market_guard, event_bus,
        )
        self.buy_evaluator = buy_evaluator or BuyEvaluator(
            settings, market_repo, order_repo, order_log_repo,
            report_repo, self.indicator, event_bus,
        )

    # ── proxy properties for sub-service state ──

    @property
    def _highest_prices(self) -> dict[str, int]:
        return self.sell_evaluator.highest_prices

    @_highest_prices.setter
    def _highest_prices(self, value: dict[str, int]) -> None:
        self.sell_evaluator.highest_prices = value

    @property
    def _trailing_activated(self) -> set[str]:
        return self.sell_evaluator.trailing_activated

    @_trailing_activated.setter
    def _trailing_activated(self, value: set[str]) -> None:
        self.sell_evaluator.trailing_activated = value

    @property
    def _consecutive_stop_loss(self) -> int:
        return self.market_guard.consecutive_stop_loss

    @_consecutive_stop_loss.setter
    def _consecutive_stop_loss(self, value: int) -> None:
        self.market_guard.consecutive_stop_loss = value

    @property
    def _daily_buy_count(self) -> int:
        return self.buy_evaluator.daily_buy_count

    @_daily_buy_count.setter
    def _daily_buy_count(self, value: int) -> None:
        self.buy_evaluator.daily_buy_count = value

    def _emit(self, event_type: EventType, message: str, data: dict | None = None) -> None:
        if self._event_bus:
            self._event_bus.emit(BotEvent(type=event_type, message=message, data=data))

    # ── backward-compatible delegate methods ──

    async def _evaluate_sell(self, pos: Position, unfilled_codes: set[str]) -> str:
        return await self.sell_evaluator.evaluate_sell(pos, unfilled_codes)

    async def _evaluate_buy(
        self,
        stock_code: str,
        current_holding_count: int,
        holding_codes: set[str],
        unfilled_codes: set[str],
        market_ok: bool,
    ) -> str:
        self.buy_evaluator.daily_buy_count = self._daily_buy_count
        self.buy_evaluator.last_positions = self._last_positions
        self.buy_evaluator.last_account_summary = self._last_account_summary
        result = await self.buy_evaluator.evaluate_buy(
            stock_code, current_holding_count, holding_codes, unfilled_codes,
            market_ok,
        )
        self._last_account_summary = self.buy_evaluator.last_account_summary
        self._last_positions = self.buy_evaluator.last_positions
        return result

    def _calculate_ma(self, candles):
        return self.indicator.calculate_ma(candles)

    def _calculate_rsi(self, candles):
        return self.indicator.calculate_rsi(candles)

    def _find_cross_day_index(self, ma):
        return self.indicator.find_cross_day_index(ma)

    def _check_golden_cross(self, ma, current_price):
        return self.indicator.check_golden_cross(ma, current_price)

    def _check_scalping_entry(self, ma, current_price, rsi):
        return self.indicator.check_scalping_entry(ma, current_price, rsi)

    def _check_existing_uptrend(self, ma, candles, current_price):
        return self.indicator.check_existing_uptrend(ma, candles, current_price)

    def _check_momentum_entry(self, ma, current_price, rsi):
        return self.indicator.check_momentum_entry(ma, current_price, rsi)

    def _check_volume_confirmation(self, candles, cross_day_index):
        return self.indicator.check_volume_confirmation(candles, cross_day_index)

    async def _check_dead_cross(self, stock_code):
        return await self.indicator.check_dead_cross(stock_code, self._market)

    async def _execute_sell(self, pos, reason, price=0):
        return await self.sell_evaluator.execute_sell(pos, reason, price)

    async def _cleanup_trailing(self, code):
        await self.sell_evaluator.cleanup_trailing(code)

    @staticmethod
    def _market_elapsed_ratio():
        return IndicatorService.market_elapsed_ratio()

    def _count_business_days(self, start, end):
        return self.indicator.count_business_days(start, end)

    # ── 상태 조회 프로퍼티 ──

    @property
    def status(self) -> dict:
        now = datetime.now()
        in_market = (
            now.weekday() < 5
            and now.strftime("%Y%m%d") not in C.KRX_HOLIDAYS
            and dt_time(C.MARKET_OPEN_HOUR, C.MARKET_OPEN_MINUTE) <= now.time() <= dt_time(C.MARKET_CLOSE_HOUR, C.MARKET_CLOSE_MINUTE)
        )
        acct = self._last_account_summary
        return {
            "phase": self._phase,
            "scan_running": self._scan_running,
            "in_market_hours": in_market,
            "daily_buy_count": self._daily_buy_count,
            "max_daily_buy": self._settings.max_daily_buy_count,
            "max_holding": self._settings.max_holding_count,
            "consecutive_failures": self._consecutive_failures,
            "trailing_count": len(self._trailing_activated),
            "watch_list": self._settings.watch_list_codes,
            "paper_trading": self._settings.kis_is_paper_trading,
            "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "total_cash": acct.total_cash if acct else 0,
            "total_assets": acct.total_assets if acct else 0,
            "stock_eval": acct.stock_eval if acct else 0,
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
        self._daily_buy_count = counts.buy_count

    async def _refresh_holdings_after_eod_sells(self, initial_count: int, sell_count: int) -> None:
        """매도 직후 KIS 잔고 API가 이전 보유 수량을 반환하는 경우가 있어 재조회한다."""
        target_max = max(0, initial_count - sell_count)
        for attempt in range(6):
            await self.refresh_holdings()
            if len(self._last_positions) <= target_max:
                return
            if attempt < 5:
                await asyncio.sleep(1.5)
        logger.warning(
            "장마감 청산 후 잔고 동기화 지연 — 예상 최대 %d종목, API 응답 %d종목 (잠시 후 다시 조회됩니다)",
            target_max,
            len(self._last_positions),
        )

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
            daily_pnl = realized.total_pnl

            await self._report.save_daily_report(
                report_date=fill_date,
                buy_count=counts.buy_count,
                sell_count=counts.sell_count,
                unfilled=counts.fail_count,
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
        self._daily_buy_count = counts.buy_count
        self._consecutive_stop_loss = await self._order_log.get_consecutive_stop_loss(today)

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
            code = st.stock_code
            if code in holding_codes:
                db_highest[code] = st.highest_price
                if st.activated:
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
        buy_block_reason = ""

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
            if current_time < dt_time(C.MARKET_OPEN_HOUR, C.SCAN_START_MINUTE):
                self._phase = "WAITING_MARKET"
                self._emit(EventType.SCAN_START,
                    "⏰ 장이 열렸지만 시초가 변동이 큰 시간이라 09:05부터 분석을 시작합니다")
                return
            if current_time > dt_time(C.MARKET_CLOSE_HOUR, C.SCAN_END_MINUTE):
                self._phase = "MARKET_CLOSED"
                return
            buy_allowed = current_time <= dt_time(C.MARKET_CLOSE_HOUR, C.BUY_CUTOFF_MINUTE)

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

            # ── 긴급 매도 체크: KOSPI 급락 시 전량 매도 ──
            emergency_sell, kospi_change = await self.market_guard.check_emergency_sell(positions)
            if emergency_sell:
                self._emit(EventType.SELL_EVAL,
                    f"🚨 KOSPI {kospi_change:+.1f}% 급락! 보유 전종목 긴급 매도합니다")
                for pos in positions:
                    try:
                        pd = await self._market.get_current_price(pos.stock_code)
                        cur = pd.current_price if pd.current_price > 0 else int(pos.avg_price)
                        sold = await self.sell_evaluator.execute_sell(pos, OrderReason.STOP_LOSS, cur)
                        if sold:
                            sell_count += 1
                            await self.sell_evaluator.cleanup_trailing(pos.stock_code)
                            name = stock_fmt(pos.stock_code)
                            self._emit(EventType.ORDER_EXEC,
                                f"🚨 {name} 긴급 매도 완료 (KOSPI {kospi_change:+.1f}%)",
                                {"type": "SELL", "code": pos.stock_code,
                                 "reason": "EMERGENCY", "price": cur, "success": True})
                    except Exception:
                        logger.exception("긴급 매도 중 오류: %s", pos.stock_code)
                        error_count += 1

            # ── 매도 평가 ──
            if not emergency_sell:
                for pos in positions:
                    try:
                        result = await self.sell_evaluator.evaluate_sell(pos, unfilled_codes)
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
                            stock_data(pos.stock_code))

            if emergency_sell:
                buy_allowed = False

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

                buy_block_reason = ""
                market_ok, market_reason = await self.market_guard.check_buy_allowed()
                if not market_ok:
                    buy_block_reason = market_reason
                    buy_allowed = False

                if buy_allowed and self._consecutive_stop_loss >= C.CONSECUTIVE_STOP_LOSS_LIMIT:
                    buy_block_reason = f"연속 손절 {self._consecutive_stop_loss}회"
                    buy_allowed = False
                if (buy_allowed and self._settings.max_daily_buy_count > 0
                        and self._daily_buy_count >= self._settings.max_daily_buy_count):
                    buy_block_reason = f"매수 한도 도달 ({self._daily_buy_count}/{self._settings.max_daily_buy_count}건)"
                    buy_allowed = False
                if (self._settings.max_holding_count > 0
                        and buy_allowed and current_holding >= self._settings.max_holding_count):
                    buy_block_reason = f"보유 종목 한도 ({self._settings.max_holding_count}개)"
                    buy_allowed = False

                self.buy_evaluator.last_positions = self._last_positions
                self.buy_evaluator.last_account_summary = self._last_account_summary

                for code in self._settings.watch_list_codes:
                    try:
                        result = await self.buy_evaluator.evaluate_buy(
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
                            stock_data(code))

                self._last_positions = self.buy_evaluator.last_positions
                self._last_account_summary = self.buy_evaluator.last_account_summary

            if buy_count > 0:
                optimistic_codes = {p.stock_code for p in self._last_positions}
                refreshed = await self._order.get_balance()
                if refreshed is not None:
                    refreshed_codes = {p.stock_code for p in refreshed}
                    for op in self._last_positions:
                        if op.stock_code not in refreshed_codes and op.stock_code in optimistic_codes:
                            refreshed.append(op)
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
        if buy_block_reason:
            parts.append(f"매수 차단: {buy_block_reason}")
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

    # ── run_pre_market ──

    async def run_pre_market(self) -> None:
        try:
            if datetime.now().strftime("%Y%m%d") in C.KRX_HOLIDAYS:
                return
            self._phase = "PRE_MARKET"
            self._emit(EventType.PRE_MARKET, "🌅 장 시작 준비 중... 토큰 갱신, 잔고 확인, 미체결 정리")

            await self._auth.get_token()
            self._daily_buy_count = 0
            self._consecutive_stop_loss = 0
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
                sold = await self.sell_evaluator.execute_sell(pos, OrderReason.EOD_CLOSE, cur)
                if sold:
                    sell_count += 1
                    await self.sell_evaluator.cleanup_trailing(pos.stock_code)
            except Exception:
                logger.exception("장마감 청산 중 %s 매도 오류", pos.stock_code)

        if sell_count > 0:
            await self._refresh_holdings_after_eod_sells(len(positions), sell_count)

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
            counts = await self._order_log.get_today_counts(today)

            await self.refresh_holdings()
            positions = self._last_positions
            if counts.sell_count > 0 and len(positions) > 0:
                for _ in range(5):
                    await asyncio.sleep(1.2)
                    await self.refresh_holdings()
                    positions = self._last_positions
                    if len(positions) == 0:
                        break

            await self._cleanup_unfilled_orders()

            total_eval = sum(p.current_price * p.quantity for p in positions)
            total_cost = sum(p.avg_price * p.quantity for p in positions)
            unrealized_pnl = total_eval - total_cost

            realized = await self._order_log.get_today_realized_pnl(today)
            today_realized = realized.total_pnl
            total_profit = today_realized + unrealized_pnl
            rate = (total_profit / 10_000_000 * 100) if total_profit != 0 else 0.0

            account = await self._order.get_account_summary()
            total_cash = account.total_cash if account else 0
            total_assets = account.total_assets if account else total_eval

            yesterday = await self._report.get_yesterday_report(today)
            deposit_withdrawal = 0
            if yesterday and yesterday.get("total_assets", 0) > 0:
                prev_assets = yesterday["total_assets"]
                asset_change = total_assets - prev_assets
                deposit_withdrawal = asset_change - today_realized - unrealized_pnl
                if abs(deposit_withdrawal) < C.DEPOSIT_THRESHOLD:
                    deposit_withdrawal = 0

            prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
            cumulative_pnl = prev_cumulative + today_realized

            if deposit_withdrawal != 0:
                dw_type = "입금" if deposit_withdrawal > 0 else "출금"
                await self._report.save_capital_event(
                    today, deposit_withdrawal,
                    f"자동 감지: {dw_type} {abs(deposit_withdrawal):,}원",
                )
                logger.info("입출금 자동 감지: %s %s원", dw_type, f"{abs(deposit_withdrawal):,}")

            await self._report.save_daily_report(
                report_date=today,
                buy_count=counts.buy_count,
                sell_count=counts.sell_count,
                unfilled=counts.fail_count,
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

            r_sign = "+" if today_realized >= 0 else ""
            parts = [f"보유 {len(positions)}종목"]
            if counts.buy_count:
                parts.append(f"매수 {counts.buy_count}건")
            if counts.sell_count:
                parts.append(f"매도 {counts.sell_count}건")
            parts.append(f"총자산 {total_assets:,}원")
            parts.append(f"오늘 실현손익 {r_sign}{today_realized:,}원")
            if deposit_withdrawal != 0:
                dw_sign = "+" if deposit_withdrawal > 0 else ""
                parts.append(f"입출금 {dw_sign}{deposit_withdrawal:,}원")

            msg = "🌙 오늘 거래 결산 완료 — " + ", ".join(parts)
            trade_logger.info(msg)
            self._emit(EventType.POST_MARKET, msg, {
                "holding": len(positions), "eval": total_eval,
                "realized": today_realized, "unrealized": int(unrealized_pnl),
                "rate": round(rate, 2),
                "total_cash": total_cash, "total_assets": total_assets,
                "cumulative_pnl": cumulative_pnl,
            })

            deleted = await self._report.cleanup_old_scan_logs(days=C.CLEANUP_SCAN_LOG_DAYS)
            if deleted:
                logger.info("%d일 이전 스캔 로그 %d건 정리 완료", C.CLEANUP_SCAN_LOG_DAYS, deleted)
            evt_deleted = await self._report.cleanup_old_bot_events(days=C.CLEANUP_BOT_EVENT_DAYS)
            if evt_deleted:
                logger.info("%d일 이전 이벤트 로그 %d건 정리 완료", C.CLEANUP_BOT_EVENT_DAYS, evt_deleted)

            self._phase = "MARKET_CLOSED"
        except Exception:
            logger.exception("run_post_market 오류")
            self._emit(EventType.ERROR, "⚠️ 장 마감 처리 중 오류 발생")
            self._phase = "MARKET_CLOSED"

    # ── 수동 매매 ──

    async def manual_sell(self, stock_code: str, quantity: int | None = None) -> dict:
        """수동 매도. quantity 생략 시 전량 매도. Router에서 호출."""
        pos = next((p for p in self._last_positions if p.stock_code == stock_code), None)
        if not pos:
            return {"success": False, "order_no": None, "message": "보유 종목이 아닙니다", "error": "not_found"}
        qty = quantity if quantity is not None and 0 < quantity <= pos.quantity else pos.quantity
        if qty <= 0:
            return {"success": False, "order_no": None, "message": "수량이 올바르지 않습니다", "error": "invalid"}

        from app.model.domain import OrderType
        result = await self._order.execute_order(
            stock_code=stock_code,
            order_type=OrderType.SELL,
            quantity=qty,
            price=pos.current_price,
        )
        if result.success:
            await self._order_log.save_order(
                stock_code, OrderType.SELL, OrderReason.MANUAL,
                qty, pos.current_price, result, datetime.now().isoformat(),
            )
            await self.refresh_holdings()
            name = stock_fmt(stock_code)
            self._emit(EventType.ORDER_EXEC,
                f"✋ 수동 매도: {name} {qty}주 × {pos.current_price:,}원",
                {"type": "SELL", "code": stock_code, "qty": qty,
                 "price": pos.current_price, "success": True, "reason": "MANUAL"})
        return {
            "success": result.success,
            "order_no": result.order_no,
            "message": result.error_message or "매도 완료",
        }

    async def manual_buy(self, stock_code: str, quantity: int) -> dict:
        """수동 매수. Router에서 호출."""
        if stock_code not in self._settings.watch_list_codes:
            return {"success": False, "order_no": None, "message": "관심 종목에 없는 종목입니다", "error": "invalid"}

        try:
            price_data = await self._market.get_current_price(stock_code)
        except Exception as e:
            return {"success": False, "order_no": None, "message": f"현재가 조회 실패: {e}", "error": "api_error"}

        if price_data.current_price <= 0:
            return {"success": False, "order_no": None, "message": "현재가를 조회할 수 없습니다", "error": "invalid"}

        from app.model.domain import OrderType
        result = await self._order.execute_order(
            stock_code=stock_code,
            order_type=OrderType.BUY,
            quantity=quantity,
            price=price_data.current_price,
        )
        if result.success:
            await self._order_log.save_order(
                stock_code, OrderType.BUY, OrderReason.MANUAL,
                quantity, price_data.current_price, result, datetime.now().isoformat(),
            )
            await self.refresh_holdings()
            name = stock_fmt(stock_code)
            self._emit(EventType.ORDER_EXEC,
                f"✋ 수동 매수: {name} {quantity}주 × {price_data.current_price:,}원",
                {"type": "BUY", "code": stock_code, "qty": quantity,
                 "price": price_data.current_price, "success": True, "reason": "MANUAL"})
        return {
            "success": result.success,
            "order_no": result.order_no,
            "message": result.error_message or "매수 완료",
        }

    async def execute_manual_order(
        self, stock_code: str, order_type, quantity: int, price: int = 0,
    ):
        """범용 수동 주문 실행. trading_router에서 호출."""
        return await self._order.execute_order(
            stock_code=stock_code,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )

    # ── PnL / 성과 조회 (Router 위임용) ──

    async def get_today_pnl_summary(self) -> dict:
        """오늘의 실현손익·누적손익·거래내역 조회."""
        today = datetime.now().strftime("%Y-%m-%d")
        realized = await self._order_log.get_today_realized_pnl(today)
        yesterday = await self._report.get_yesterday_report(today)
        prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
        return {
            "today_realized_pnl": realized.total_pnl,
            "today_trades": [
                {"stock_code": t.stock_code, "quantity": t.quantity,
                 "sell_price": t.sell_price, "avg_buy_price": t.avg_buy_price, "pnl": t.pnl}
                for t in realized.trades
            ],
            "cumulative_pnl": prev_cumulative + realized.total_pnl,
            "initial_assets": yesterday.get("total_assets", 0) if yesterday else 0,
            "yesterday_total_cash": yesterday.get("total_cash", 0) if yesterday else 0,
            "yesterday_total_assets": yesterday.get("total_assets", 0) if yesterday else 0,
        }

    async def build_today_performance_entry(self) -> dict | None:
        """오늘자 성과 항목 생성 (performance history 추가용)."""
        today = datetime.now().strftime("%Y-%m-%d")
        acct_status = self.status
        realized = await self._order_log.get_today_realized_pnl(today)
        yesterday = await self._report.get_yesterday_report(today)
        prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
        total_assets = acct_status.get("total_assets", 0)
        total_cash = acct_status.get("total_cash", 0)

        if total_assets <= 0 and yesterday:
            total_assets = yesterday.get("total_assets", 0)
            total_cash = yesterday.get("total_cash", 0)

        if total_assets <= 0:
            return None

        return {
            "report_date": today,
            "eval_amount": acct_status.get("stock_eval", 0),
            "eval_profit": realized.total_pnl,
            "profit_rate": 0.0,
            "total_cash": total_cash,
            "total_assets": total_assets,
            "deposit_withdrawal": 0,
            "cumulative_pnl": prev_cumulative + realized.total_pnl,
        }

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
