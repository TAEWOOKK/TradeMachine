from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from app.config import constants as C
from app.config.settings import Settings
from app.core.cache import TTLCache
from app.core.exceptions import KisApiError
from app.core.rate_limiter import RateLimiter
from app.core.utils import safe_float, safe_int
from app.model.domain import DailyCandle, StockPrice
from app.repository.kis_auth_repository import KisAuthRepository

logger = logging.getLogger(__name__)


class MarketDataRepository:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        auth_repo: KisAuthRepository,
        cache: TTLCache,
        rate_limiter: RateLimiter,
    ) -> None:
        self._client = client
        self._settings = settings
        self._auth_repo = auth_repo
        self._cache = cache
        self._rate_limiter = rate_limiter
        self._api_call_count: int = 0

    def reset_api_count(self) -> None:
        self._api_call_count = 0

    @property
    def api_call_count(self) -> int:
        return self._api_call_count

    async def get_current_price(self, stock_code: str, market_code: str = "J") -> StockPrice:
        cache_key = f"price:{stock_code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        tr_id = "FHKST01010100"
        headers = await self._auth_repo.get_common_headers(tr_id)
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": stock_code,
        }
        data = await self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            headers,
            params,
        )
        output = data.get("output", {})

        result = StockPrice(
            stock_code=stock_code,
            current_price=safe_int(output.get("stck_prpr", "0")),
            upper_limit=safe_int(output.get("stck_mxpr", "0")),
            lower_limit=safe_int(output.get("stck_llam", "0")),
            change_rate=safe_float(output.get("prdy_ctrt", "0")),
            volume=safe_int(output.get("acml_vol", "0")),
            trading_value=safe_int(output.get("acml_tr_pbmn", "0")),
            market_cap=safe_int(output.get("hts_avls", "0")) * 100_000_000,
            is_stopped=output.get("temp_stop_yn", "N") == "Y",
            is_managed=output.get("mang_issu_cls_code", "00") not in ("00", "N", ""),
            is_caution=output.get("invt_caful_yn", "N") == "Y",
            is_clearing=output.get("sltr_yn", "N") == "Y",
        )
        self._cache.set(cache_key, result, 5)
        return result

    async def get_index_price(self, index_code: str = "0001") -> StockPrice:
        cache_key = f"index:{index_code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        tr_id = "FHPUP02100000"
        headers = await self._auth_repo.get_common_headers(tr_id)
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
        }
        data = await self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            headers,
            params,
        )
        output = data.get("output", {})
        price_str = output.get("bstp_nmix_prpr", "0")
        current_price = int(float(price_str)) if price_str else 0

        result = StockPrice(
            stock_code=index_code,
            current_price=current_price,
            upper_limit=0,
            lower_limit=0,
            change_rate=safe_float(output.get("bstp_nmix_prdy_ctrt", "0")),
            volume=safe_int(output.get("acml_vol", "0")),
            trading_value=safe_int(output.get("acml_tr_pbmn", "0")),
            market_cap=0,
            is_stopped=False,
            is_managed=False,
            is_caution=False,
            is_clearing=False,
        )
        self._cache.set(cache_key, result, 5)
        return result

    async def get_daily_chart(self, stock_code: str, days: int = 60) -> list[DailyCandle]:
        cache_key = f"chart:{stock_code}:{days}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        tr_id = "FHKST03010100"
        headers = await self._auth_repo.get_common_headers(tr_id)
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 15)).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        data = await self._request_get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers,
            params,
        )
        candles: list[DailyCandle] = []
        for item in data.get("output2", []):
            date_str = item.get("stck_bsop_date", "")
            if not date_str:
                continue
            candles.append(
                DailyCandle(
                    date=date_str,
                    close=safe_int(item.get("stck_clpr", "0")),
                    open=safe_int(item.get("stck_oprc", "0")),
                    high=safe_int(item.get("stck_hgpr", "0")),
                    low=safe_int(item.get("stck_lwpr", "0")),
                    volume=safe_int(item.get("acml_vol", "0")),
                )
            )
        candles.sort(key=lambda c: c.date, reverse=True)
        self._cache.set(cache_key, candles, 900)
        return candles

    async def _request_get(
        self,
        path: str,
        headers: dict[str, str],
        params: dict[str, str],
    ) -> dict:
        for attempt in range(C.MAX_API_RETRY):
            await self._rate_limiter.acquire()
            self._api_call_count += 1
            try:
                resp = await self._client.get(path, headers=headers, params=params)
            except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as net_err:
                logger.warning("API 네트워크 오류 (시도 %d/%d): %s — %s", attempt + 1, C.MAX_API_RETRY, path, net_err)
                await asyncio.sleep(C.API_RETRY_DELAY_SECONDS * (attempt + 1))
                continue

            if resp.status_code == 429:
                wait = C.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                logger.warning("HTTP 429 Rate Limit (시도 %d/%d), %ds 대기", attempt + 1, C.MAX_API_RETRY, wait)
                await asyncio.sleep(wait)
                continue

            data = resp.json()
            msg_cd = data.get("msg_cd", "")

            if msg_cd == "EGW00123":
                logger.warning("토큰 만료 감지, 재발급 후 재시도")
                headers = await self._auth_repo.get_common_headers(headers.get("tr_id", ""))
                continue

            if data.get("rt_cd") == "0":
                return data

            msg1 = data.get("msg1", "")
            logger.warning("API 오류 (시도 %d/%d): [%s] %s", attempt + 1, C.MAX_API_RETRY, msg_cd, msg1)

            if "초당" in msg1 or "거래건수" in msg1:
                wait = C.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                logger.info("초당 한도 초과 → %ds 대기 후 재시도", wait)
                await asyncio.sleep(wait)
            else:
                await asyncio.sleep(C.API_RETRY_DELAY_SECONDS)

        raise KisApiError("MAX_RETRY", f"API 호출 {C.MAX_API_RETRY}회 실패: {path}")
