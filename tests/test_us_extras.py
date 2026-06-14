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


def test_format_us_overnight_close_and_session_split() -> None:
    """미국 야간 — 선물 + M7·SOXL 마감·프리장 병기(#503)."""
    snap = MarketSnapshot(mode="midday", generated_at=datetime.now())
    snap.us_overnight = {
        "futures": [{"symbol": "NQ=F", "name": "나스닥 선물", "price": 29223.0, "change_pct": 0.68}],
        "m7": [{"symbol": "NVDA", "name": "엔비디아", "price": 205.1, "change_pct": -6.2,
                "session_pct": 2.0, "session_label": "프리장"},
               {"symbol": "AAPL", "name": "애플", "price": 307.3, "change_pct": -1.1,
                "session_pct": None, "session_label": ""}],  # 세션 없음 → 마감만
        "etf": [{"symbol": "SOXL", "name": "SOXL(반도체 3X)", "price": 182.5, "change_pct": -30.5,
                 "session_pct": 7.0, "session_label": "프리장"}],
    }
    out = "\n".join(_format_us_overnight(snap))
    assert "나스닥 선물 +0.68%" in out
    assert "엔비디아 마감 -6.2% · 프리장 +2.0%" in out
    assert "애플 마감 -1.1%" in out and "애플 마감 -1.1% ·" not in out  # 세션 없으면 마감만
    assert "SOXL(반도체 3X) 마감 -30.5% · 프리장 +7.0%" in out


def test_format_us_overnight_excludes_futures_for_premarket() -> None:
    """us_premarket: 선물은 지수 카드에 있어 야간 섹션에서 제외(#503 B)."""
    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime.now())
    snap.us_overnight = {
        "futures": [{"symbol": "NQ=F", "name": "나스닥 선물", "price": 1.0, "change_pct": 0.5}],
        "m7": [{"symbol": "NVDA", "name": "엔비디아", "price": 205.1, "change_pct": -6.2,
                "session_pct": 2.0, "session_label": "프리장"}],
        "etf": [],
    }
    out = "\n".join(_format_us_overnight(snap, include_futures=False))
    assert "나스닥 선물" not in out          # 선물 제외
    assert "엔비디아 마감 -6.2% · 프리장 +2.0%" in out


async def test_collect_sector_leaders_appends_soxl(monkeypatch) -> None:
    """섹터별 대장주 끝에 SOXL(반도체 3X)이 고정 추가되는지(사용자 2026-06-09)."""
    from src.market_report import pipeline as P

    async def fake_leaders(names):
        return [{"sector": "반도체", "symbol": "NVDA", "name": "엔비디아",
                 "price": 200.0, "change_pct": 1.0, "week_pct": 3.0}]

    async def fake_soxl():
        return {"sector": "반도체 3X", "symbol": "SOXL", "name": "반도체 3X(SOXL)",
                "price": 30.0, "change_pct": 5.0, "week_pct": 9.0}

    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_sector_leaders", fake_leaders)
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_soxl_leader", fake_soxl)

    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_sectors = [{"name": "반도체", "change_pct": 2.0}]
    await P._collect_sector_leaders(snap)

    syms = [d["symbol"] for d in snap.us_sector_leaders]
    assert "SOXL" in syms and syms[-1] == "SOXL"   # 본 리스트 뒤에 병기


async def test_collect_sector_leaders_soxl_no_dup(monkeypatch) -> None:
    """SOXL이 이미 섹터 대장에 있으면 중복 추가하지 않음."""
    from src.market_report import pipeline as P

    async def fake_leaders(names):
        return [{"sector": "반도체 3X", "symbol": "SOXL", "name": "반도체 3X(SOXL)",
                 "price": 30.0, "change_pct": 5.0, "week_pct": 9.0}]

    async def fake_soxl():
        return {"sector": "반도체 3X", "symbol": "SOXL", "name": "반도체 3X(SOXL)",
                "price": 30.0, "change_pct": 5.0, "week_pct": 9.0}

    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_sector_leaders", fake_leaders)
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_soxl_leader", fake_soxl)

    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_sectors = [{"name": "반도체", "change_pct": 2.0}]
    await P._collect_sector_leaders(snap)

    assert [d["symbol"] for d in snap.us_sector_leaders].count("SOXL") == 1


def test_format_us_overnight_empty() -> None:
    snap = MarketSnapshot(mode="midday", generated_at=datetime.now())
    assert _format_us_overnight(snap) == []
    snap.us_overnight = {"futures": [], "m7": []}
    assert _format_us_overnight(snap) == []


def test_midday_shows_overnight_at_top() -> None:
    snap = MarketSnapshot(mode="midday", generated_at=datetime(2026, 6, 8, 11, 40))
    snap.kospi = _idx("KOSPI", 8160.5, -2.0)
    # KM2(2026-06-14): 선물·기타 M7 제외, 테슬라·마이크론·SOXL·EWY만
    snap.us_overnight = {
        "futures": [{"symbol": "NQ=F", "name": "나스닥 선물", "price": 1.0, "change_pct": 0.65}],
        "m7": [{"symbol": "TSLA", "name": "테슬라", "change_pct": 2.1}],
        "etf": [{"symbol": "EWY", "name": "한국 ETF(EWY)", "change_pct": 1.2}],
        "extra": [{"symbol": "MU", "name": "마이크론", "change_pct": 1.5}],
    }
    msg = _format_midday_summary(snap)
    assert "미국 야간" in msg
    # 미국 야간이 코스피(지수)보다 위
    assert msg.index("미국 야간") < msg.index("코스피")
    # 선물은 제외, 테슬라·마이크론·EWY만
    assert "나스닥 선물" not in msg
    assert "테슬라" in msg and "마이크론" in msg and "EWY" in msg


def test_us_morning_shows_ewy() -> None:
    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2},
                       {"name": "나스닥", "price": 17000.0, "change_pct": 1.5}]
    snap.ewy = {"name": "EWY(한국 MSCI ETF)", "price": 175.19, "change_pct": -14.11,
                "session_pct": 0.5, "session_label": "애프터", "date": "2026-06-05"}
    msg = _format_us_morning_summary(snap)
    assert "EWY" in msg and "마감 -14.11%" in msg and "애프터 +0.50%" in msg  # 병기(#507)


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


async def test_apply_index_realtime_for_intraday(monkeypatch) -> None:
    """장중(us_intraday): 선물 아닌 실시간 지수(^GSPC/^IXIC)로 교체, 이름은 지수 그대로(#511)."""
    from src.market_report.us_report_runner import _apply_index_realtime

    def fake_fetch_one(sym):
        return {"^GSPC": {"price": 7443.0, "change_pct": 0.81},
                "^IXIC": {"price": 26035.0, "change_pct": 1.27}}.get(sym)
    monkeypatch.setattr("src.datasource.us.overnight._fetch_one", fake_fetch_one)

    snap = MarketSnapshot(mode="us_intraday", generated_at=datetime.now())
    snap.us_indices = [
        {"name": "S&P500", "price": 5300.0, "change_pct": -1.2},
        {"name": "나스닥", "price": 17000.0, "change_pct": -1.5},
        {"name": "다우", "price": 38000.0, "change_pct": -0.8},
    ]
    await _apply_index_realtime(snap)
    sp, nq, dow = snap.us_indices
    assert sp["change_pct"] == 0.81 and sp["is_realtime"] and "선물" not in sp["name"]  # 지수 그대로
    assert nq["change_pct"] == 1.27 and nq["name"] == "나스닥"
    assert dow["name"] == "다우" and dow["change_pct"] == -0.8  # 매핑 없는 지수 유지


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
