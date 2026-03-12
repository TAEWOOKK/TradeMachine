from __future__ import annotations

import pytest
import pytest_asyncio

from app.model.domain import Position, ScanResult
from app.repository.report_repository import ReportRepository


@pytest_asyncio.fixture
async def report_repo(db_instance):
    return ReportRepository(database=db_instance)


class TestReportRepository:
    @pytest.mark.asyncio
    async def test_save_scan_log(self, report_repo, db_instance):
        scan = ScanResult(
            scan_time="2026-03-10T10:00:00",
            holding_count=3,
            sell_count=1,
            buy_count=2,
            skip_count=5,
            error_count=0,
            api_call_count=15,
            elapsed_ms=1200,
        )
        await report_repo.save_scan_log(scan)
        rows = await db_instance.fetch_all("SELECT * FROM scan_logs")
        assert len(rows) == 1
        assert rows[0]["holding_count"] == 3
        assert rows[0]["buy_count"] == 2
        assert rows[0]["elapsed_ms"] == 1200

    @pytest.mark.asyncio
    async def test_save_daily_report(self, report_repo, db_instance):
        await report_repo.save_daily_report(
            report_date="2026-03-10",
            buy_count=2,
            sell_count=1,
            unfilled=0,
            holding_count=4,
            eval_amount=5_000_000,
            eval_profit=200_000,
            profit_rate=4.17,
        )
        rows = await db_instance.fetch_all("SELECT * FROM daily_reports")
        assert len(rows) == 1
        assert rows[0]["report_date"] == "2026-03-10"
        assert rows[0]["buy_count"] == 2
        assert rows[0]["eval_amount"] == 5_000_000
        assert rows[0]["profit_rate"] == pytest.approx(4.17)

    @pytest.mark.asyncio
    async def test_save_balance_snapshot(self, report_repo, db_instance):
        positions = [
            Position(
                stock_code="005930",
                quantity=10,
                avg_price=70000.0,
                profit_rate=2.5,
                current_price=71750,
            ),
            Position(
                stock_code="000660",
                quantity=5,
                avg_price=120000.0,
                profit_rate=-1.2,
                current_price=118560,
            ),
        ]
        await report_repo.save_balance_snapshot("2026-03-10", positions)
        rows = await db_instance.fetch_all("SELECT * FROM balance_snapshots")
        assert len(rows) == 2
        codes = {row["stock_code"] for row in rows}
        assert codes == {"005930", "000660"}

    @pytest.mark.asyncio
    async def test_cleanup_old_scan_logs(self, report_repo, db_instance):
        await db_instance.execute(
            """INSERT INTO scan_logs
               (scan_time, holding_count, sell_count, buy_count,
                skip_count, error_count, api_call_count, elapsed_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2025-01-01T10:00:00", 0, 0, 0, 0, 0, 0, 100),
        )
        await db_instance.execute(
            """INSERT INTO scan_logs
               (scan_time, holding_count, sell_count, buy_count,
                skip_count, error_count, api_call_count, elapsed_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2026-03-10T10:00:00", 3, 1, 2, 5, 0, 15, 1200),
        )

        deleted = await report_repo.cleanup_old_scan_logs(days=90)
        assert deleted >= 1

        rows = await db_instance.fetch_all("SELECT * FROM scan_logs")
        for row in rows:
            assert row["scan_time"] >= "2026"
