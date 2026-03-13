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

    async def get_today_realized_pnl(self, today: str) -> dict:
        """오늘 매도로 실현된 손익 계산."""
        rows = await self._db.fetch_all(
            """
            SELECT s.stock_code, s.quantity, s.price AS sell_price,
                   COALESCE(
                       (SELECT AVG(b.price) FROM orders b
                        WHERE b.stock_code = s.stock_code
                          AND b.order_type = 'BUY' AND b.success = 1
                          AND b.created_at > COALESCE(
                              (SELECT MAX(ps.created_at) FROM orders ps
                               WHERE ps.stock_code = s.stock_code
                                 AND ps.order_type = 'SELL' AND ps.success = 1
                                 AND ps.created_at < s.created_at),
                              '0000-00-00'
                          )
                       ), s.price
                   ) AS avg_buy_price
            FROM orders s
            WHERE s.order_type = 'SELL' AND s.success = 1
              AND s.created_at LIKE ?
            """,
            (f"{today}%",),
        )
        total_pnl = 0
        trades: list[dict] = []
        for r in rows:
            pnl = int((r["sell_price"] - r["avg_buy_price"]) * r["quantity"])
            total_pnl += pnl
            trades.append({
                "stock_code": r["stock_code"],
                "quantity": r["quantity"],
                "sell_price": r["sell_price"],
                "avg_buy_price": int(r["avg_buy_price"]),
                "pnl": pnl,
            })
        return {"total_pnl": total_pnl, "trades": trades}

    # ── Trailing State 영속화 ──

    async def save_trailing_state(
        self, stock_code: str, highest_price: int, activated: bool,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO trailing_state (stock_code, highest_price, activated, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                highest_price = MAX(excluded.highest_price, trailing_state.highest_price),
                activated = excluded.activated,
                updated_at = excluded.updated_at
            """,
            (stock_code, highest_price, 1 if activated else 0,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

    async def load_trailing_states(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT stock_code, highest_price, activated FROM trailing_state"
        )

    async def delete_trailing_state(self, stock_code: str) -> None:
        await self._db.execute(
            "DELETE FROM trailing_state WHERE stock_code = ?",
            (stock_code,),
        )

    async def cleanup_trailing_states(self, holding_codes: set[str]) -> None:
        """보유하지 않는 종목의 trailing state 정리."""
        rows = await self._db.fetch_all(
            "SELECT stock_code FROM trailing_state"
        )
        for row in rows:
            if row["stock_code"] not in holding_codes:
                await self.delete_trailing_state(row["stock_code"])
