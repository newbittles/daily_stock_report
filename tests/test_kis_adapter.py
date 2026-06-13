"""KIS REST 어댑터 테스트 — respx로 HTTP 모킹.

토큰 발급 + 현재가 + 일봉 + 순위 파싱 검증.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from src.datasource.kis.adapter import KisAdapter
from src.datasource.kis.token import BASE_URL


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    # 토큰 캐시를 임시 경로로 (실제 캐시 오염 방지)
    import src.datasource.kis.token as token_mod
    monkeypatch.setattr(token_mod, "TOKEN_CACHE", tmp_path / "kis_token.json")
    return KisAdapter(
        app_key="TESTKEY", app_secret="TESTSECRET",
        account_no="50123456-01", env="paper",
    )


PAPER = BASE_URL["paper"]


@respx.mock
async def test_token_issue_and_quote(adapter):
    # 토큰 발급 모킹
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={
            "access_token": "test-token-abc", "expires_in": 86400,
        })
    )
    # 현재가 모킹
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0", "msg1": "정상",
            "output": {
                "stck_prpr": "82500", "prdy_ctrt": "3.21",
                "acml_vol": "18432000", "bstp_kor_isnm": "반도체",
            },
        })
    )

    quote = await adapter.get_quote("005930")
    assert quote.ticker == "005930"
    assert quote.price == 82500.0
    assert quote.change_pct == 3.21
    assert quote.volume == 18432000


@respx.mock
async def test_ohlcv_parsing_and_order(adapter):
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
    )
    # KIS는 최신→과거 순으로 반환 → 어댑터가 뒤집어야 함
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0",
            "output2": [
                {"stck_bsop_date": "20260528", "stck_oprc": "300", "stck_hgpr": "310",
                 "stck_lwpr": "295", "stck_clpr": "305", "acml_vol": "1000"},
                {"stck_bsop_date": "20260527", "stck_oprc": "290", "stck_hgpr": "300",
                 "stck_lwpr": "288", "stck_clpr": "298", "acml_vol": "1200"},
            ],
        })
    )

    candles = await adapter.get_ohlcv("005930", days=10)
    assert len(candles) == 2
    # 과거→최신 순으로 정렬됐는지
    assert candles[0].date == "20260527"
    assert candles[1].date == "20260528"
    assert candles[1].close == 305.0


@respx.mock
async def test_ranking_fluctuation(adapter):
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
    )
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/ranking/fluctuation").mock(
        return_value=httpx.Response(200, json={
            "rt_cd": "0",
            "output": [
                {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                 "stck_prpr": "82500", "prdy_ctrt": "3.21", "acml_vol": "100"},
                {"stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
                 "stck_prpr": "211000", "prdy_ctrt": "5.78", "acml_vol": "200"},
            ],
        })
    )

    from src.datasource.base import RankingKind
    ranks = await adapter.get_ranking(RankingKind.CHANGE_PCT, top=10)
    assert len(ranks) == 2
    assert ranks[0].ticker == "005930"
    assert ranks[0].rank == 1
    assert ranks[1].ticker == "000660"
    assert ranks[1].change_pct == 5.78


@respx.mock
async def test_hard_stop_on_429(adapter):
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
    )
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        return_value=httpx.Response(429, text="Too Many Requests")
    )

    from src.datasource.kis.adapter import KisHardStop
    with pytest.raises(KisHardStop):
        await adapter.get_quote("005930")


def test_split_account(adapter):
    cano, prdt = adapter._split_account()
    assert cano == "50123456"
    assert prdt == "01"


# get_balance — KRX 종가 기준 (2종목: 후성 NXT거래·테크윙 NXT미거래)
_BALANCE_JSON = {
    "rt_cd": "0",
    "output1": [
        {"pdno": "093370", "prdt_name": "후성", "hldg_qty": "172",
         "pchs_avg_pric": "16240", "prpr": "19010",
         "evlu_pfls_amt": "476440", "evlu_pfls_rt": "17.05"},
        {"pdno": "089030", "prdt_name": "테크윙", "hldg_qty": "193",
         "pchs_avg_pric": "68800", "prpr": "65300",
         "evlu_pfls_amt": "-675500", "evlu_pfls_rt": "-5.08"},
    ],
    "output2": [{"evlu_pfls_smtl_amt": "-199060"}],
}


def _nxt_price_response(request):
    """inquire-price(NX) 종목별 응답 — 후성=18400(NXT거래), 테크윙=0(NXT미거래)."""
    iscd = request.url.params.get("FID_INPUT_ISCD")
    nxt = {"093370": "18400", "089030": "0"}.get(iscd, "0")
    return httpx.Response(200, json={
        "rt_cd": "0",
        "output": {"stck_prpr": nxt, "prdy_ctrt": "16.68",
                   "acml_vol": "1000", "bstp_kor_isnm": "화학"},
    })


@respx.mock
async def test_get_balance_krx_default(adapter):
    """기본 get_balance()는 KRX 정규장 종가(prpr) 유지 — NXT 미호출(stoploss 경로 보호)."""
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
    )
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-balance").mock(
        return_value=httpx.Response(200, json=_BALANCE_JSON)
    )
    nxt_route = respx.get(f"{PAPER}/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        side_effect=_nxt_price_response
    )

    rows = await adapter.get_balance()
    husung = next(r for r in rows if r["ticker"] == "093370")
    assert husung["current_price"] == 19010.0   # KRX 종가 유지
    assert husung["eval_profit"] == 476440.0
    assert husung["profit_rate"] == 17.05
    assert not nxt_route.called                  # NXT 시세 호출 안 함


@respx.mock
async def test_get_balance_prefer_nxt_matches_mts(adapter):
    """prefer_nxt=True는 NXT 거래종목 현재가를 NXT 종가로 덮고 평가손익 재계산(MTS 일치)."""
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
    )
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/trading/inquire-balance").mock(
        return_value=httpx.Response(200, json=_BALANCE_JSON)
    )
    respx.get(f"{PAPER}/uapi/domestic-stock/v1/quotations/inquire-price").mock(
        side_effect=_nxt_price_response
    )

    rows = await adapter.get_balance(prefer_nxt=True)
    husung = next(r for r in rows if r["ticker"] == "093370")
    techwing = next(r for r in rows if r["ticker"] == "089030")

    # 후성: NXT 종가 18,400으로 재평가 → MTS와 동일
    assert husung["current_price"] == 18400.0
    assert husung["eval_profit"] == (18400 - 16240) * 172   # 371520
    assert husung["profit_rate"] == 13.30
    # 테크윙: NXT 미거래(price=0) → KRX 종가 유지
    assert techwing["current_price"] == 65300.0
    assert techwing["eval_profit"] == -675500.0
