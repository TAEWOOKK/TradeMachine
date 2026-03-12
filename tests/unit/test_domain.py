from __future__ import annotations

from app.model.domain import (
    DailyCandle,
    OrderReason,
    OrderType,
    Position,
    ScanResult,
    StockPrice,
)


class TestOrderType:
    def test_order_type_values(self):
        assert OrderType.BUY.value == "BUY"
        assert OrderType.SELL.value == "SELL"
        assert len(OrderType) == 2


class TestOrderReason:
    def test_order_reason_values(self):
        assert OrderReason.GOLDEN_CROSS.value == "GOLDEN_CROSS"
        assert OrderReason.DEAD_CROSS.value == "DEAD_CROSS"
        assert OrderReason.STOP_LOSS.value == "STOP_LOSS"
        assert OrderReason.TAKE_PROFIT.value == "TAKE_PROFIT"
        assert OrderReason.TRAILING_STOP.value == "TRAILING_STOP"
        assert OrderReason.MAX_HOLDING.value == "MAX_HOLDING"
        assert len(OrderReason) == 7


class TestPosition:
    def test_position_creation(self):
        pos = Position(
            stock_code="005930",
            quantity=10,
            avg_price=70000.0,
            profit_rate=2.5,
            current_price=71750,
        )
        assert pos.stock_code == "005930"
        assert pos.quantity == 10
        assert pos.avg_price == 70000.0
        assert pos.profit_rate == 2.5
        assert pos.current_price == 71750


class TestStockPrice:
    def test_stock_price_creation(self):
        sp = StockPrice(
            stock_code="005930",
            current_price=71750,
            upper_limit=90000,
            lower_limit=50000,
            change_rate=1.5,
            volume=10_000_000,
            trading_value=5_000_000_000_000,
            market_cap=400_000_000_000_000,
            is_stopped=False,
            is_managed=False,
            is_caution=False,
            is_clearing=False,
        )
        assert sp.stock_code == "005930"
        assert sp.current_price == 71750
        assert sp.is_stopped is False
        assert sp.is_clearing is False


class TestDailyCandle:
    def test_daily_candle_creation(self):
        dc = DailyCandle(
            date="20260310",
            close=10000,
            open=9900,
            high=10100,
            low=9800,
            volume=1_000_000,
        )
        assert dc.date == "20260310"
        assert dc.close == 10000
        assert dc.volume == 1_000_000


class TestScanResult:
    def test_scan_result_defaults(self):
        sr = ScanResult(
            scan_time="2026-03-10T10:00:00",
            holding_count=3,
            sell_count=1,
            buy_count=2,
            skip_count=5,
            error_count=0,
            api_call_count=15,
            elapsed_ms=1200,
        )
        assert sr.note is None
        assert sr.holding_count == 3
        assert sr.elapsed_ms == 1200
