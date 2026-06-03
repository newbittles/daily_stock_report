# 모의 주문 배관 검증(스모크) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KIS 모의투자(VTS)에서 hashkey→매수가능조회→1주 매수→잔고확인→매도가 동작함을 재사용 가능한 주문 프리미티브 + 스모크 스크립트로 검증한다.

**Architecture:** 읽기전용 `src/datasource`(시세)와 분리된 신규 `src/trading/` 주문 쓰기 레이어. `KisOrderClient`가 기존 `KisTokenManager`(토큰 캐시 공유)를 재사용하고, 주문은 hashkey 포함 POST, 조회는 GET으로 보내며 `adapter._request`와 동일한 재시도·HARD STOP·`rt_cd` 검증 패턴을 따른다. `KisAdapter`는 수정하지 않는다.

**Tech Stack:** Python 3.11+ (async), httpx, pytest + respx + pytest-asyncio(auto mode), KIS REST.

**검증 출처(2026-06-03):** koreainvestment/open-trading-api `examples_llm/domestic_stock/{order_cash,inquire_psbl_order}` — 모의 주문 TR이 구 `VTTC0802U/0801U`에서 **`VTTC0012U`(매수)/`VTTC0011U`(매도)**로 변경됨(2025 NXT 대체거래소 스키마 개편, body에 `EXCG_ID_DVSN_CD` 추가). hashkey는 현재 KIS상 선택사항이나 본 플랜은 구현한다.

---

## File Structure

| 파일 | 책임 |
|------|------|
| `src/trading/__init__.py` (생성) | 패키지 마커 |
| `src/trading/kis_order.py` (생성) | `KisOrderClient` — hashkey·매수가능조회·현금주문·잔고. 주문 프리미티브 단일 책임 |
| `tests/test_kis_order.py` (생성) | L2 단위(respx 모킹): TR_ID 선택·계좌분리·body 조립·hashkey·rt_cd/429 |
| `scripts/smoke_paper_order.py` (생성) | 수동 CLI 스모크(dry-run 기본, `--send` 게이트, env/수량 가드) |

후속 마일스톤(②broker)이 `kis_order.py`를 그대로 확장한다.

---

## Task 1: 패키지 스캐폴드 + 계좌분리 + TR 매핑 + 예외

**Files:**
- Create: `src/trading/__init__.py`
- Create: `src/trading/kis_order.py`
- Test: `tests/test_kis_order.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_kis_order.py
"""KIS 주문 프리미티브 테스트 — respx로 HTTP 모킹 (라이브 호출 0)."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.datasource.kis.token import BASE_URL
from src.trading.kis_order import KisOrderClient, KisOrderError, KisOrderHardStop, _split_account, _ORDER_TR

PAPER = BASE_URL["paper"]


def test_split_account():
    assert _split_account("50123456-01") == ("50123456", "01")
    assert _split_account("5012345601") == ("50123456", "01")


def test_order_tr_mapping():
    assert _ORDER_TR[("paper", "buy")] == "VTTC0012U"
    assert _ORDER_TR[("paper", "sell")] == "VTTC0011U"
    assert _ORDER_TR[("real", "buy")] == "TTTC0012U"
    assert _ORDER_TR[("real", "sell")] == "TTTC0011U"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_kis_order.py::test_split_account tests/test_kis_order.py::test_order_tr_mapping -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.trading.kis_order'`

- [ ] **Step 3: 최소 구현**

```python
# src/trading/__init__.py
"""주문 쓰기 레이어 (datasource 시세 읽기와 분리)."""
```

```python
# src/trading/kis_order.py
"""KIS 현금주문 프리미티브 — hashkey·매수가능조회·현금주문·잔고.

읽기전용 datasource(시세)와 분리된 주문 쓰기 레이어. KisTokenManager 재사용.
TR_ID는 KIS 공식 examples_llm(2026-06 검증) 기준 — 2025 NXT 개편 반영.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Literal

import httpx

from src.datasource.kis.token import KisTokenManager

logger = logging.getLogger(__name__)

MAX_RETRY = 3
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


def _split_account(account_no: str) -> tuple[str, str]:
    """'50123456-01' 또는 '5012345601' → (CANO, ACNT_PRDT_CD)."""
    s = account_no.replace("-", "").strip()
    return s[:8], (s[8:10] if len(s) >= 10 else "01")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_kis_order.py::test_split_account tests/test_kis_order.py::test_order_tr_mapping -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/trading/__init__.py src/trading/kis_order.py tests/test_kis_order.py
git commit -m "feat(trading): KIS 주문 프리미티브 스캐폴드 — 계좌분리·TR 매핑"
```

---

## Task 2: `KisOrderClient.__init__` + `hashkey()`

**Files:**
- Modify: `src/trading/kis_order.py`
- Test: `tests/test_kis_order.py`

- [ ] **Step 1: 실패 테스트 작성** (append to `tests/test_kis_order.py`)

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    import src.datasource.kis.token as token_mod
    monkeypatch.setattr(token_mod, "TOKEN_CACHE", tmp_path / "kis_token.json")
    return KisOrderClient(
        app_key="TESTKEY", app_secret="TESTSECRET",
        account_no="50123456-01", env="paper",
    )


def _mock_token():
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 86400})
    )


@respx.mock
async def test_hashkey(client):
    _mock_token()
    route = respx.post(f"{PAPER}/uapi/hashkey").mock(
        return_value=httpx.Response(200, json={"HASH": "HASHED-123"})
    )
    h = await client.hashkey({"CANO": "50123456"})
    assert h == "HASHED-123"
    assert route.called
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_kis_order.py::test_hashkey -v`
Expected: FAIL — `AttributeError: 'KisOrderClient' ... has no attribute 'hashkey'` (또는 클래스 미정의)

- [ ] **Step 3: 최소 구현** (append to `src/trading/kis_order.py`)

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_kis_order.py::test_hashkey -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/trading/kis_order.py tests/test_kis_order.py
git commit -m "feat(trading): KisOrderClient + hashkey() 발급"
```

---

## Task 3: `_post()` — 재시도·HARD STOP·rt_cd 검증

**Files:**
- Modify: `src/trading/kis_order.py`
- Test: `tests/test_kis_order.py`

- [ ] **Step 1: 실패 테스트 작성** (append)

```python
@respx.mock
async def test_post_rtcd_error(client):
    _mock_token()
    respx.post(f"{PAPER}/uapi/hashkey").mock(return_value=httpx.Response(200, json={"HASH": "H"}))
    respx.post(f"{PAPER}/test-order").mock(
        return_value=httpx.Response(200, json={"rt_cd": "1", "msg1": "주문가능금액부족"})
    )
    with pytest.raises(KisOrderError, match="주문가능금액부족"):
        await client._post("/test-order", "VTTC0012U", {"CANO": "50123456"})


@respx.mock
async def test_post_hard_stop_429(client):
    _mock_token()
    respx.post(f"{PAPER}/uapi/hashkey").mock(return_value=httpx.Response(200, json={"HASH": "H"}))
    respx.post(f"{PAPER}/test-order").mock(return_value=httpx.Response(429, text="too many"))
    with pytest.raises(KisOrderHardStop):
        await client._post("/test-order", "VTTC0012U", {"CANO": "50123456"})
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_kis_order.py::test_post_rtcd_error tests/test_kis_order.py::test_post_hard_stop_429 -v`
Expected: FAIL — `'KisOrderClient' object has no attribute '_post'`

- [ ] **Step 3: 최소 구현** (append to class `KisOrderClient`)

```python
    async def _post(self, path: str, tr_id: str, body: dict[str, Any], *, use_hash: bool = True) -> dict[str, Any]:
        """주문 POST + (선택)hashkey + 재시도·백오프·HARD STOP·rt_cd 검증."""
        await self._token_mgr.get_token()
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRY):
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
                async with httpx.AsyncClient(timeout=10.0) as http:
                    resp = await http.post(url, headers=headers, json=body)
                if resp.status_code in (429, 401, 403):
                    raise KisOrderHardStop(f"HTTP {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                rt_cd = data.get("rt_cd")
                if rt_cd is not None and rt_cd != "0":
                    raise KisOrderError(f"rt_cd={rt_cd} msg={data.get('msg1', '')}")
                return data
            except KisOrderHardStop:
                raise
            except KisOrderError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning("kis_order_post_error path=%s attempt=%d error=%s", path, attempt, exc)
        raise KisOrderError(f"{path} failed after {MAX_RETRY} attempts") from last_exc
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_kis_order.py::test_post_rtcd_error tests/test_kis_order.py::test_post_hard_stop_429 -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/trading/kis_order.py tests/test_kis_order.py
git commit -m "feat(trading): _post — hashkey·재시도·HARD STOP·rt_cd 검증"
```

---

## Task 4: `order_cash()` — 현금 매수/매도 주문

**Files:**
- Modify: `src/trading/kis_order.py`
- Test: `tests/test_kis_order.py`

- [ ] **Step 1: 실패 테스트 작성** (append)

```python
@respx.mock
async def test_order_cash_buy_body(client):
    _mock_token()
    respx.post(f"{PAPER}/uapi/hashkey").mock(return_value=httpx.Response(200, json={"HASH": "H"}))
    route = respx.post(f"{PAPER}/uapi/domestic-stock/v1/trading/order-cash").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "msg1": "정상", "output": {"ODNO": "0001"}})
    )
    res = await client.order_cash("buy", "005930", qty=1, price=0, ord_dvsn="01")
    assert res["output"]["ODNO"] == "0001"
    # 요청 검증: 매수 TR_ID + body
    req = route.calls.last.request
    assert req.headers["tr_id"] == "VTTC0012U"
    import json
    body = json.loads(req.content)
    assert body["PDNO"] == "005930"
    assert body["CANO"] == "50123456"
    assert body["ACNT_PRDT_CD"] == "01"
    assert body["ORD_QTY"] == "1"
    assert body["ORD_DVSN"] == "01"
    assert body["ORD_UNPR"] == "0"
    assert body["EXCG_ID_DVSN_CD"] == "KRX"
    assert body["SLL_TYPE"] == ""


@respx.mock
async def test_order_cash_sell_tr(client):
    _mock_token()
    respx.post(f"{PAPER}/uapi/hashkey").mock(return_value=httpx.Response(200, json={"HASH": "H"}))
    route = respx.post(f"{PAPER}/uapi/domestic-stock/v1/trading/order-cash").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "msg1": "정상", "output": {"ODNO": "0002"}})
    )
    await client.order_cash("sell", "005930", qty=1)
    req = route.calls.last.request
    assert req.headers["tr_id"] == "VTTC0011U"
    import json
    assert json.loads(req.content)["SLL_TYPE"] == "01"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_kis_order.py::test_order_cash_buy_body tests/test_kis_order.py::test_order_cash_sell_tr -v`
Expected: FAIL — `'KisOrderClient' object has no attribute 'order_cash'`

- [ ] **Step 3: 최소 구현** (append to class)

```python
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
        return await self._post("/uapi/domestic-stock/v1/trading/order-cash", tr_id, body)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_kis_order.py::test_order_cash_buy_body tests/test_kis_order.py::test_order_cash_sell_tr -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/trading/kis_order.py tests/test_kis_order.py
git commit -m "feat(trading): order_cash — 현금 매수/매도(VTTC0012U/0011U + EXCG_ID_DVSN_CD)"
```

---

## Task 5: `_get()` + `inquire_psbl_order()` + `inquire_balance()`

**Files:**
- Modify: `src/trading/kis_order.py`
- Test: `tests/test_kis_order.py`

- [ ] **Step 1: 실패 테스트 작성** (append)

```python
@respx.mock
async def test_inquire_psbl_order(client):
    _mock_token()
    route = respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-psbl-order").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0", "msg1": "정상",
            "output": {"nrcvb_buy_qty": "12", "nrcvb_buy_amt": "990000", "max_buy_qty": "20"},
        })
    )
    res = await client.inquire_psbl_order("005930", price=82500)
    assert res["output"]["nrcvb_buy_qty"] == "12"
    assert route.calls.last.request.headers["tr_id"] == "VTTC8908R"


@respx.mock
async def test_inquire_balance(client):
    _mock_token()
    route = respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-balance").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0", "msg1": "정상",
            "output1": [{"pdno": "005930", "hldg_qty": "1", "prpr": "82500"}],
            "output2": [{"dnca_tot_amt": "9917500"}],
        })
    )
    res = await client.inquire_balance()
    assert res["output1"][0]["hldg_qty"] == "1"
    assert route.calls.last.request.headers["tr_id"] == "VTTC8434R"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_kis_order.py::test_inquire_psbl_order tests/test_kis_order.py::test_inquire_balance -v`
Expected: FAIL — `'KisOrderClient' object has no attribute 'inquire_psbl_order'`

- [ ] **Step 3: 최소 구현** (append to class)

```python
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
        return await self._get("/uapi/domestic-stock/v1/trading/inquire-psbl-order", _PSBL_TR[self._env], params)

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
        return await self._get("/uapi/domestic-stock/v1/trading/inquire-balance", _BALANCE_TR[self._env], params)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_kis_order.py -v`
Expected: PASS (전체 통과 — Task1~5 누적)

- [ ] **Step 5: 커밋**

```bash
git add src/trading/kis_order.py tests/test_kis_order.py
git commit -m "feat(trading): 매수가능조회·잔고조회 + GET 헬퍼"
```

---

## Task 6: 스모크 스크립트 (dry-run 기본 + `--send` 게이트)

**Files:**
- Create: `scripts/smoke_paper_order.py`

> 안전 가드는 자동 테스트 대신 코드 내 명시적 검사로 구현(실거래 게이트). 스크립트 실행 자체가 검증.

- [ ] **Step 1: 스크립트 작성**

```python
# scripts/smoke_paper_order.py
"""KIS 모의(VTS) 주문 배관 스모크 — hashkey→매수가능→매수→잔고→매도.

dry-run 기본(미전송 프리뷰). 실제 모의주문 전송은 --send 명시 시에만.
실행: python scripts/smoke_paper_order.py --ticker 005930 --qty 1            # dry-run
      python scripts/smoke_paper_order.py --ticker 005930 --qty 1 --send     # 실제 모의주문
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings  # noqa: E402
from src.trading.kis_order import KisOrderClient  # noqa: E402


async def run(ticker: str, qty: int, price: int, send: bool) -> int:
    s = get_settings()
    # ── 안전 가드 ──
    if s.kis_env != "paper":
        print(f"[중단] KIS_ENV={s.kis_env} — 본 스모크는 모의(paper) 전용입니다. 실전 거부.")
        return 1
    if qty > 10:
        print(f"[중단] qty={qty} — 모의여도 10주 초과 금지(fat-finger 방지).")
        return 1

    client = KisOrderClient(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="paper")
    ord_dvsn = "00" if price > 0 else "01"   # 가격 지정 시 지정가, 아니면 시장가

    print(f"=== 모의 주문 스모크 · {ticker} {qty}주 · {'지정가 '+str(price) if price else '시장가'} ===")

    # 1) hashkey
    sample = {"CANO": client._cano, "ACNT_PRDT_CD": client._acnt, "PDNO": ticker, "ORD_QTY": str(qty)}
    h = await client.hashkey(sample)
    print(f"[1] hashkey OK · HASH={h[:10]}…")

    # 2) 매수가능조회
    psbl = await client.inquire_psbl_order(ticker, price=price, ord_dvsn=ord_dvsn)
    out = psbl.get("output", {})
    print(f"[2] 매수가능 수량={out.get('nrcvb_buy_qty')} 금액={out.get('nrcvb_buy_amt')}")

    if not send:
        print("[dry-run] 여기까지 배관 확인 완료. 실제 주문은 --send 로 실행.")
        return 0

    # 3) 매수
    print(f"[3] 매수 주문 전송: {ticker} {qty}주 ({'지정가 '+str(price) if price else '시장가'})")
    buy = await client.order_cash("buy", ticker, qty, price=price, ord_dvsn=ord_dvsn)
    odno = buy.get("output", {}).get("ODNO")
    print(f"    → 접수 odno={odno} msg={buy.get('msg1')}")
    await asyncio.sleep(random.uniform(1.5, 3.0))

    # 4) 잔고 확인
    bal = await client.inquire_balance()
    held = [r for r in bal.get("output1", []) if r.get("pdno") == ticker and int(r.get("hldg_qty", "0")) > 0]
    print(f"[4] 보유 확인: {held if held else '(아직 미체결 또는 0)'}")

    # 5) 매도(청산)
    print(f"[5] 매도 주문 전송(청산): {ticker} {qty}주 시장가")
    sell = await client.order_cash("sell", ticker, qty, price=0, ord_dvsn="01")
    print(f"    → 접수 odno={sell.get('output', {}).get('ODNO')} msg={sell.get('msg1')}")
    await asyncio.sleep(random.uniform(1.5, 3.0))

    bal2 = await client.inquire_balance()
    cash = (bal2.get("output2") or [{}])[0].get("dnca_tot_amt")
    print(f"[6] 최종 예수금={cash}")
    print("=== 스모크 완료 ===")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="KIS 모의 주문 배관 스모크")
    ap.add_argument("--ticker", default="005930", help="종목코드 6자리(기본 삼성전자)")
    ap.add_argument("--qty", type=int, default=1, help="수량(기본 1, 최대 10)")
    ap.add_argument("--price", type=int, default=0, help="지정가(0이면 시장가)")
    ap.add_argument("--send", action="store_true", help="실제 모의주문 전송(미지정 시 dry-run)")
    a = ap.parse_args()
    if not (a.ticker.isdigit() and len(a.ticker) == 6):
        print("[중단] 종목코드는 6자리 숫자여야 합니다.")
        raise SystemExit(1)
    raise SystemExit(asyncio.run(run(a.ticker, a.qty, a.price, a.send)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 설정 필드명 확인**

Run: `python -c "from src.config.settings import get_settings; s=get_settings(); print(s.kis_env, bool(s.kis_app_key), bool(s.kis_account_no))"`
Expected: `paper True True` — 필드명(`kis_env`/`kis_app_key`/`kis_app_secret`/`kis_account_no`)이 다르면 `src/config/settings.py`에 맞춰 스크립트의 접근자를 수정.

> 만약 settings 필드명이 다르면(예: `KIS_APP_KEY` 환경변수만 있고 pydantic 필드명이 다른 경우), `src/config/settings.py`를 Read로 확인해 실제 속성명으로 교체한 뒤 진행.

- [ ] **Step 3: dry-run 실행(미전송) — 배관 검증**

Run: `python scripts/smoke_paper_order.py --ticker 005930 --qty 1`
Expected: `[1] hashkey OK …` → `[2] 매수가능 …` → `[dry-run] …` 출력, exit 0. (네트워크/모의장 상태에 따라 매수가능 수치는 달라짐)

- [ ] **Step 4: 커밋**

```bash
git add scripts/smoke_paper_order.py
git commit -m "feat(trading): 모의 주문 스모크 스크립트(dry-run 기본·--send 게이트)"
```

---

## Task 7: 실제 VTS 전송 스모크 (수동, CI 제외)

**Files:** 없음 (실행·관찰만)

> ⚠️ 실제 모의주문이 KIS VTS 서버로 전송됩니다. **장중(09:00~15:30 KST)**에 실행해야 체결 확인 가능. 모의장 운영시간은 KIS 공지 기준.

- [ ] **Step 1: 장중 `--send` 실행**

Run: `python scripts/smoke_paper_order.py --ticker 005930 --qty 1 --send`
Expected: `[3] 매수 … odno=… msg=정상` → `[4] 보유 … hldg_qty=1` → `[5] 매도 … 정상` → `[6] 최종 예수금=…`

- [ ] **Step 2: 결과 판정 (DoD)**

- 매수·매도 모두 `rt_cd=0`(msg=정상)이고 `odno` 수신 → **주문 배관 검증 성공**.
- 만약 매수에서 `rt_cd!=0` + TR_ID 관련 거부 → `_ORDER_TR` 값을 KIS 응답 msg와 대조해 재확인(드물지만 추가 개편 가능성). 마일스톤 ②로 넘어가기 전 반드시 해소.

- [ ] **Step 3: 검증 결과 기록**

`docs/superpowers/specs/2026-06-03-paper-order-smoke-design.md` 하단에 실행 일시·odno·결과 한 줄 추가 후 커밋:
```bash
git add docs/superpowers/specs/2026-06-03-paper-order-smoke-design.md
git commit -m "docs: 모의 주문 스모크 실제 VTS 검증 결과 기록"
```

---

## Self-Review (작성자 점검 완료)

- **Spec 커버리지:** §2 경계(Task1·2 분리 패키지) / §3 컴포넌트(hashkey T2, order_cash T4, psbl·balance T5, _post/_get T3·T5) / §4 흐름(T6·T7 스크립트) / §5 안전(T6 env·수량·dry-run 가드) / §6 테스트(T1~5 respx) / §7 사전검증(완료 — 본 플랜 헤더에 TR_ID 정정 반영). 모든 섹션 대응 태스크 존재.
- **플레이스홀더:** 없음. 모든 코드·명령·기대출력 명시.
- **타입/명명 일관성:** `KisOrderClient`·`_ORDER_TR`·`_split_account`·`order_cash(side,ticker,qty,price,ord_dvsn,excg)`·`inquire_psbl_order`·`inquire_balance`가 전 태스크에서 일관.
- **스펙 정정 반영:** 스펙 §7의 추정 TR_ID(VTTC0802U/0801U)는 공식 검증으로 VTTC0012U/0011U로 정정됨 — 스펙 문서도 동기화 필요(아래 핸드오프에서 처리).
