"""KIS 주문 프리미티브 테스트 — respx로 HTTP 모킹 (라이브 호출 0)."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.datasource.kis.token import BASE_URL
from src.trading.kis_order import (
    KisOrderClient,
    KisOrderError,
    KisOrderHardStop,
    _ORDER_TR,
    _split_account,
)

PAPER = BASE_URL["paper"]


def test_split_account():
    assert _split_account("50123456-01") == ("50123456", "01")
    assert _split_account("5012345601") == ("50123456", "01")


def test_order_tr_mapping():
    assert _ORDER_TR[("paper", "buy")] == "VTTC0012U"
    assert _ORDER_TR[("paper", "sell")] == "VTTC0011U"
    assert _ORDER_TR[("real", "buy")] == "TTTC0012U"
    assert _ORDER_TR[("real", "sell")] == "TTTC0011U"


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


@respx.mock
async def test_order_cash_buy_body(client):
    _mock_token()
    respx.post(f"{PAPER}/uapi/hashkey").mock(return_value=httpx.Response(200, json={"HASH": "H"}))
    route = respx.post(f"{PAPER}/uapi/domestic-stock/v1/trading/order-cash").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "msg1": "정상", "output": {"ODNO": "0001"}})
    )
    res = await client.order_cash("buy", "005930", qty=1, price=0, ord_dvsn="01")
    assert res["output"]["ODNO"] == "0001"
    req = route.calls.last.request
    assert req.headers["tr_id"] == "VTTC0012U"
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
    assert json.loads(req.content)["SLL_TYPE"] == "01"


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
