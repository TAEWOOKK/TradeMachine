from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.core.database import Database
from app.model.domain import Position, ScanResult


class ReportRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def save_scan_log(self, result: ScanResult) -> None:
        await self._db.execute(
            """INSERT INTO scan_logs
               (scan_time, holding_count, sell_count, buy_count, skip_count,
                error_count, api_call_count, elapsed_ms, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result.scan_time, result.holding_count, result.sell_count,
             result.buy_count, result.skip_count, result.error_count,
             result.api_call_count, result.elapsed_ms, result.note),
        )

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
    ) -> None:
        await self._db.execute(
            """INSERT OR REPLACE INTO daily_reports
               (report_date, buy_count, sell_count, unfilled_count,
                holding_count, eval_amount, eval_profit, profit_rate,
                total_cash, total_assets, deposit_withdrawal, cumulative_pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_date, buy_count, sell_count, unfilled,
             holding_count, eval_amount, eval_profit, profit_rate,
             total_cash, total_assets, deposit_withdrawal, cumulative_pnl),
        )

    async def save_balance_snapshot(
        self, snapshot_date: str, positions: list[Position],
    ) -> None:
        for p in positions:
            await self._db.execute(
                """INSERT INTO balance_snapshots
                   (snapshot_date, stock_code, quantity, avg_price,
                    current_price, eval_amount, profit_rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_date, p.stock_code, p.quantity, p.avg_price,
                 p.current_price, p.current_price * p.quantity, p.profit_rate),
            )

    async def get_yesterday_report(self, today: str) -> dict | None:
        """오늘 이전의 가장 최근 리포트를 반환."""
        return await self._db.fetch_one(
            """SELECT * FROM daily_reports
               WHERE report_date < ? ORDER BY report_date DESC LIMIT 1""",
            (today,),
        )

    async def get_performance_history(self, days: int = 90) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return await self._db.fetch_all(
            """SELECT report_date, eval_amount, eval_profit, profit_rate,
                      total_cash, total_assets, deposit_withdrawal, cumulative_pnl
               FROM daily_reports
               WHERE report_date >= ?
               ORDER BY report_date ASC""",
            (cutoff,),
        )

    async def get_capital_events(self, days: int = 90) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return await self._db.fetch_all(
            """SELECT event_date, amount, detected_auto, note
               FROM capital_events WHERE event_date >= ?
               ORDER BY event_date ASC""",
            (cutoff,),
        )

    async def save_capital_event(
        self, event_date: str, amount: int, note: str = "",
    ) -> None:
        await self._db.execute(
            """INSERT INTO capital_events (event_date, amount, detected_auto, note)
               VALUES (?, ?, 1, ?)""",
            (event_date, amount, note),
        )

    async def cleanup_old_scan_logs(self, days: int = 90) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM scan_logs WHERE scan_time < ?", (cutoff,),
        )
        return cursor.rowcount

    # ── Bot Event 영속화 ──

    _PERSIST_TYPES = {
        "order_exec", "sell_eval", "buy_eval",
        "pre_market", "post_market", "state_change", "error",
        "scan_end",
    }

    async def save_bot_event(
        self, event_type: str, message: str,
        timestamp: float, data: dict | None = None,
    ) -> None:
        if event_type not in self._PERSIST_TYPES:
            return
        if event_type == "buy_eval" and data:
            action = data.get("action")
            if action not in ("매수검토", None):
                return
        await self._db.execute(
            """INSERT INTO bot_events
               (event_type, message, timestamp, data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event_type, message, timestamp,
             json.dumps(data, ensure_ascii=False) if data else None,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

    async def get_recent_bot_events(self, limit: int = 200) -> list[dict]:
        rows = await self._db.fetch_all(
            """SELECT event_type, message, timestamp, data
               FROM bot_events
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        result = []
        for row in reversed(rows):
            d = row.get("data")
            result.append({
                "type": row["event_type"],
                "message": row["message"],
                "timestamp": row["timestamp"],
                "data": json.loads(d) if d else None,
            })
        return result

    async def cleanup_old_bot_events(self, days: int = 30) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = await self._db.execute(
            "DELETE FROM bot_events WHERE created_at < ?", (cutoff,),
        )
        return cursor.rowcount
