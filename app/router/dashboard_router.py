from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config.stock_names import get_name
from app.core.dependencies import get_order_log_repo, get_report_repo, get_trading_service
from app.core.event_bus import EventBus, get_event_bus
from app.repository.order_log_repository import OrderLogRepository
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
    next_min = (minute // 5 + 1) * 5
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

    if status.get("total_cash", 0) <= 0 and yesterday:
        status["total_cash"] = yesterday.get("total_cash", 0)
        status["total_assets"] = yesterday.get("total_assets", 0)

    return status


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
