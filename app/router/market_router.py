from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import get_market_repo, get_order_repo
from app.model.dto import BalanceResponse, StockPriceResponse
from app.repository.market_data_repository import MarketDataRepository
from app.repository.order_repository import OrderRepository

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/price/{stock_code}", response_model=StockPriceResponse)
async def get_stock_price(
    stock_code: str,
    market_repo: MarketDataRepository = Depends(get_market_repo),
) -> StockPriceResponse:
    price = await market_repo.get_current_price(stock_code)
    return StockPriceResponse(
        stock_code=price.stock_code,
        current_price=price.current_price,
        change_rate=price.change_rate,
        volume=price.volume,
    )


@router.get("/balance", response_model=list[BalanceResponse])
async def get_balance(
    order_repo: OrderRepository = Depends(get_order_repo),
) -> list[BalanceResponse]:
    positions = await order_repo.get_balance()
    if positions is None:
        return []
    return [
        BalanceResponse(
            stock_code=p.stock_code,
            stock_name="",
            quantity=p.quantity,
            avg_price=p.avg_price,
            current_price=p.current_price,
            profit_rate=p.profit_rate,
        )
        for p in positions
    ]
