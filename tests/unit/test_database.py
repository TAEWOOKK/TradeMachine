from __future__ import annotations

import pytest

from app.core.database import Database


class TestDatabase:
    @pytest.mark.asyncio
    async def test_connect_and_tables_created(self, db_instance):
        rows = await db_instance.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        )
        table_names = {row["name"] for row in rows}
        assert "orders" in table_names
        assert "daily_reports" in table_names
        assert "balance_snapshots" in table_names
        assert "scan_logs" in table_names

    @pytest.mark.asyncio
    async def test_execute_and_fetch(self, db_instance):
        await db_instance.execute(
            """INSERT INTO orders
               (created_at, stock_code, order_type, order_reason, quantity, price, success)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2026-03-10 10:00:00", "005930", "BUY", "GOLDEN_CROSS", 10, 70000, 1),
        )
        rows = await db_instance.fetch_all("SELECT * FROM orders")
        assert len(rows) == 1
        assert rows[0]["stock_code"] == "005930"
        assert rows[0]["order_type"] == "BUY"
        assert rows[0]["quantity"] == 10

    @pytest.mark.asyncio
    async def test_fetch_one_none(self, db_instance):
        row = await db_instance.fetch_one(
            "SELECT * FROM orders WHERE id = ?", (999,),
        )
        assert row is None
