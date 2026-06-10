"""한국장 프리/장초 리포트 — 종가베팅 후보 영속화·로드 (#404) + 프리장 NXT 시세·전일지수 (#469)."""
from __future__ import annotations

import asyncio

from datetime import datetime

from src.market_report.kr_morning import last_session_pct
from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.telegram_notify import _format_kr_morning_summary
from src.market_report.top3_status import (
    compute_status, fetch_prev_top3_status, find_prev_candidates,
)
from src.trading.top3_bridge import persist_candidates


def test_premarket_summary_new_sections_and_no_kr_index() -> None:
    """프리장(08:05) 리포트 재구성(사용자 2026-06-10): 프리장 테마·NXT 상승/하락 Top5 노출,
    한국지수(코스피/코스닥)·AI요약 미표시. 미국 야간(EWY 등)은 헤더 유지."""
    s = MarketSnapshot(mode="kr_premarket", generated_at=datetime(2026, 6, 10, 8, 5))
    s.kospi = IndexQuote(market="KOSPI", value=7800.0, change=0, change_pct=0.0,
                         volume=0, trade_value=0.0, timestamp=datetime.now())
    s.summary = "이건 표시되면 안 됨(프리장 AI요약 제거)"
    s.premarket_themes = [{"name": "반도체 재료/부품", "count": 3, "avg_pct": 4.2,
                           "stocks": ["후성", "타이거일렉"]}]
    s.overtime_gainers = [{"ticker": "093370", "name": "후성", "overtime_pct": 2.07}]
    s.overtime_losers = [{"ticker": "005930", "name": "삼성전자", "overtime_pct": -0.78}]
    s.us_overnight = {"futures": [{"name": "나스닥 선물", "change_pct": 0.5}], "m7": [],
                      "etf": [{"symbol": "EWY", "name": "한국 ETF(EWY)", "price": 70.0,
                               "change_pct": 1.2, "session_pct": 0.3, "session_label": "애프터"}]}
    txt = _format_kr_morning_summary(s)
    assert "프리장 소속 테마" in txt and "반도체 재료/부품" in txt
    assert "상승 Top5" in txt and "하락 Top5" in txt
    assert "EWY" in txt
    assert "코스피" not in txt and "코스닥" not in txt   # 한국지수 미표시
    assert "프리장 AI요약 제거" not in txt              # AI요약 미표시


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


# ─── #484: quote 500 장애 → 일봉 폴백 ────────────────────────────────────────


class _QuoteFailAdapter:
    """get_quote는 항상 실패(KIS inquire-price 500 재현), get_ohlcv는 정상."""

    def __init__(self, candles_by_ticker: dict) -> None:
        self.table = candles_by_ticker

    async def get_quote(self, ticker: str):
        raise RuntimeError("inquire-price 500")

    async def get_ohlcv(self, ticker: str, days: int = 5):
        from src.datasource.base import Candle
        rows = self.table.get(ticker, [])
        return [Candle(date=d, open=c, high=c, low=c, close=c, volume=0) for d, c in rows]


def test_fetch_status_falls_back_to_ohlcv_on_quote_500() -> None:
    picks = [{"ticker": "009150", "name": "삼성전기", "price": 1790000}]
    # 일봉: 전일 1700000 → 오늘(현재가) 1586000 = -6.7%
    adapter = _QuoteFailAdapter({"009150": [("20260605", 1700000.0), ("20260608", 1586000.0)]})
    out = asyncio.run(fetch_prev_top3_status(picks, adapter, use_nxt=False))
    assert len(out) == 1
    assert out[0]["cur_price"] == 1586000.0
    assert out[0]["today_pct"] == round((1586000 / 1700000 - 1) * 100, 2)
    assert out[0]["return_pct"] == round((1586000 - 1790000) / 1790000 * 100, 2)


def test_fetch_status_skips_when_both_fail() -> None:
    picks = [{"ticker": "009150", "name": "삼성전기", "price": 1790000}]
    adapter = _QuoteFailAdapter({})  # ohlcv도 빈 → 폴백 실패 → 스킵
    out = asyncio.run(fetch_prev_top3_status(picks, adapter, use_nxt=False))
    assert out == []


def test_compute_status_today_pct_none_passthrough() -> None:
    st = compute_status({"ticker": "1", "name": "x", "price": 100}, 98.0, None)
    assert st["today_pct"] is None
    assert st["return_pct"] == -2.0
