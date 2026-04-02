from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OrderType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderReason(str, Enum):
    GOLDEN_CROSS = "GOLDEN_CROSS"
    UPTREND_ENTRY = "UPTREND_ENTRY"
    MOMENTUM_ENTRY = "MOMENTUM_ENTRY"
    SCALPING_ENTRY = "SCALPING_ENTRY"
    DEAD_CROSS = "DEAD_CROSS"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    MAX_HOLDING = "MAX_HOLDING"
    FRIDAY_CLOSE = "FRIDAY_CLOSE"  # 레거시 DB 기록용
    EOD_CLOSE = "EOD_CLOSE"  # 매 영업일 장마감 전량 청산
    MANUAL = "MANUAL"


@dataclass
class Position:
    stock_code: str
    quantity: int
    avg_price: float
    profit_rate: float
    current_price: int


@dataclass
class StockPrice:
    stock_code: str
    current_price: int
    upper_limit: int
    lower_limit: int
    change_rate: float
    volume: int
    trading_value: int
    market_cap: int
    is_stopped: bool
    is_managed: bool
    is_caution: bool
    is_clearing: bool


@dataclass
class DailyCandle:
    date: str
    close: int
    open: int
    high: int
    low: int
    volume: int


@dataclass
class MaResult:
    ma_short: list[float] = field(default_factory=list)
    ma_long: list[float] = field(default_factory=list)
    candles: list[DailyCandle] = field(default_factory=list)


@dataclass
class OrderResult:
    success: bool
    order_no: str | None
    error_message: str | None


@dataclass
class ScanResult:
    scan_time: str
    holding_count: int
    sell_count: int
    buy_count: int
    skip_count: int
    error_count: int
    api_call_count: int
    elapsed_ms: int
    note: str | None = None


@dataclass
class TodayCounts:
    buy_count: int
    sell_count: int
    fail_count: int


@dataclass
class RealizedTrade:
    stock_code: str
    quantity: int
    sell_price: int
    avg_buy_price: int
    pnl: int


@dataclass
class RealizedPnl:
    total_pnl: int
    trades: list[RealizedTrade]


@dataclass
class AccountSummary:
    total_cash: int
    stock_eval: int
    total_assets: int


@dataclass
class TrailingState:
    stock_code: str
    highest_price: int
    activated: bool


@dataclass
class EstimatedFees:
    buy_fee: int
    sell_fee: int
    sell_tax: int
    total: int


@dataclass
class BotEventRecord:
    type: str
    message: str
    timestamp: float
    data: dict | None
