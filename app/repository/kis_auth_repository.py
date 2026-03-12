from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from typing import TYPE_CHECKING

from app.config.settings import Settings
from app.core.exceptions import KisApiError

if TYPE_CHECKING:
    from app.core.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_TOKEN_CACHE_PATH = Path("data/.token_cache.json")


class KisAuthRepository:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._rate_limiter = rate_limiter
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._token_lock = asyncio.Lock()

    async def get_token(self) -> str:
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at:
                return self._access_token
        async with self._token_lock:
            if self._access_token and self._token_expires_at:
                if datetime.now() < self._token_expires_at:
                    return self._access_token
            if self._load_cached_token():
                return self._access_token
            await self._issue_token()
        return self._access_token

    def _load_cached_token(self) -> bool:
        try:
            if not _TOKEN_CACHE_PATH.exists():
                return False
            data = json.loads(_TOKEN_CACHE_PATH.read_text())
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() >= expires_at:
                return False
            self._access_token = data["token"]
            self._token_expires_at = expires_at
            logger.info("캐시된 토큰 재사용 (만료: %s)", expires_at)
            return True
        except Exception:
            return False

    def _save_token_cache(self) -> None:
        try:
            _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_CACHE_PATH.write_text(json.dumps({
                "token": self._access_token,
                "expires_at": self._token_expires_at.isoformat(),
            }))
        except Exception:
            logger.debug("토큰 캐시 저장 실패")

    async def _issue_token(self) -> None:
        body = {
            "grant_type": "client_credentials",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
        }
        for attempt in range(3):
            try:
                resp = await self._client.post("/oauth2/tokenP", json=body)
                data = resp.json()
            except Exception:
                logger.warning("토큰 발급 요청 실패 (시도 %d/3)", attempt + 1)
                await asyncio.sleep(30)
                continue
            if "access_token" in data:
                self._access_token = data["access_token"]
                expires_in = int(data.get("expires_in", 86400))
                self._token_expires_at = datetime.now() + timedelta(seconds=expires_in - 3600)
                self._save_token_cache()
                logger.info("KIS 토큰 발급 성공 (만료: %s)", self._token_expires_at)
                return
            logger.warning("토큰 발급 실패 (시도 %d/3): %s", attempt + 1, data)
            await asyncio.sleep(30)
        raise KisApiError("TOKEN_FAIL", "토큰 발급 3회 실패")

    async def get_hashkey(self, body: dict) -> str:
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()
        headers = {
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            resp = await self._client.post("/uapi/hashkey", json=body, headers=headers)
            data = resp.json()
            hashkey = data.get("HASH", "")
            if not hashkey:
                logger.warning("hashkey 발급 실패: %s", data)
            return hashkey
        except Exception:
            logger.exception("hashkey 요청 중 오류")
            return ""

    async def get_common_headers(self, tr_id: str) -> dict[str, str]:
        token = await self.get_token()
        return {
            "authorization": f"Bearer {token}",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
            "custtype": "P",
        }

    def get_tr_id(self, real_tr_id: str) -> str:
        if self._settings.kis_is_paper_trading and real_tr_id.startswith("TTTC"):
            return real_tr_id.replace("TTTC", "VTTC")
        return real_tr_id
