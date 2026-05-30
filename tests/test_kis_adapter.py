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
