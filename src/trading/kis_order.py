"""KIS 현금주문 프리미티브 — hashkey·매수가능조회·현금주문·잔고.

읽기전용 datasource(시세)와 분리된 주문 쓰기 레이어. KisTokenManager 재사용.
TR_ID는 KIS 공식 examples_llm(2026-06 검증) 기준 — 2025 NXT 대체거래소 개편 반영
(구 VTTC0802U/0801U → VTTC0012U/0011U, body에 EXCG_ID_DVSN_CD 추가).
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Literal

import httpx

from src.datasource.kis.token import KisTokenManager

logger = logging.getLogger(__name__)

MAX_RETRY = 3
ORDER_TIMEOUT = 30.0  # 주문은 응답 지연 가능 → 읽기(10s)보다 길게
Side = Literal["buy", "sell"]


class KisOrderError(Exception):
    """주문/조회 실패 (rt_cd != 0 등)."""


class KisOrderHardStop(Exception):
    """429/인증오류 — 재시도 금지, 즉시 중단."""


# 모의/실전 × 매수/매도 주문 TR_ID (공식 examples_llm 2026-06 검증)
_ORDER_TR: dict[tuple[str, str], str] = {
    ("paper", "buy"): "VTTC0012U",
    ("paper", "sell"): "VTTC0011U",
    ("real", "buy"): "TTTC0012U",
    ("real", "sell"): "TTTC0011U",
}
_PSBL_TR = {"paper": "VTTC8908R", "real": "TTTC8908R"}
_BALANCE_TR = {"paper": "VTTC8434R", "real": "TTTC8434R"}
# 주식일별주문체결조회 TR_ID (NXT 개편 반영, 공식 examples_llm 2026-06 검증).
# 옛 8001R 아님 — 주문 TR(0802U→0012U)처럼 8001R→0081R로 재번호됨.
# 당일 조회만 사용하므로 3개월내 코드만 둔다(>3개월 CTSC9215R/VTSC9215R 미사용).
_CCLD_TR = {"paper": "VTTC0081R", "real": "TTTC0081R"}


def _split_account(account_no: str) -> tuple[str, str]:
    """'50123456-01' 또는 '5012345601' → (CANO, ACNT_PRDT_CD)."""
    s = account_no.replace("-", "").strip()
    return s[:8], (s[8:10] if len(s) >= 10 else "01")


def _norm_odno(x: Any) -> str:
    """주문번호 정규화 — 접수응답 ODNO('0001')와 체결조회 odno(zero-pad) 비교용."""
    return str(x or "").strip().lstrip("0") or "0"


def parse_ccld_fill(data: dict[str, Any], odno: str) -> dict[str, Any] | None:
    """체결조회 output1에서 특정 주문번호(odno)의 체결결과 집계 (순수 함수, 테스트 가능).

    같은 주문이 분할체결로 여러 행일 수 있어 수량가중 평균가로 합산한다.
    반환: {filled_qty, avg_price, ord_qty, rmn_qty}. 주문 미발견 시 None
    (None=조회 시점 미반영/오류와 구분 → 호출측이 '확인불가'로 처리).
    filled_qty==0 이면 '확인됐으나 미체결'을 의미한다.
    """
    target = _norm_odno(odno)
    rows = [
        r for r in (data.get("output1") or [])
        if _norm_odno(r.get("odno")) == target
    ]
    if not rows:
        return None
    filled = 0
    amt = 0.0
    ord_qty = 0
    rmn = 0
    for r in rows:
        q = int(float(r.get("tot_ccld_qty", "0") or 0))
        avg = float(r.get("avg_prvs", "0") or 0)
        filled += q
        amt += q * avg
        ord_qty = max(ord_qty, int(float(r.get("ord_qty", "0") or 0)))
        rmn = int(float(r.get("rmn_qty", "0") or 0))  # 마지막 행의 잔여수량
    avg_price = (amt / filled) if filled else 0.0
    return {"filled_qty": filled, "avg_price": avg_price, "ord_qty": ord_qty, "rmn_qty": rmn}


class KisOrderClient:
    """KIS 현금주문 클라이언트. KisAdapter와 동일 생성자 시그니처."""

    def __init__(self, app_key: str, app_secret: str, account_no: str, env: str = "paper") -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._env = env
        self._token_mgr = KisTokenManager(app_key, app_secret, env)
        self._base = self._token_mgr.base_url
        self._cano, self._acnt = _split_account(account_no)

    async def hashkey(self, body: dict[str, Any]) -> str:
        """주문 body 무결성 해시 발급. POST /uapi/hashkey → 응답 HASH."""
        await self._token_mgr.get_token()
        url = f"{self._base}/uapi/hashkey"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()["HASH"]

    async def _post(
        self, path: str, tr_id: str, body: dict[str, Any], *, use_hash: bool = True,
        retries: int = MAX_RETRY, timeout: float = 10.0,
    ) -> dict[str, Any]:
        """POST + (선택)hashkey + 재시도·백오프·HARD STOP·rt_cd 검증.

        ⚠️ 실주문은 retries=1(멱등)로 호출 — 응답유실 후 재시도가 중복주문이 되는 걸 방지.
        """
        await self._token_mgr.get_token()
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(retries):
            if attempt > 0:
                wait = random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1)))
                logger.info("kis_order_retry path=%s attempt=%d wait=%.1fs", path, attempt, wait)
                await asyncio.sleep(wait)
            else:
                await asyncio.sleep(random.uniform(0.2, 0.6))

            headers = self._token_mgr.auth_headers(tr_id)
            if use_hash:
                headers["hashkey"] = await self.hashkey(body)
            try:
                async with httpx.AsyncClient(timeout=timeout) as http:
                    resp = await http.post(url, headers=headers, json=body)
                if resp.status_code in (429, 401, 403):
                    raise KisOrderHardStop(f"HTTP {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                rt_cd = data.get("rt_cd")
                if rt_cd is not None and rt_cd != "0":
                    raise KisOrderError(f"rt_cd={rt_cd} msg={data.get('msg1', '')}")
                return data
            except (KisOrderHardStop, KisOrderError):
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("kis_order_post_error path=%s attempt=%d error=%s", path, attempt, exc)
        raise KisOrderError(f"{path} failed after {retries} attempts") from last_exc

    async def _get(self, path: str, tr_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """조회 GET + 재시도·백오프·HARD STOP·rt_cd 검증 (주문용 _post의 GET 버전)."""
        await self._token_mgr.get_token()
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRY):
            if attempt > 0:
                await asyncio.sleep(random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1))))
            else:
                await asyncio.sleep(random.uniform(0.2, 0.6))
            headers = self._token_mgr.auth_headers(tr_id)
            try:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    resp = await http.get(url, headers=headers, params=params)
                if resp.status_code in (429, 401, 403):
                    raise KisOrderHardStop(f"HTTP {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                rt_cd = data.get("rt_cd")
                if rt_cd is not None and rt_cd != "0":
                    raise KisOrderError(f"rt_cd={rt_cd} msg={data.get('msg1', '')}")
                return data
            except (KisOrderHardStop, KisOrderError):
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("kis_order_get_error path=%s attempt=%d error=%s", path, attempt, exc)
        raise KisOrderError(f"{path} failed after {MAX_RETRY} attempts") from last_exc

    async def order_cash(
        self, side: Side, ticker: str, qty: int, price: int = 0,
        ord_dvsn: str = "01", excg: str = "KRX",
    ) -> dict[str, Any]:
        """현금 매수/매도 주문. ord_dvsn '01'=시장가(ORD_UNPR=0), '00'=지정가."""
        tr_id = _ORDER_TR[(self._env, side)]
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "EXCG_ID_DVSN_CD": excg,
            "SLL_TYPE": "01" if side == "sell" else "",
            "CNDT_PRIC": "",
        }
        # retries=1: 주문은 멱등 보장을 위해 재시도 금지(응답유실 후 중복주문 방지). 타임아웃은 길게.
        return await self._post(
            "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body,
            retries=1, timeout=ORDER_TIMEOUT,
        )

    async def inquire_psbl_order(self, ticker: str, price: int = 0, ord_dvsn: str = "01") -> dict[str, Any]:
        """매수가능조회. 응답 output.nrcvb_buy_qty(미수없는 매수가능수량)/max_buy_qty."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt,
            "PDNO": ticker,
            "ORD_UNPR": str(price),
            "ORD_DVSN": ord_dvsn,
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        return await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order", _PSBL_TR[self._env], params
        )

    async def inquire_balance(self) -> dict[str, Any]:
        """주식잔고조회. output1=종목별 보유(hldg_qty), output2=예수금(dnca_tot_amt)."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt,
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
        return await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance", _BALANCE_TR[self._env], params
        )

    async def inquire_daily_ccld(
        self, *, ticker: str = "", odno: str = "", today: str = "",
        sll_buy: str = "00", ccld_dvsn: str = "00",
    ) -> dict[str, Any]:
        """주식일별주문체결조회(당일). odno/ticker로 필터해 체결결과 확인.

        sll_buy: 00 전체 / 01 매도 / 02 매수.  ccld_dvsn: 00 전체 / 01 체결 / 02 미체결.
        today: 'YYYYMMDD'(미지정 시 오늘). output1=주문/체결 행 리스트.
        """
        d = today or datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt,
            "INQR_STRT_DT": d,
            "INQR_END_DT": d,
            "SLL_BUY_DVSN_CD": sll_buy,
            "PDNO": ticker,
            "CCLD_DVSN": ccld_dvsn,
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": odno,
            "INQR_DVSN_1": "",
            "EXCG_ID_DVSN_CD": "KRX",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", _CCLD_TR[self._env], params
        )

    async def confirm_fill(
        self, ticker: str, odno: str, *, today: str = "",
        attempts: int = 3, wait: tuple[float, float] = (1.0, 2.0),
    ) -> dict[str, Any] | None:
        """주문 직후 실제 체결수량·평균가 확인. 체결 반영 지연 대비 짧게 재조회.

        반환: parse_ccld_fill 결과({filled_qty>0}면 즉시 반환). 끝까지 미체결이면
        마지막 결과(filled_qty=0 또는 None)를 반환 → 호출측이 미체결/확인불가 판정.
        """
        last: dict[str, Any] | None = None
        for i in range(attempts):
            if i > 0:
                await asyncio.sleep(random.uniform(*wait))
            data = await self.inquire_daily_ccld(ticker=ticker, odno=odno, today=today)
            last = parse_ccld_fill(data, odno)
            if last and last["filled_qty"] > 0:
                return last
        return last
