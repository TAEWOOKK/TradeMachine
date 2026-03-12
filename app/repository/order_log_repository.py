from __future__ import annotations

import logging
from datetime import datetime

from app.core.database import Database
from app.model.domain import OrderReason, OrderResult, OrderType

logger = logging.getLogger(__name__)


class OrderLogRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

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
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO orders (created_at, stock_code, order_type, order_reason,
                                order_method, quantity, price, kis_order_no, success,
                                error_message, scan_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                stock_code,
                order_type.value,
                order_reason.value,
                order_method,
                quantity,
                price,
                result.order_no,
                1 if result.success else 0,
                result.error_message,
                scan_time,
            ),
        )

    async def get_last_sell_time(self, stock_code: str) -> datetime | None:
        row = await self._db.fetch_one(
            """
            SELECT created_at FROM orders
            WHERE stock_code = ? AND order_type = 'SELL' AND success = 1
            ORDER BY created_at DESC LIMIT 1
            """,
            (stock_code,),
        )
        if not row:
            return None
        return datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")

    async def get_first_buy_date(self, stock_code: str) -> str | None:
        """현재 보유의 시작일 반환 (마지막 매도 이후 첫 매수일)."""
        row = await self._db.fetch_one(
            """
            SELECT created_at FROM orders
            WHERE stock_code = ? AND order_type = 'BUY' AND success = 1
              AND created_at > COALESCE(
                (SELECT created_at FROM orders
                 WHERE stock_code = ? AND order_type = 'SELL' AND success = 1
                 ORDER BY created_at DESC LIMIT 1),
                '0000-00-00'
              )
            ORDER BY created_at ASC LIMIT 1
            """,
            (stock_code, stock_code),
        )
        if not row:
            return None
        return row["created_at"][:10]

    async def get_today_counts(self, today: str) -> dict:
        row = await self._db.fetch_one(
            """
            SELECT
                COALESCE(SUM(CASE WHEN order_type = 'BUY' AND success = 1 THEN 1 ELSE 0 END), 0) AS buy_count,
                COALESCE(SUM(CASE WHEN order_type = 'SELL' AND success = 1 THEN 1 ELSE 0 END), 0) AS sell_count,
                COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS fail_count
            FROM orders
            WHERE created_at LIKE ?
            """,
            (f"{today}%",),
        )
        if not row:
            return {"buy_count": 0, "sell_count": 0, "fail_count": 0}
        return {
            "buy_count": row["buy_count"],
            "sell_count": row["sell_count"],
            "fail_count": row["fail_count"],
        }
