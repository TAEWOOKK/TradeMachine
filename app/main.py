from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.dependencies import close_dependencies, init_dependencies
from app.core.exceptions import KisApiError, RateLimitError, TokenExpiredError
from app.router.dashboard_router import router as dashboard_router
from app.router.market_router import router as market_router
from app.router.trading_router import router as trading_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_dependencies()
    logger.info("앱 시작 완료")
    yield
    await close_dependencies()
    logger.info("앱 정상 종료")


def create_app() -> FastAPI:
    application = FastAPI(title="TradeMachine", lifespan=lifespan)

    @application.exception_handler(KisApiError)
    async def kis_api_error_handler(request: Request, exc: KisApiError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @application.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": "API 호출 제한 초과"})

    @application.exception_handler(TokenExpiredError)
    async def token_expired_handler(request: Request, exc: TokenExpiredError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": "토큰 만료"})

    application.include_router(dashboard_router)
    application.include_router(market_router)
    application.include_router(trading_router)
    return application


app = create_app()
