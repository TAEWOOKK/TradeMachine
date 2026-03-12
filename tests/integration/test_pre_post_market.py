from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.model.domain import OrderResult, Position
from app.service.trading_service import TradingService


def _build_service(
    mock_settings: MagicMock,
    *,
    market_repo: AsyncMock | None = None,
    order_repo: AsyncMock | None = None,
    order_log_repo: AsyncMock | None = None,
    report_repo: AsyncMock | None = None,
    auth_repo: AsyncMock | None = None,
) -> TradingService:
    return TradingService(
        auth_repo=auth_repo or AsyncMock(),
        market_repo=market_repo or AsyncMock(),
        order_repo=order_repo or AsyncMock(),
        order_log_repo=order_log_repo or AsyncMock(),
        report_repo=report_repo or AsyncMock(),
        settings=mock_settings,
    )


# ──────────────────────────────────────────────────────────────────────
# run_pre_market: 정상 초기화
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pre_market_initializes(mock_settings):
    """장전 처리: 토큰 발급, 카운터 초기화, trailing 정리."""
    auth_repo = AsyncMock()
    order_repo = AsyncMock()
    order_repo.get_balance.return_value = [
        Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=2.0, current_price=71400,
        ),
    ]
    order_repo.get_unfilled_orders.return_value = []

    svc = _build_service(
        mock_settings,
        auth_repo=auth_repo,
        order_repo=order_repo,
    )
    svc._daily_buy_count = 5
    svc._highest_prices = {"005930": 72000, "000660": 90000}
    svc._trailing_activated = {"005930", "000660"}

    weekday = datetime(2026, 3, 11, 8, 50)  # Wednesday
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = weekday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_pre_market()

    auth_repo.get_token.assert_called_once()
    assert svc._daily_buy_count == 0
    assert "000660" not in svc._highest_prices
    assert "000660" not in svc._trailing_activated
    assert "005930" in svc._highest_prices


# ──────────────────────────────────────────────────────────────────────
# run_pre_market: 공휴일 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pre_market_holiday_skips(mock_settings):
    """KRX 휴장일에는 장전 처리를 건너뛴다."""
    auth_repo = AsyncMock()
    order_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        auth_repo=auth_repo,
        order_repo=order_repo,
    )

    holiday = datetime(2026, 1, 1, 8, 50)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = holiday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_pre_market()

    auth_repo.get_token.assert_not_called()
    order_repo.get_balance.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# run_post_market: 리포트 저장
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_market_saves_report(mock_settings):
    """장후 처리: 잔고 기반으로 daily_report와 balance_snapshot 저장."""
    positions = [
        Position(
            stock_code="005930", quantity=10,
            avg_price=70000.0, profit_rate=5.0, current_price=73500,
        ),
        Position(
            stock_code="000660", quantity=5,
            avg_price=80000.0, profit_rate=-2.0, current_price=78400,
        ),
    ]

    order_repo = AsyncMock()
    order_repo.get_balance.return_value = positions
    order_repo.get_unfilled_orders.return_value = []
    order_repo.get_account_summary.return_value = {
        "total_cash": 5_000_000, "stock_eval": 1_127_000, "total_assets": 6_127_000,
    }

    order_log_repo = AsyncMock()
    order_log_repo.get_today_counts.return_value = {
        "buy_count": 2,
        "sell_count": 1,
        "fail_count": 0,
    }

    report_repo = AsyncMock()
    report_repo.get_yesterday_report.return_value = None

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        order_log_repo=order_log_repo,
        report_repo=report_repo,
    )

    weekday = datetime(2026, 3, 11, 15, 30)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = weekday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_post_market()

    report_repo.save_daily_report.assert_called_once()
    call_kwargs = report_repo.save_daily_report.call_args[1]
    assert call_kwargs["buy_count"] == 2
    assert call_kwargs["sell_count"] == 1
    assert call_kwargs["holding_count"] == 2

    report_repo.save_balance_snapshot.assert_called_once()
    snap_date = report_repo.save_balance_snapshot.call_args[0][0]
    assert snap_date == "2026-03-11"


# ──────────────────────────────────────────────────────────────────────
# run_post_market: 공휴일 스킵
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_market_holiday_skips(mock_settings):
    """KRX 휴장일에는 장후 처리를 건너뛴다."""
    order_repo = AsyncMock()
    report_repo = AsyncMock()

    svc = _build_service(
        mock_settings,
        order_repo=order_repo,
        report_repo=report_repo,
    )

    holiday = datetime(2026, 1, 1, 15, 30)
    with patch("app.service.trading_service.datetime") as mock_dt:
        mock_dt.now.return_value = holiday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await svc.run_post_market()

    order_repo.get_balance.assert_not_called()
    report_repo.save_daily_report.assert_not_called()
