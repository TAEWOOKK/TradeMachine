from __future__ import annotations

import pytest
import pytest_asyncio

from app.model.domain import OrderReason, OrderResult, OrderType
from app.repository.order_log_repository import OrderLogRepository


@pytest_asyncio.fixture
async def order_log_repo(db_instance):
    return OrderLogRepository(database=db_instance)


class TestOrderLogRepository:
    @pytest.mark.asyncio
    async def test_save_order(self, order_log_repo, db_instance):
        result = OrderResult(success=True, order_no="12345", error_message=None)
        await order_log_repo.save_order(
            "005930", OrderType.BUY, OrderReason.GOLDEN_CROSS,
            10, 70000, result, "2026-03-10T10:00:00",
        )
        rows = await db_instance.fetch_all("SELECT * FROM orders")
        assert len(rows) == 1
        assert rows[0]["stock_code"] == "005930"
        assert rows[0]["order_type"] == "BUY"
        assert rows[0]["order_reason"] == "GOLDEN_CROSS"
        assert rows[0]["quantity"] == 10
        assert rows[0]["price"] == 70000
        assert rows[0]["kis_order_no"] == "12345"
        assert rows[0]["success"] == 1

    @pytest.mark.asyncio
    async def test_get_last_sell_time_exists(self, order_log_repo):
        result = OrderResult(success=True, order_no="99999", error_message=None)
        await order_log_repo.save_order(
            "005930", OrderType.SELL, OrderReason.STOP_LOSS,
            5, 68000, result, "2026-03-10T14:00:00",
        )
        last_sell = await order_log_repo.get_last_sell_time("005930")
        assert last_sell is not None

    @pytest.mark.asyncio
    async def test_get_last_sell_time_none(self, order_log_repo):
        last_sell = await order_log_repo.get_last_sell_time("999999")
        assert last_sell is None

    @pytest.mark.asyncio
    async def test_get_first_buy_date(self, order_log_repo):
        result = OrderResult(success=True, order_no="11111", error_message=None)
        await order_log_repo.save_order(
            "005930", OrderType.BUY, OrderReason.GOLDEN_CROSS,
            10, 70000, result, "2026-03-10T09:30:00",
        )
        buy_date = await order_log_repo.get_first_buy_date("005930")
        assert buy_date is not None
        assert len(buy_date) == 10  # "YYYY-MM-DD"

    @pytest.mark.asyncio
    async def test_get_today_counts(self, order_log_repo, db_instance):
        success = OrderResult(success=True, order_no="A", error_message=None)
        failure = OrderResult(success=False, order_no=None, error_message="err")

        await order_log_repo.save_order(
            "005930", OrderType.BUY, OrderReason.GOLDEN_CROSS,
            10, 70000, success, "2026-03-10T09:30:00",
        )
        await order_log_repo.save_order(
            "000660", OrderType.BUY, OrderReason.GOLDEN_CROSS,
            5, 120000, success, "2026-03-10T10:00:00",
        )
        await order_log_repo.save_order(
            "005930", OrderType.SELL, OrderReason.TAKE_PROFIT,
            10, 80000, success, "2026-03-10T14:00:00",
        )
        await order_log_repo.save_order(
            "035420", OrderType.BUY, OrderReason.GOLDEN_CROSS,
            3, 300000, failure, "2026-03-10T11:00:00",
        )

        # get_today_counts uses created_at LIKE prefix, we inserted with datetime.now()
        # so we query using today's date from the inserted rows
        rows = await db_instance.fetch_all("SELECT created_at FROM orders LIMIT 1")
        today = rows[0]["created_at"][:10]

        counts = await order_log_repo.get_today_counts(today)
        assert counts["buy_count"] == 2
        assert counts["sell_count"] == 1
        assert counts["fail_count"] == 1
