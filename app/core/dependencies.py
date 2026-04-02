from __future__ import annotations

import logging

import httpx

from app.config.settings import Settings
from app.core.cache import TTLCache
from app.core.database import Database
from app.core.event_bus import get_event_bus
from app.core.logging_config import setup_logging
from app.core.rate_limiter import RateLimiter
from app.repository.kis_auth_repository import KisAuthRepository
from app.repository.market_data_repository import MarketDataRepository
from app.repository.order_log_repository import OrderLogRepository
from app.repository.order_repository import OrderRepository
from app.repository.report_repository import ReportRepository
from app.scheduler.trading_scheduler import TradingScheduler
from app.service.buy_evaluator import BuyEvaluator
from app.service.indicator_service import IndicatorService
from app.service.market_guard import MarketGuard
from app.service.sell_evaluator import SellEvaluator
from app.service.trading_service import TradingService

logger = logging.getLogger(__name__)

_settings: Settings | None = None
_database: Database | None = None
_http_client: httpx.AsyncClient | None = None
_trading_service: TradingService | None = None
_trading_scheduler: TradingScheduler | None = None
_market_repo: MarketDataRepository | None = None
_order_repo: OrderRepository | None = None
_order_log_repo: OrderLogRepository | None = None
_report_repo: ReportRepository | None = None


async def init_dependencies() -> None:
    global _settings, _database, _http_client
    global _trading_service, _trading_scheduler, _market_repo, _order_repo
    global _order_log_repo, _report_repo

    setup_logging()

    try:
        _settings = Settings()
        _database = Database()
        await _database.connect()

        _http_client = httpx.AsyncClient(
            base_url=_settings.kis_base_url,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        )

        from app.config import constants as C_

        cache = TTLCache()
        rate_limiter = RateLimiter(max_per_second=C_.RATE_LIMIT_PER_SECOND)

        auth_repo = KisAuthRepository(_http_client, _settings, rate_limiter)
        _market_repo = MarketDataRepository(
            _http_client, _settings, auth_repo, cache, rate_limiter,
        )
        _order_repo = OrderRepository(_http_client, _settings, auth_repo, rate_limiter)
        _order_log_repo = OrderLogRepository(_database)
        _report_repo = ReportRepository(_database)

        event_bus = get_event_bus()
        event_bus.set_report_repo(_report_repo)

        indicator = IndicatorService(_settings)
        market_guard = MarketGuard(_settings, _market_repo)
        sell_evaluator = SellEvaluator(
            _settings, _market_repo, _order_repo, _order_log_repo,
            indicator, market_guard, event_bus,
        )
        buy_evaluator = BuyEvaluator(
            _settings, _market_repo, _order_repo, _order_log_repo,
            _report_repo, indicator, event_bus,
        )

        _trading_service = TradingService(
            auth_repo=auth_repo,
            market_repo=_market_repo,
            order_repo=_order_repo,
            order_log_repo=_order_log_repo,
            report_repo=_report_repo,
            settings=_settings,
            event_bus=event_bus,
            indicator=indicator,
            sell_evaluator=sell_evaluator,
            buy_evaluator=buy_evaluator,
            market_guard=market_guard,
        )

        await _trading_service.recover_state()

        _trading_scheduler = TradingScheduler(_trading_service)
        _trading_scheduler.start()

    except Exception:
        logger.exception("의존성 초기화 실패 — 리소스 정리")
        await close_dependencies()
        raise


async def close_dependencies() -> None:
    global _trading_scheduler, _http_client, _database
    global _trading_service, _market_repo, _order_repo, _settings, _report_repo

    if _trading_scheduler:
        _trading_scheduler.stop()
        _trading_scheduler = None
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    if _database:
        await _database.disconnect()
        _database = None
    _trading_service = None
    _market_repo = None
    _order_repo = None
    _order_log_repo = None
    _report_repo = None
    _settings = None


def get_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("Dependencies not initialized")
    return _settings


def get_market_repo() -> MarketDataRepository:
    if _market_repo is None:
        raise RuntimeError("Dependencies not initialized")
    return _market_repo


def get_order_repo() -> OrderRepository:
    if _order_repo is None:
        raise RuntimeError("Dependencies not initialized")
    return _order_repo


def get_order_log_repo() -> OrderLogRepository:
    if _order_log_repo is None:
        raise RuntimeError("Dependencies not initialized")
    return _order_log_repo


def get_report_repo() -> ReportRepository:
    if _report_repo is None:
        raise RuntimeError("Dependencies not initialized")
    return _report_repo


def get_trading_service() -> TradingService:
    if _trading_service is None:
        raise RuntimeError("Dependencies not initialized")
    return _trading_service
