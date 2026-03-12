from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config.stock_names import get_name
from app.core.dependencies import get_report_repo, get_trading_service
from app.core.event_bus import EventBus, get_event_bus
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
async def get_event_history() -> list[dict]:
    bus = get_event_bus()
    return [
        {
            "type": e.type.value,
            "message": e.message,
            "timestamp": e.timestamp,
            "data": e.data,
        }
        for e in bus.recent_events[-100:]
    ]


@router.get("/api/events/errors")
async def get_error_history() -> list[dict]:
    bus = get_event_bus()
    return [
        {
            "type": e.type.value,
            "message": e.message,
            "timestamp": e.timestamp,
            "data": e.data,
        }
        for e in bus.recent_errors
    ]


@router.get("/api/performance")
async def get_performance(
    report_repo: ReportRepository = Depends(get_report_repo),
) -> dict:
    history = await report_repo.get_performance_history(days=90)
    capital_events = await report_repo.get_capital_events(days=90)
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
