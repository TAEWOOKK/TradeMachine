"""recover_state() 테스트 — 서버 재시작 시 인메모리 상태 복구."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.model.domain import Position
from app.service.trading_service import TradingService


def _make_settings():
    s = AsyncMock()
    s.trailing_stop_activate = 8.0
    s.watch_list_codes = []
    return s


def _build_service(settings, order_repo=None, order_log_repo=None):
    return TradingService(
        auth_repo=AsyncMock(),
        market_repo=AsyncMock(),
        order_repo=order_repo or AsyncMock(),
        order_log_repo=order_log_repo or AsyncMock(),
        report_repo=AsyncMock(),
        settings=settings,
    )


@pytest.mark.asyncio
async def test_recover_state_restores_counts_and_trailing():
    """포지션이 있고 trailing 조건 충족 시 상태 복구."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = [
        Position(stock_code="005930", quantity=10, avg_price=50000.0,
                 profit_rate=10.0, current_price=55000),
        Position(stock_code="035720", quantity=5, avg_price=90000.0,
                 profit_rate=3.0, current_price=92700),
    ]
    order_log_repo = AsyncMock()
    order_log_repo.get_today_counts.return_value = {
        "buy_count": 2, "sell_count": 1, "fail_count": 0,
    }

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._daily_buy_count == 2
    assert "005930" in svc._highest_prices
    assert "035720" in svc._highest_prices
    assert "005930" in svc._trailing_activated
    assert "035720" not in svc._trailing_activated


@pytest.mark.asyncio
async def test_recover_state_balance_failure():
    """잔고 조회 실패 시 빈 상태로 graceful 복구."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.side_effect = Exception("network error")
    order_log_repo = AsyncMock()
    order_log_repo.get_today_counts.return_value = {
        "buy_count": 1, "sell_count": 0, "fail_count": 0,
    }

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._daily_buy_count == 1
    assert len(svc._highest_prices) == 0
    assert len(svc._trailing_activated) == 0


@pytest.mark.asyncio
async def test_recover_state_balance_none():
    """잔고 None 반환 시에도 안전하게 처리."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = None
    order_log_repo = AsyncMock()
    order_log_repo.get_today_counts.return_value = {
        "buy_count": 0, "sell_count": 0, "fail_count": 0,
    }

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._daily_buy_count == 0
    assert len(svc._highest_prices) == 0
