"""KIS 토큰 발급 403(분당 제한) 처리 — 60초 1회 백오프 후 KisTokenError로 Hard Stop (사용자 2026-06-17).

자동매매가 종목마다 토큰을 재발급 연타해 분당제한 403에 종일 막히던 회귀 방지.
백오프(60초)는 monkeypatch로 즉시 처리해 테스트가 지연되지 않게 한다.
"""
from __future__ import annotations

import httpx
import pytest
import respx

import src.datasource.kis.token as token_mod
from src.datasource.kis.token import BASE_URL, KisTokenError, KisTokenManager

PAPER = BASE_URL["paper"]


@pytest.fixture
def _cache(tmp_path, monkeypatch):
    monkeypatch.setattr(token_mod, "TOKEN_CACHE", tmp_path / "kis_token.json")


@pytest.fixture
def _no_sleep(monkeypatch):
    async def _noslp(*_a, **_k):
        return None
    monkeypatch.setattr(token_mod.asyncio, "sleep", _noslp)  # 60초 백오프 즉시화


@respx.mock
async def test_403_twice_raises_token_error(_cache, _no_sleep):
    route = respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(403, text="forbidden"))
    mgr = KisTokenManager("K", "S", "paper")
    with pytest.raises(KisTokenError):
        await mgr.get_token()
    assert route.call_count == 2  # 최초 1회 + 60초 후 1회만 (연타 없음)


@respx.mock
async def test_403_then_200_recovers(_cache, _no_sleep):
    route = respx.post(f"{PAPER}/oauth2/tokenP").mock(side_effect=[
        httpx.Response(403),
        httpx.Response(200, json={"access_token": "tok-ok", "expires_in": 86400}),
    ])
    mgr = KisTokenManager("K", "S", "paper")
    assert await mgr.get_token() == "tok-ok"
    assert route.call_count == 2


@respx.mock
async def test_200_issues_normally(_cache):
    respx.post(f"{PAPER}/oauth2/tokenP").mock(
        return_value=httpx.Response(200, json={"access_token": "t2", "expires_in": 86400}))
    mgr = KisTokenManager("K", "S", "paper")
    assert await mgr.get_token() == "t2"
