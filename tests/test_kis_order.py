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
    _CCLD_TR,
    _ORDER_TR,
    _split_account,
    parse_ccld_fill,
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


def test_ccld_tr_mapping():
    # NXT 개편 반영 — 옛 8001R 아님(주문 0012U처럼 0081R로 재번호, examples_llm 2026-06)
    assert _CCLD_TR["paper"] == "VTTC0081R"
    assert _CCLD_TR["real"] == "TTTC0081R"


def test_parse_ccld_fill_basic():
    data = {"output1": [
        {"odno": "0000000001", "pdno": "005930", "ord_qty": "12",
         "tot_ccld_qty": "5", "avg_prvs": "83000", "rmn_qty": "7"},
    ]}
    fill = parse_ccld_fill(data, "0001")   # 접수 ODNO는 zero-pad 안 됨 → 정규화 비교
    assert fill == {"filled_qty": 5, "avg_price": 83000.0, "ord_qty": 12, "rmn_qty": 7}


def test_parse_ccld_fill_partial_rows_weighted_avg():
    # 같은 주문이 분할체결 2행 → 수량가중 평균가
    data = {"output1": [
        {"odno": "10", "tot_ccld_qty": "2", "avg_prvs": "100", "ord_qty": "10", "rmn_qty": "8"},
        {"odno": "10", "tot_ccld_qty": "3", "avg_prvs": "110", "ord_qty": "10", "rmn_qty": "5"},
        {"odno": "99", "tot_ccld_qty": "9", "avg_prvs": "999", "ord_qty": "9", "rmn_qty": "0"},
    ]}
    fill = parse_ccld_fill(data, "10")
    assert fill["filled_qty"] == 5
    assert fill["avg_price"] == (2 * 100 + 3 * 110) / 5   # 106.0
    assert fill["rmn_qty"] == 5


def test_parse_ccld_fill_not_found_is_none():
    assert parse_ccld_fill({"output1": []}, "0001") is None
    assert parse_ccld_fill({"output1": [{"odno": "7", "tot_ccld_qty": "1"}]}, "0001") is None


def test_parse_ccld_fill_unfilled_zero():
    data = {"output1": [{"odno": "1", "tot_ccld_qty": "0", "avg_prvs": "0",
                         "ord_qty": "12", "rmn_qty": "12"}]}
    fill = parse_ccld_fill(data, "1")
    assert fill["filled_qty"] == 0 and fill["avg_price"] == 0.0


@respx.mock
async def test_inquire_daily_ccld(client):
    _mock_token()
    route = respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-daily-ccld").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0", "msg1": "정상",
            "output1": [{"odno": "0000000001", "pdno": "005930",
                         "tot_ccld_qty": "5", "avg_prvs": "83000", "rmn_qty": "7"}],
        })
    )
    res = await client.inquire_daily_ccld(ticker="005930", odno="0001", today="20260616")
    assert res["output1"][0]["tot_ccld_qty"] == "5"
    req = route.calls.last.request
    assert req.headers["tr_id"] == "VTTC0081R"
    assert dict(req.url.params)["INQR_STRT_DT"] == "20260616"
    assert dict(req.url.params)["ODNO"] == "0001"
    assert dict(req.url.params)["PDNO"] == "005930"


@respx.mock
async def test_confirm_fill_returns_parsed(client):
    _mock_token()
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-daily-ccld").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0",
            "output1": [{"odno": "1", "tot_ccld_qty": "5", "avg_prvs": "83000",
                         "ord_qty": "12", "rmn_qty": "7"}],
        })
    )
    fill = await client.confirm_fill("005930", "1", today="20260616")
    assert fill["filled_qty"] == 5 and fill["avg_price"] == 83000.0


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
async def test_order_cash_no_retry_idempotent(client):
    """실전 안전: 주문 POST는 재시도 금지(중복주문 방지). 500이어도 1회만 시도 후 실패."""
    _mock_token()
    respx.post(f"{PAPER}/uapi/hashkey").mock(return_value=httpx.Response(200, json={"HASH": "H"}))
    route = respx.post(f"{PAPER}/uapi/domestic-stock/v1/trading/order-cash").mock(
        return_value=httpx.Response(500, text="server error")
    )
    with pytest.raises(KisOrderError):
        await client.order_cash("buy", "005930", qty=1)
    assert route.call_count == 1   # 재시도 없이 단 1회 (멱등 보장)


@respx.mock
async def test_inquire_psbl_order_retries(client):
    """조회는 기존대로 재시도(주문前 단계라 멱등). 일시 500 → 재시도 후 성공."""
    _mock_token()
    route = respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-psbl-order").mock(
        side_effect=[
            httpx.Response(500, text="err"),
            httpx.Response(200, json={"rt_cd": "0", "output": {"nrcvb_buy_qty": "12"}}),
        ]
    )
    res = await client.inquire_psbl_order("005930", price=82500)
    assert res["output"]["nrcvb_buy_qty"] == "12"
    assert route.call_count == 2   # 1회 실패 후 재시도 성공


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
