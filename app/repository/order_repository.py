from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.config.settings import Settings
from app.core.exceptions import KisApiError
from app.core.rate_limiter import RateLimiter
from app.core.utils import safe_float, safe_int
from app.model.domain import OrderResult, OrderType, Position
from app.repository.kis_auth_repository import KisAuthRepository

logger = logging.getLogger(__name__)


class OrderRepository:
    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        auth_repo: KisAuthRepository,
        rate_limiter: RateLimiter,
    ) -> None:
        self._client = client
        self._settings = settings
        self._auth_repo = auth_repo
        self._rate_limiter = rate_limiter

    async def get_balance(self) -> list[Position] | None:
        tr_id = self._auth_repo.get_tr_id("TTTC8434R")
        headers = await self._auth_repo.get_common_headers(tr_id)
        params = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            await self._rate_limiter.acquire()
            resp = await self._client.get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=headers,
                params=params,
            )
            data = resp.json()
        except httpx.TimeoutException as e:
            logger.warning("잔고 조회 타임아웃: %s", type(e).__name__)
            return None
        except httpx.RequestError as e:
            logger.warning("잔고 조회 네트워크 오류: %s — %s", type(e).__name__, e)
            return None
        except Exception as e:
            logger.warning("잔고 조회 예외: %s — %s", type(e).__name__, e)
            return None

        if data.get("rt_cd") != "0":
            logger.warning("잔고 조회 실패: [%s] %s", data.get("msg_cd", ""), data.get("msg1", ""))
            return None
        positions: list[Position] = []
        for item in data.get("output1", []):
            qty = safe_int(item.get("hldg_qty", "0"))
            if qty <= 0:
                continue
            positions.append(
                Position(
                    stock_code=item.get("pdno", ""),
                    quantity=qty,
                    avg_price=safe_float(item.get("pchs_avg_pric", "0")),
                    profit_rate=safe_float(item.get("evlu_pfls_rt", "0")),
                    current_price=safe_int(item.get("prpr", "0")),
                )
            )
        return positions

    async def get_unfilled_orders(self) -> list[dict]:
        tr_id = self._auth_repo.get_tr_id("TTTC8001R")
        headers = await self._auth_repo.get_common_headers(tr_id)
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "02",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            await self._rate_limiter.acquire()
            resp = await self._client.get(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                headers=headers,
                params=params,
            )
            data = resp.json()
        except (httpx.RequestError, Exception) as e:
            logger.warning("미체결 조회 요청 실패: %s", e)
            return []

        if data.get("rt_cd") != "0":
            logger.warning("미체결 조회 실패: [%s] %s", data.get("msg_cd", ""), data.get("msg1", ""))
            return []
        orders: list[dict] = []
        for item in data.get("output1", []):
            side_code = item.get("sll_buy_dvsn_cd", "")
            side = "SELL" if side_code == "01" else "BUY"
            orders.append({
                "stock_code": item.get("pdno", ""),
                "order_no": item.get("odno", ""),
                "side": side,
                "quantity": safe_int(item.get("ord_qty", "0")),
            })
        return orders

    async def cancel_order(self, order_no: str, quantity: int) -> bool:
        tr_id = self._auth_repo.get_tr_id("TTTC0803U")
        headers = await self._auth_repo.get_common_headers(tr_id)
        body = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        hashkey = await self._auth_repo.get_hashkey(body)
        headers["hashkey"] = hashkey

        try:
            await self._rate_limiter.acquire()
            resp = await self._client.post(
                "/uapi/domestic-stock/v1/trading/order-rvsecncl",
                headers=headers,
                json=body,
            )
            data = resp.json()
        except (httpx.RequestError, Exception) as e:
            logger.warning("주문 취소 요청 실패: %s — %s", order_no, e)
            return False

        if data.get("rt_cd") == "0":
            logger.info("주문 취소 성공: %s", order_no)
            return True
        logger.warning("주문 취소 실패: %s — [%s] %s", order_no, data.get("msg_cd", ""), data.get("msg1", ""))
        return False

    async def get_account_summary(self) -> dict[str, int] | None:
        """계좌 요약 정보 반환 (예수금, 주식평가, 총자산)."""
        tr_id = self._auth_repo.get_tr_id("TTTC8434R")
        headers = await self._auth_repo.get_common_headers(tr_id)
        params = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            await self._rate_limiter.acquire()
            resp = await self._client.get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=headers,
                params=params,
            )
            data = resp.json()
        except httpx.TimeoutException as e:
            logger.warning("계좌 요약 조회 타임아웃: %s", type(e).__name__)
            return None
        except httpx.RequestError as e:
            logger.warning("계좌 요약 네트워크 오류: %s — %s", type(e).__name__, e)
            return None
        except Exception as e:
            logger.warning("계좌 요약 예외: %s — %s", type(e).__name__, e)
            return None

        if data.get("rt_cd") != "0":
            logger.warning("계좌 요약 실패: [%s] %s", data.get("msg_cd", ""), data.get("msg1", ""))
            return None
        out2 = data.get("output2", [{}])
        summary = out2[0] if isinstance(out2, list) and out2 else out2
        total_assets = safe_int(summary.get("tot_evlu_amt", "0"))
        stock_eval = safe_int(summary.get("scts_evlu_amt", "0"))
        total_cash = total_assets - stock_eval
        if total_cash <= 0:
            dnca = safe_int(summary.get("dnca_tot_amt", "0"))
            if dnca > 0:
                total_cash = dnca
                logger.info("tot_evlu-scts=0 → dnca_tot_amt(예수금) %s원 사용", f"{dnca:,}")
        return {
            "total_cash": total_cash,
            "stock_eval": stock_eval,
            "total_assets": total_assets,
        }

    async def get_available_cash(self, stock_code: str) -> int:
        tr_id = self._auth_repo.get_tr_id("TTTC8908R")
        headers = await self._auth_repo.get_common_headers(tr_id)
        params = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "Y",
            "OVRS_ICLD_YN": "Y",
        }
        try:
            await self._rate_limiter.acquire()
            resp = await self._client.get(
                "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                headers=headers,
                params=params,
            )
            data = resp.json()
        except (httpx.RequestError, Exception) as e:
            logger.warning("매수가능 조회 요청 실패: %s", e)
            return 0

        if data.get("rt_cd") != "0":
            logger.warning("매수가능 조회 실패: [%s] %s", data.get("msg_cd", ""), data.get("msg1", ""))
            return 0
        output = data.get("output", {})
        return safe_int(output.get("ord_psbl_cash", "0"))

    async def execute_order(
        self,
        stock_code: str,
        order_type: OrderType,
        quantity: int,
        price: int = 0,
    ) -> OrderResult:
        if order_type == OrderType.BUY:
            raw_tr_id = "TTTC0802U"
        else:
            raw_tr_id = "TTTC0801U"

        tr_id = self._auth_repo.get_tr_id(raw_tr_id)
        headers = await self._auth_repo.get_common_headers(tr_id)
        body = {
            "CANO": self._settings.kis_cano,
            "ACNT_PRDT_CD": self._settings.kis_acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": "01",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }
        hashkey = await self._auth_repo.get_hashkey(body)
        headers["hashkey"] = hashkey

        try:
            await self._rate_limiter.acquire()
            resp = await self._client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                headers=headers,
                json=body,
            )
            data = resp.json()
        except (httpx.RequestError, Exception) as e:
            msg = f"주문 요청 실패: {e}"
            logger.warning("%s %s %d주 — %s", order_type.value, stock_code, quantity, msg)
            return OrderResult(success=False, order_no=None, error_message=msg)

        if data.get("rt_cd") == "0":
            order_no = data.get("output", {}).get("ODNO", "")
            logger.info("%s 주문 성공: %s %d주 (주문번호: %s)", order_type.value, stock_code, quantity, order_no)
            return OrderResult(success=True, order_no=order_no, error_message=None)

        msg = f"[{data.get('msg_cd', '')}] {data.get('msg1', '')}"
        logger.warning("%s 주문 실패: %s %d주 — %s", order_type.value, stock_code, quantity, msg)
        return OrderResult(success=False, order_no=None, error_message=msg)
