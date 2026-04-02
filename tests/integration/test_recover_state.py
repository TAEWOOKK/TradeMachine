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
    s.max_daily_buy_count = 3
    return s


def _make_order_log_repo(
    counts: dict | None = None,
    trailing_states: list[dict] | None = None,
):
    repo = AsyncMock()
    repo.get_today_counts.return_value = counts or {
        "buy_count": 0, "sell_count": 0, "fail_count": 0,
    }
    repo.load_trailing_states.return_value = trailing_states or []
    return repo


def _make_report_repo():
    repo = AsyncMock()
    repo.get_yesterday_report.return_value = None
    return repo


def _build_service(settings, order_repo=None, order_log_repo=None):
    return TradingService(
        auth_repo=AsyncMock(),
        market_repo=AsyncMock(),
        order_repo=order_repo or AsyncMock(),
        order_log_repo=order_log_repo or _make_order_log_repo(),
        report_repo=_make_report_repo(),
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
    order_log_repo = _make_order_log_repo(
        counts={"buy_count": 2, "sell_count": 1, "fail_count": 0},
    )

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._daily_buy_count == 2
    assert "005930" in svc._highest_prices
    assert "035720" in svc._highest_prices
    assert "005930" in svc._trailing_activated
    assert "035720" not in svc._trailing_activated


@pytest.mark.asyncio
async def test_recover_state_restores_db_highest_price():
    """DB에 저장된 고점이 현재가보다 높으면 DB 값을 사용."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = [
        Position(stock_code="005930", quantity=10, avg_price=50000.0,
                 profit_rate=8.0, current_price=54000),
    ]
    order_log_repo = _make_order_log_repo(
        counts={"buy_count": 1, "sell_count": 0, "fail_count": 0},
        trailing_states=[
            {"stock_code": "005930", "highest_price": 58000, "activated": 1},
        ],
    )

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._highest_prices["005930"] == 58000
    assert "005930" in svc._trailing_activated


@pytest.mark.asyncio
async def test_recover_state_prefers_current_if_higher():
    """현재가가 DB 고점보다 높으면 현재가를 사용."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = [
        Position(stock_code="005930", quantity=10, avg_price=50000.0,
                 profit_rate=20.0, current_price=60000),
    ]
    order_log_repo = _make_order_log_repo(
        counts={"buy_count": 1, "sell_count": 0, "fail_count": 0},
        trailing_states=[
            {"stock_code": "005930", "highest_price": 58000, "activated": 1},
        ],
    )

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._highest_prices["005930"] == 60000


@pytest.mark.asyncio
async def test_recover_state_cleans_stale_trailing():
    """보유하지 않는 종목의 trailing state는 DB에서 정리."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = []
    order_log_repo = _make_order_log_repo(
        trailing_states=[
            {"stock_code": "999999", "highest_price": 10000, "activated": 1},
        ],
    )

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert "999999" not in svc._highest_prices
    order_log_repo.cleanup_trailing_states.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_state_balance_failure():
    """잔고 조회 실패 시 빈 상태로 graceful 복구."""
    settings = _make_settings()
    order_repo = AsyncMock()
    order_repo.get_balance.side_effect = Exception("network error")
    order_log_repo = _make_order_log_repo(
        counts={"buy_count": 1, "sell_count": 0, "fail_count": 0},
    )

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
    order_log_repo = _make_order_log_repo()

    svc = _build_service(settings, order_repo=order_repo, order_log_repo=order_log_repo)
    await svc.recover_state()

    assert svc._daily_buy_count == 0
    assert len(svc._highest_prices) == 0
