from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import get_order_repo, get_trading_service
from app.model.dto import OrderRequest, OrderResponse
from app.model.domain import OrderType
from app.repository.order_repository import OrderRepository
from app.service.trading_service import TradingService

router = APIRouter(prefix="/trading", tags=["trading"])


@router.post("/scan")
async def trigger_scan(
    service: TradingService = Depends(get_trading_service),
) -> dict[str, str]:
    await service.run_scan()
    return {"status": "scan completed"}


@router.post("/order", response_model=OrderResponse)
async def manual_order(
    req: OrderRequest,
    order_repo: OrderRepository = Depends(get_order_repo),
) -> OrderResponse:
    order_type = OrderType(req.order_type)
    result = await order_repo.execute_order(
        stock_code=req.stock_code,
        order_type=order_type,
        quantity=req.quantity,
        price=req.price or 0,
    )
    return OrderResponse(
        success=result.success,
        order_no=result.order_no,
        message=result.error_message or "주문 완료",
    )
