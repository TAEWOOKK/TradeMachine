from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    stock_code: str
    order_type: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    price: int | None = Field(default=None, ge=0)


class ManualSellRequest(BaseModel):
    stock_code: str
    quantity: int | None = Field(default=None, gt=0)


class ManualBuyRequest(BaseModel):
    stock_code: str
    quantity: int = Field(gt=0)


class OrderResponse(BaseModel):
    success: bool
    order_no: str | None
    message: str


class BalanceResponse(BaseModel):
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: int
    profit_rate: float


class StockPriceResponse(BaseModel):
    stock_code: str
    current_price: int
    change_rate: float
    volume: int
