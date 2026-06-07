"""한국장 프리/장초 리포트 — 종가베팅 후보 영속화·로드 (#404) + 프리장 NXT 시세·전일지수 (#469)."""
from __future__ import annotations

import asyncio

from src.market_report.kr_morning import last_session_pct
from src.market_report.top3_status import (
    compute_status, fetch_prev_top3_status, find_prev_candidates,
)
from src.trading.top3_bridge import persist_candidates


def test_candidate_persist_and_find_prev(tmp_path) -> None:
    picks = [
        {"ticker": "055550", "name": "신한지주", "theme": "금융", "rationale": "외인매수", "risk": "금리"},
        {"ticker": "005930", "name": "삼성전자", "rationale": "반등"},
    ]
    persist_candidates(picks, "2026-06-05", base_dir=tmp_path)
    # 이전 거래일분 로드 (today=06-08 → 06-05 선택)
    res = find_prev_candidates("2026-06-08", base_dir=tmp_path)
    assert res is not None
    date, loaded = res
    assert date == "2026-06-05"
    assert loaded[0]["ticker"] == "055550"
    assert loaded[0]["name"] == "신한지주"


def test_find_prev_candidates_excludes_today_and_future(tmp_path) -> None:
    persist_candidates([{"ticker": "000660", "name": "SK하이닉스"}], "2026-06-08", base_dir=tmp_path)
    # today 이전만 → 06-08 자신은 제외 → None
    assert find_prev_candidates("2026-06-08", base_dir=tmp_path) is None


def test_find_prev_candidates_none_when_empty(tmp_path) -> None:
    assert find_prev_candidates("2026-06-08", base_dir=tmp_path) is None


# ─── #469: 프리장 지수 — 전일 등락률 계산 (순수) ─────────────────────────────


def test_last_session_pct_uses_two_latest_before_today() -> None:
    closes = [("2026-06-02", 8801.49), ("2026-06-04", 8639.41), ("2026-06-05", 8160.59)]
    r = last_session_pct(closes, "2026-06-08")
    assert r is not None
    value, pct = r
    assert value == 8160.59
    assert round(pct, 2) == -5.54  # 8639.41 → 8160.59


def test_last_session_pct_excludes_today_row() -> None:
    # 강제 재실행 등으로 당일 봉이 섞여도 today 이전만 사용
    closes = [("2026-06-04", 100.0), ("2026-06-05", 110.0), ("2026-06-08", 120.0)]
    r = last_session_pct(closes, "2026-06-08")
    assert r is not None
    assert r[0] == 110.0
    assert round(r[1], 2) == 10.0


def test_last_session_pct_none_when_insufficient() -> None:
    assert last_session_pct([("2026-06-05", 8160.59)], "2026-06-08") is None
    assert last_session_pct([], "2026-06-08") is None


# ─── #469: 프리장 Top3 — NXT 시세 + 미체결 폴백 ──────────────────────────────


class _Q:
    def __init__(self, price: float, change_pct: float) -> None:
        self.price = price
        self.change_pct = change_pct


class _FakeAdapter:
    """get_nxt_quote/get_quote 스텁 — {ticker: (nxt_price, nxt_pct, krx_price)}."""

    def __init__(self, table: dict) -> None:
        self.table = table

    async def get_nxt_quote(self, ticker: str) -> _Q:
        nxt_price, nxt_pct, _ = self.table[ticker]
        return _Q(nxt_price, nxt_pct)

    async def get_quote(self, ticker: str) -> _Q:
        *_, krx = self.table[ticker]
        return _Q(krx, 0.0)  # 프리장 KRX: 현재가=전일종가, prdy_ctrt=0 (실측)


def test_fetch_status_premarket_uses_nxt() -> None:
    picks = [{"ticker": "009150", "name": "삼성전기", "price": 1790000}]
    adapter = _FakeAdapter({"009150": (1586000.0, -9.73, 1757000.0)})
    out = asyncio.run(fetch_prev_top3_status(picks, adapter, use_nxt=True))
    assert out[0]["today_pct"] == -9.73          # NXT 등락 그대로
    assert out[0]["cur_price"] == 1586000.0      # 추천가대비도 NXT가 기준
    assert out[0]["return_pct"] == round((1586000 - 1790000) / 1790000 * 100, 2)


def test_fetch_status_premarket_nxt_no_trade_fallback() -> None:
    # NXT 미체결(price=0) → KRX 전일종가로 추천가대비만, today_pct=None
    picks = [{"ticker": "055550", "name": "신한지주", "price": 50000}]
    adapter = _FakeAdapter({"055550": (0.0, 0.0, 49000.0)})
    out = asyncio.run(fetch_prev_top3_status(picks, adapter, use_nxt=True))
    assert out[0]["today_pct"] is None
    assert out[0]["cur_price"] == 49000.0
    assert out[0]["return_pct"] == -2.0


def test_fetch_status_regular_keeps_krx() -> None:
    picks = [{"ticker": "009150", "name": "삼성전기", "price": 1790000}]
    adapter = _FakeAdapter({"009150": (1586000.0, -9.73, 1757000.0)})
    out = asyncio.run(fetch_prev_top3_status(picks, adapter, use_nxt=False))
    assert out[0]["cur_price"] == 1757000.0  # 장중(09:15~)은 기존 KRX 경로 유지


def test_compute_status_today_pct_none_passthrough() -> None:
    st = compute_status({"ticker": "1", "name": "x", "price": 100}, 98.0, None)
    assert st["today_pct"] is None
    assert st["return_pct"] == -2.0
