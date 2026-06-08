"""미국 야간(나스닥선물+M7, #476) + EWY(#479) 텔레그램 포맷."""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.telegram_notify import (
    _format_midday_summary, _format_us_morning_summary, _format_us_overnight,
)


def _idx(market: str, value: float, pct: float) -> IndexQuote:
    return IndexQuote(market=market, value=value, change=0.0, change_pct=pct,
                      volume=0, trade_value=0.0, timestamp=datetime.now())


def test_format_us_overnight_futures_and_m7() -> None:
    snap = MarketSnapshot(mode="midday", generated_at=datetime.now())
    snap.us_overnight = {
        "futures": [{"symbol": "NQ=F", "name": "나스닥 선물", "price": 29223.0, "change_pct": 0.68}],
        "m7": [{"symbol": "NVDA", "name": "엔비디아", "price": 205.1, "change_pct": -5.2},
               {"symbol": "AAPL", "name": "애플", "price": 307.3, "change_pct": -1.1}],
    }
    out = "\n".join(_format_us_overnight(snap))
    assert "미국 야간" in out
    assert "나스닥 선물 +0.68%" in out
    assert "엔비디아 -5.2%" in out and "애플 -1.1%" in out


def test_format_us_overnight_empty() -> None:
    snap = MarketSnapshot(mode="midday", generated_at=datetime.now())
    assert _format_us_overnight(snap) == []
    snap.us_overnight = {"futures": [], "m7": []}
    assert _format_us_overnight(snap) == []


def test_midday_shows_overnight_at_top() -> None:
    snap = MarketSnapshot(mode="midday", generated_at=datetime(2026, 6, 8, 11, 40))
    snap.kospi = _idx("KOSPI", 8160.5, -2.0)
    snap.us_overnight = {"futures": [{"symbol": "NQ=F", "name": "나스닥 선물",
                                      "price": 1.0, "change_pct": 0.65}], "m7": []}
    msg = _format_us_morning_idx = _format_midday_summary(snap)
    assert "미국 야간" in msg
    # 미국 야간이 코스피(지수)보다 위
    assert msg.index("미국 야간") < msg.index("코스피")


def test_us_morning_shows_ewy() -> None:
    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2},
                       {"name": "나스닥", "price": 17000.0, "change_pct": 1.5}]
    snap.ewy = {"name": "EWY(한국 MSCI ETF)", "price": 175.19, "change_pct": -14.11, "date": "2026-06-05"}
    msg = _format_us_morning_summary(snap)
    assert "EWY" in msg and "-14.11%" in msg


def test_us_morning_no_ewy_when_absent() -> None:
    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2},
                       {"name": "나스닥", "price": 17000.0, "change_pct": 1.5}]
    msg = _format_us_morning_summary(snap)
    assert "EWY" not in msg


# ─── #497: 프리장/장중 지수 = 선물 실시간 교체 ───────────────────────────────
async def test_apply_index_futures_overrides_nasdaq_sp(monkeypatch) -> None:
    from src.market_report.us_report_runner import _apply_index_futures

    async def fake_overnight():
        return {"futures": [{"symbol": "NQ=F", "name": "나스닥 선물", "price": 29000.0, "change_pct": 0.69},
                            {"symbol": "ES=F", "name": "S&P500 선물", "price": 7400.0, "change_pct": 0.13}],
                "m7": [], "etf": []}
    monkeypatch.setattr("src.datasource.us.overnight.fetch_us_overnight", fake_overnight)

    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime.now())
    snap.us_indices = [
        {"name": "S&P500", "price": 5300.0, "change_pct": -1.2},   # 전날 마감
        {"name": "나스닥", "price": 17000.0, "change_pct": -1.5},
        {"name": "다우", "price": 38000.0, "change_pct": -0.8},
    ]
    await _apply_index_futures(snap)
    sp, nq, dow = snap.us_indices
    assert sp["name"] == "S&P500 선물" and sp["change_pct"] == 0.13 and sp["is_futures"]
    assert nq["name"] == "나스닥 선물" and nq["change_pct"] == 0.69
    assert dow["name"] == "다우" and dow["change_pct"] == -0.8  # 선물 없는 지수는 전날 유지
    assert snap.us_overnight["futures"]  # 보관


async def test_apply_index_futures_keeps_prev_on_missing(monkeypatch) -> None:
    """선물 수신 실패 시 전날 종가 유지(섹션 안 깨짐)."""
    from src.market_report.us_report_runner import _apply_index_futures

    async def empty_overnight():
        return {"futures": [], "m7": [], "etf": []}
    monkeypatch.setattr("src.datasource.us.overnight.fetch_us_overnight", empty_overnight)

    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime.now())
    snap.us_indices = [{"name": "나스닥", "price": 17000.0, "change_pct": -1.5}]
    await _apply_index_futures(snap)
    assert snap.us_indices[0]["name"] == "나스닥"  # 교체 안 됨
    assert snap.us_indices[0]["change_pct"] == -1.5
