from __future__ import annotations

from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, db_path: str = "data/trademachine.db") -> None:
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        await self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._migrate()

    async def disconnect(self) -> None:
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        assert self._connection is not None
        cursor = await self._connection.execute(sql, params)
        await self._connection.commit()
        return cursor

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        assert self._connection is not None
        cursor = await self._connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        assert self._connection is not None
        cursor = await self._connection.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def _migrate(self) -> None:
        """기존 테이블에 누락된 컬럼을 추가하는 마이그레이션."""
        assert self._connection is not None
        migrations = [
            ("daily_reports", "total_cash", "INTEGER NOT NULL DEFAULT 0"),
            ("daily_reports", "total_assets", "INTEGER NOT NULL DEFAULT 0"),
            ("daily_reports", "deposit_withdrawal", "INTEGER NOT NULL DEFAULT 0"),
            ("daily_reports", "cumulative_pnl", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, col, col_type in migrations:
            try:
                await self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                )
            except Exception:
                pass  # 이미 존재하면 무시
        await self._connection.commit()

    async def _create_tables(self) -> None:
        assert self._connection is not None
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                order_type TEXT NOT NULL,
                order_reason TEXT NOT NULL,
                order_method TEXT NOT NULL DEFAULT 'MARKET',
                quantity INTEGER NOT NULL,
                price INTEGER NOT NULL,
                kis_order_no TEXT,
                success INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                scan_time TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL UNIQUE,
                buy_count INTEGER NOT NULL DEFAULT 0,
                sell_count INTEGER NOT NULL DEFAULT 0,
                unfilled_count INTEGER NOT NULL DEFAULT 0,
                holding_count INTEGER NOT NULL DEFAULT 0,
                eval_amount INTEGER NOT NULL DEFAULT 0,
                eval_profit INTEGER NOT NULL DEFAULT 0,
                profit_rate REAL NOT NULL DEFAULT 0.0,
                total_cash INTEGER NOT NULL DEFAULT 0,
                total_assets INTEGER NOT NULL DEFAULT 0,
                deposit_withdrawal INTEGER NOT NULL DEFAULT 0,
                cumulative_pnl INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS capital_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT NOT NULL,
                amount INTEGER NOT NULL,
                detected_auto INTEGER NOT NULL DEFAULT 1,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS balance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_price REAL NOT NULL,
                current_price INTEGER NOT NULL,
                eval_amount INTEGER NOT NULL,
                profit_rate REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT NOT NULL,
                holding_count INTEGER NOT NULL DEFAULT 0,
                sell_count INTEGER NOT NULL DEFAULT 0,
                buy_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                api_call_count INTEGER NOT NULL DEFAULT 0,
                elapsed_ms INTEGER NOT NULL DEFAULT 0,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp REAL NOT NULL,
                data TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_bot_events_created_at
                ON bot_events(created_at);

            CREATE TABLE IF NOT EXISTS trailing_state (
                stock_code TEXT PRIMARY KEY,
                highest_price INTEGER NOT NULL,
                activated INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
            CREATE INDEX IF NOT EXISTS idx_orders_stock_code ON orders(stock_code);
            CREATE INDEX IF NOT EXISTS idx_balance_snapshot_date ON balance_snapshots(snapshot_date);
            CREATE INDEX IF NOT EXISTS idx_scan_logs_scan_time ON scan_logs(scan_time);
            """
        )
