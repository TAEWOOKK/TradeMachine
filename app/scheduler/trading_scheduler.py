from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.service.trading_service import TradingService

logger = logging.getLogger(__name__)


class TradingScheduler:
    def __init__(self, trading_service: TradingService) -> None:
        self._service = trading_service
        self._scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    def start(self) -> None:
        self._scheduler.add_job(
            self._service.run_pre_market,
            "cron", hour=8, minute=50, day_of_week="mon-fri",
            id="pre_market", replace_existing=True, max_instances=1,
        )
        self._scheduler.add_job(
            self._service.run_scan,
            "cron", day_of_week="mon-fri", hour="9-14", minute="*/5",
            id="scan_cycle_main", replace_existing=True, max_instances=1,
        )
        self._scheduler.add_job(
            self._service.run_scan,
            "cron", day_of_week="mon-fri", hour=15, minute="0,5,10,15,20,25",
            id="scan_cycle_closing", replace_existing=True, max_instances=1,
        )
        self._scheduler.add_job(
            self._service.run_friday_close,
            "cron", hour=15, minute=28, day_of_week="fri",
            id="friday_close", replace_existing=True, max_instances=1,
        )
        self._scheduler.add_job(
            self._service.run_post_market,
            "cron", hour=15, minute=30, day_of_week="mon-fri",
            id="post_market", replace_existing=True, max_instances=1,
        )
        self._scheduler.start()
        logger.info("트레이딩 스케줄러 시작")

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("트레이딩 스케줄러 정지")
