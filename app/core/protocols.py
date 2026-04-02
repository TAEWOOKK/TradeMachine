from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.model.domain import (
    AccountSummary,
    BotEventRecord,
    DailyCandle,
    EstimatedFees,
    OrderReason,
    OrderResult,
    OrderType,
    Position,
    RealizedPnl,
    ScanResult,
    StockPrice,
    TodayCounts,
    TrailingState,
)


@runtime_checkable
class AuthRepository(Protocol):
    async def get_token(self) -> str: ...
    async def get_common_headers(self, tr_id: str) -> dict[str, str]: ...
    async def get_hashkey(self, body: dict) -> str: ...
    def get_tr_id(self, real_tr_id: str) -> str: ...


@runtime_checkable
class MarketDataRepository(Protocol):
    def reset_api_count(self) -> None: ...

    @property
    def api_call_count(self) -> int: ...

    async def get_current_price(
        self, stock_code: str, market_code: str = "J",
    ) -> StockPrice: ...

    async def get_index_price(self, index_code: str = "0001") -> StockPrice: ...

    async def get_daily_chart(
        self, stock_code: str, days: int = 60,
    ) -> list[DailyCandle]: ...


@runtime_checkable
class OrderRepository(Protocol):
    async def get_balance(self) -> list[Position] | None: ...
    async def get_unfilled_orders(self) -> list[dict]: ...
    async def cancel_order(self, order_no: str, quantity: int) -> bool: ...
    async def get_account_summary(self) -> AccountSummary | None: ...
    async def get_available_cash(self, stock_code: str) -> int: ...

    async def execute_order(
        self,
        stock_code: str,
        order_type: OrderType,
        quantity: int,
        price: int = 0,
    ) -> OrderResult: ...


@runtime_checkable
class OrderLogRepository(Protocol):
    async def save_order(
        self,
        stock_code: str,
        order_type: OrderType,
        order_reason: OrderReason,
        quantity: int,
        price: int,
        result: OrderResult,
        scan_time: str,
        order_method: str = "MARKET",
    ) -> None: ...

    async def get_last_sell_time(self, stock_code: str) -> datetime | None: ...
    async def get_first_buy_date(self, stock_code: str) -> str | None: ...
    async def get_consecutive_stop_loss(self, today: str) -> int: ...
    async def get_today_counts(self, today: str) -> TodayCounts: ...
    async def get_today_realized_pnl(self, today: str) -> RealizedPnl: ...
    async def get_estimated_fees_taxes(self) -> EstimatedFees: ...
    async def get_recent_orders(self, limit: int = 50) -> list[dict]: ...

    async def save_trailing_state(
        self, stock_code: str, highest_price: int, activated: bool,
    ) -> None: ...

    async def load_trailing_states(self) -> list[TrailingState]: ...
    async def delete_trailing_state(self, stock_code: str) -> None: ...
    async def cleanup_trailing_states(self, holding_codes: set[str]) -> None: ...


@runtime_checkable
class ReportRepository(Protocol):
    async def save_scan_log(self, result: ScanResult) -> None: ...

    async def save_daily_report(
        self,
        report_date: str,
        buy_count: int,
        sell_count: int,
        unfilled: int,
        holding_count: int,
        eval_amount: int,
        eval_profit: int,
        profit_rate: float,
        total_cash: int = 0,
        total_assets: int = 0,
        deposit_withdrawal: int = 0,
        cumulative_pnl: int = 0,
    ) -> None: ...

    async def save_balance_snapshot(
        self, snapshot_date: str, positions: list[Position],
    ) -> None: ...

    async def get_yesterday_report(self, today: str) -> dict | None: ...
    async def get_first_report(self) -> dict | None: ...
    async def get_deposit_withdrawal_sum(self, days: int = 365) -> int: ...
    async def get_performance_history(self, days: int = 90) -> list[dict]: ...
    async def get_capital_events(self, days: int = 90) -> list[dict]: ...

    async def save_capital_event(
        self, event_date: str, amount: int, note: str = "",
    ) -> None: ...

    async def cleanup_old_scan_logs(self, days: int = 90) -> int: ...

    async def save_bot_event(
        self,
        event_type: str,
        message: str,
        timestamp: float,
        data: dict | None = None,
    ) -> None: ...

    async def get_recent_bot_events(
        self, limit: int = 200,
    ) -> list[BotEventRecord]: ...

    async def cleanup_old_bot_events(self, days: int = 30) -> int: ...
