from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config import constants as C
from app.config.stock_names import get_name
from app.core.dependencies import (
    get_event_bus,
    get_market_repo,
    get_order_log_repo,
    get_order_repo,
    get_report_repo,
    get_trading_service,
)
from app.core.event_bus import EventType, get_event_bus
from app.model.domain import OrderReason, OrderType
from app.model.dto import ManualBuyRequest, ManualSellRequest
from app.repository.market_data_repository import MarketDataRepository
from app.repository.order_log_repository import OrderLogRepository
from app.repository.order_repository import OrderRepository
from app.repository.report_repository import ReportRepository
from app.service.trading_service import TradingService

router = APIRouter(tags=["dashboard"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    html_path = _STATIC_DIR / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@router.get("/api/status")
async def get_status(
    svc: TradingService = Depends(get_trading_service),
    order_log_repo: OrderLogRepository = Depends(get_order_log_repo),
    report_repo: ReportRepository = Depends(get_report_repo),
) -> dict:
    status = svc.status
    now = datetime.now()
    minute = now.minute
    next_min = (minute // 3 + 1) * 3
    if next_min >= 60:
        next_scan = now.replace(hour=now.hour + 1, minute=0, second=0, microsecond=0)
    else:
        next_scan = now.replace(minute=next_min, second=0, microsecond=0)
    remaining = max(0, int((next_scan - now).total_seconds()))

    if not status["in_market_hours"]:
        remaining = -1

    status["next_scan_seconds"] = remaining
    status["next_scan_time"] = next_scan.strftime("%H:%M") if remaining >= 0 else None

    today = now.strftime("%Y-%m-%d")
    realized = await order_log_repo.get_today_realized_pnl(today)
    status["today_realized_pnl"] = realized["total_pnl"]
    status["today_trades"] = realized["trades"]

    yesterday = await report_repo.get_yesterday_report(today)
    prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
    status["cumulative_pnl"] = prev_cumulative + realized["total_pnl"]
    status["initial_assets"] = yesterday.get("total_assets", 0) if yesterday else 0
    status["watch_list_detail"] = [
        {"code": c, "name": get_name(c)} for c in status.get("watch_list", [])
    ]

    if status.get("total_cash", 0) <= 0 and yesterday:
        status["total_cash"] = yesterday.get("total_cash", 0)
        status["total_assets"] = yesterday.get("total_assets", 0)

    return status


@router.get("/api/asset-breakdown")
async def get_asset_breakdown(
    svc: TradingService = Depends(get_trading_service),
    order_log_repo: OrderLogRepository = Depends(get_order_log_repo),
    report_repo: ReportRepository = Depends(get_report_repo),
) -> dict:
    """총 자산과 누적손익의 차이를 설명하는 상세 breakdown (수수료·세금·입출금 등)."""
    today = datetime.now().strftime("%Y-%m-%d")
    cumulative_pnl = 0
    baseline = 0
    deposit_sum = 0
    fees_total = 0
    fees_breakdown = {"buy_fee": 0, "sell_fee": 0, "sell_tax": 0}

    try:
        realized = await order_log_repo.get_today_realized_pnl(today)
        yesterday = await report_repo.get_yesterday_report(today)
        prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
        cumulative_pnl = prev_cumulative + realized["total_pnl"]
    except Exception as e:
        logger.warning("asset-breakdown: cumulative_pnl 실패: %s", e)

    try:
        fees = await order_log_repo.get_estimated_fees_taxes()
        fees_total = fees["total"]
        fees_breakdown = {
            "buy_fee": fees["buy_fee"],
            "sell_fee": fees["sell_fee"],
            "sell_tax": fees["sell_tax"],
        }
    except Exception as e:
        logger.warning("asset-breakdown: fees 실패: %s", e)

    try:
        deposit_sum = await report_repo.get_deposit_withdrawal_sum()
    except Exception as e:
        logger.warning("asset-breakdown: deposit_withdrawal 실패: %s", e)

    try:
        first_report = await report_repo.get_first_report()
        baseline = first_report.get("total_assets", 0) if first_report else 0
    except Exception as e:
        logger.warning("asset-breakdown: baseline 실패: %s", e)

    total_assets = 0
    try:
        acct = svc.status
        total_assets = acct.get("total_assets", 0)
        if total_assets <= 0:
            yesterday = await report_repo.get_yesterday_report(today)
            if yesterday:
                total_assets = yesterday.get("total_assets", 0)
    except Exception as e:
        logger.warning("asset-breakdown: total_assets 실패: %s", e)

    other_diff = total_assets - baseline - cumulative_pnl + fees_total - deposit_sum

    return {
        "total_assets": total_assets,
        "cumulative_pnl": cumulative_pnl,
        "estimated_fees_taxes": fees_total,
        "estimated_breakdown": fees_breakdown,
        "deposit_withdrawal_sum": deposit_sum,
        "baseline_assets": baseline,
        "other_diff": other_diff,
    }


@router.get("/api/positions")
async def get_positions(
    svc: TradingService = Depends(get_trading_service),
) -> list[dict]:
    return [
        {
            "stock_code": p.stock_code,
            "stock_name": get_name(p.stock_code),
            "quantity": p.quantity,
            "avg_price": p.avg_price,
            "current_price": p.current_price,
            "profit_rate": round(
                (p.current_price - p.avg_price) / p.avg_price * 100
                if p.avg_price > 0 else 0, 2,
            ),
            "eval_amount": p.current_price * p.quantity,
            "profit_amount": int((p.current_price - p.avg_price) * p.quantity),
        }
        for p in svc.positions
    ]


@router.get("/api/last-scan")
async def get_last_scan(
    svc: TradingService = Depends(get_trading_service),
) -> dict | None:
    sr = svc.last_scan
    if sr is None:
        return None
    return {
        "scan_time": sr.scan_time,
        "holding_count": sr.holding_count,
        "sell_count": sr.sell_count,
        "buy_count": sr.buy_count,
        "skip_count": sr.skip_count,
        "error_count": sr.error_count,
        "api_call_count": sr.api_call_count,
        "elapsed_ms": sr.elapsed_ms,
    }


@router.get("/api/events/history")
async def get_event_history(
    report_repo: ReportRepository = Depends(get_report_repo),
) -> list[dict]:
    bus = get_event_bus()
    mem_events = bus.recent_events
    if len(mem_events) >= 5:
        return [
            {
                "type": e.type.value,
                "message": e.message,
                "timestamp": e.timestamp,
                "data": e.data,
            }
            for e in mem_events[-200:]
        ]
    return await report_repo.get_recent_bot_events(limit=200)


@router.get("/api/events/errors")
async def get_error_history(
    report_repo: ReportRepository = Depends(get_report_repo),
) -> list[dict]:
    bus = get_event_bus()
    mem_errors = bus.recent_errors
    if mem_errors:
        return [
            {
                "type": e.type.value,
                "message": e.message,
                "timestamp": e.timestamp,
                "data": e.data,
            }
            for e in mem_errors
        ]
    rows = await report_repo.get_recent_bot_events(limit=50)
    return [r for r in rows if r["type"] == "error"]


@router.get("/api/performance")
async def get_performance(
    svc: TradingService = Depends(get_trading_service),
    order_log_repo: OrderLogRepository = Depends(get_order_log_repo),
    report_repo: ReportRepository = Depends(get_report_repo),
) -> dict:
    history = await report_repo.get_performance_history(days=90)
    capital_events = await report_repo.get_capital_events(days=90)

    today = datetime.now().strftime("%Y-%m-%d")
    already_has_today = any(h["report_date"] == today for h in history)

    if not already_has_today:
        acct = svc.status
        realized = await order_log_repo.get_today_realized_pnl(today)
        yesterday = await report_repo.get_yesterday_report(today)
        prev_cumulative = yesterday.get("cumulative_pnl", 0) if yesterday else 0
        total_assets = acct.get("total_assets", 0)
        total_cash = acct.get("total_cash", 0)

        if total_assets <= 0 and yesterday:
            total_assets = yesterday.get("total_assets", 0)
            total_cash = yesterday.get("total_cash", 0)

        if total_assets > 0:
            history.append({
                "report_date": today,
                "eval_amount": acct.get("stock_eval", 0),
                "eval_profit": realized["total_pnl"],
                "profit_rate": 0.0,
                "total_cash": total_cash,
                "total_assets": total_assets,
                "deposit_withdrawal": 0,
                "cumulative_pnl": prev_cumulative + realized["total_pnl"],
            })

    return {
        "history": history,
        "capital_events": capital_events,
    }


@router.get("/api/trades")
async def get_trades(
    order_log_repo: OrderLogRepository = Depends(get_order_log_repo),
) -> list[dict]:
    trades = await order_log_repo.get_recent_orders(limit=50)
    for t in trades:
        t["stock_name"] = get_name(t["stock_code"])
    return trades


@router.get("/api/watch-list-prices")
async def get_watch_list_prices(
    svc: TradingService = Depends(get_trading_service),
    market_repo: MarketDataRepository = Depends(get_market_repo),
) -> list[dict]:
    """관심 종목 현재가·등락률 일괄 조회 (수동 매수용)."""
    codes = svc.status.get("watch_list", [])
    if not codes:
        return []

    async def _fetch(code: str) -> dict:
        try:
            p = await market_repo.get_current_price(code)
            return {"code": code, "name": get_name(code), "current_price": p.current_price, "change_rate": round(p.change_rate, 1)}
        except Exception:
            return {"code": code, "name": get_name(code), "current_price": 0, "change_rate": 0}

    results = await asyncio.gather(*[_fetch(c) for c in codes])
    return list(results)


@router.get("/api/fee-config")
async def get_fee_config() -> dict:
    """수동 매도 시 수수료·세금 계산용 상수."""
    return {
        "sell_fee_rate": C.SELL_FEE_RATE,
        "sell_tax_rate": C.SELL_TAX_RATE,
        "buy_fee_rate": C.BUY_FEE_RATE,
    }


@router.post("/api/manual-sell")
async def manual_sell(
    body: ManualSellRequest,
    svc: TradingService = Depends(get_trading_service),
    order_repo: OrderRepository = Depends(get_order_repo),
    order_log_repo: OrderLogRepository = Depends(get_order_log_repo),
) -> dict:
    """수동 매도. quantity 생략 시 전량 매도."""
    stock_code = body.stock_code
    quantity = body.quantity
    if not stock_code:
        raise HTTPException(status_code=400, detail="stock_code가 필요합니다")

    pos = next((p for p in svc.positions if p.stock_code == stock_code), None)
    if not pos:
        raise HTTPException(status_code=404, detail="보유 종목이 아닙니다")
    qty = quantity if quantity is not None and 0 < quantity <= pos.quantity else pos.quantity
    if qty <= 0:
        raise HTTPException(status_code=400, detail="수량이 올바르지 않습니다")

    result = await order_repo.execute_order(
        stock_code=stock_code,
        order_type=OrderType.SELL,
        quantity=qty,
        price=pos.current_price,
    )
    if result.success:
        await order_log_repo.save_order(
            stock_code, OrderType.SELL, OrderReason.MANUAL,
            qty, pos.current_price, result, datetime.now().isoformat(),
        )
        await svc.refresh_holdings()
        from app.core.event_bus import BotEvent

        bus = get_event_bus()
        name = get_name(stock_code)
        bus.emit(BotEvent(
            EventType.ORDER_EXEC,
            f"✋ 수동 매도: {name} {qty}주 × {pos.current_price:,}원",
            data={
                "type": "SELL", "code": stock_code, "qty": qty,
                "price": pos.current_price, "success": True, "reason": "MANUAL",
            },
        ))
    return {
        "success": result.success,
        "order_no": result.order_no,
        "message": result.error_message or "매도 완료",
    }


@router.post("/api/manual-buy")
async def manual_buy(
    body: ManualBuyRequest,
    svc: TradingService = Depends(get_trading_service),
    order_repo: OrderRepository = Depends(get_order_repo),
    order_log_repo: OrderLogRepository = Depends(get_order_log_repo),
    market_repo: MarketDataRepository = Depends(get_market_repo),
) -> dict:
    """수동 매수. 관심 종목에서 선택해 매수."""
    stock_code = body.stock_code
    quantity = body.quantity
    status = svc.status
    if stock_code not in status.get("watch_list", []):
        raise HTTPException(status_code=400, detail="관심 종목에 없는 종목입니다")

    try:
        price_data = await market_repo.get_current_price(stock_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"현재가 조회 실패: {e}") from e

    if price_data.current_price <= 0:
        raise HTTPException(status_code=400, detail="현재가를 조회할 수 없습니다")

    result = await order_repo.execute_order(
        stock_code=stock_code,
        order_type=OrderType.BUY,
        quantity=quantity,
        price=price_data.current_price,
    )
    if result.success:
        await order_log_repo.save_order(
            stock_code, OrderType.BUY, OrderReason.MANUAL,
            quantity, price_data.current_price, result, datetime.now().isoformat(),
        )
        await svc.refresh_holdings()
        from app.core.event_bus import BotEvent

        bus = get_event_bus()
        name = get_name(stock_code)
        bus.emit(BotEvent(
            EventType.ORDER_EXEC,
            f"✋ 수동 매수: {name} {quantity}주 × {price_data.current_price:,}원",
            data={
                "type": "BUY", "code": stock_code, "qty": quantity,
                "price": price_data.current_price, "success": True, "reason": "MANUAL",
            },
        ))
    return {
        "success": result.success,
        "order_no": result.order_no,
        "message": result.error_message or "매수 완료",
    }


@router.get("/api/events/stream")
async def event_stream() -> StreamingResponse:
    bus = get_event_bus()

    async def generate():
        yield f"data: {json.dumps({'type': 'connected', 'message': 'SSE 연결됨'})}\n\n"
        async for event in bus.subscribe():
            payload = json.dumps({
                "type": event.type.value,
                "message": event.message,
                "timestamp": event.timestamp,
                "data": event.data,
            }, ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
