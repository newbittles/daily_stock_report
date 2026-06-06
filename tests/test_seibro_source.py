"""SEIBro 서학개미 미국 순매수 어댑터 — 오프라인(픽스처/모킹) 검증.

네트워크 없이: XML 파싱(순수) · ISIN→티커 · 조회기간 계산 · 정렬/캐시 · HARD STOP(429).
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from src.datasource.us import seibro_source as ss
from src.datasource.us.seibro_symbols import ticker_for

_FIXTURE = Path(__file__).parent / "fixtures" / "seibro_us_netbuy.xml"


def test_parse_netbuy_xml_fields_and_order() -> None:
    rows = ss.parse_netbuy_xml(_FIXTURE.read_bytes())
    assert len(rows) == 5
    top = rows[0]
    assert top.rank == 1
    assert top.isin == "US5951121038"
    assert top.name_en == "MICRON TECHNOLOGY INC"
    assert top.buy_amt == 1496502003.0
    assert top.sell_amt == 671675388.0
    assert top.net_buy_amt == 824826616.0
    # rank 오름차순 보장
    assert [r.rank for r in rows] == [1, 2, 3, 4, 5]


def test_parse_netbuy_xml_empty() -> None:
    assert ss.parse_netbuy_xml(b"<vector result='0'></vector>") == []


def test_ticker_for_observed_and_unknown() -> None:
    assert ticker_for("US5951121038") == "MU"      # MICRON
    assert ticker_for("US0420682058") == "ARM"     # ARM
    assert ticker_for("US88160R1014") == "TSLA"    # 메가캡 선등록
    assert ticker_for("US77926X3200") == "DRAM"    # Roundhill Memory ETF (#436 확인)
    assert ticker_for("US87975E7765") == "NASA"    # Tema Space ETF (#436 확인)
    assert ticker_for("US25461A5285") == ""        # 미매핑 레버리지 ETF


def test_lookback_range_window() -> None:
    end = date(2026, 6, 4)
    start_dt, end_dt = ss.lookback_range(trading_days=5, end=end)
    assert end_dt == "20260604"
    # 5거래일 ≈ 11 캘린더일 여유 → start < end, YYYYMMDD 형식
    assert len(start_dt) == 8 and start_dt < end_dt


def test_fetch_sorts_by_netbuy_desc_and_caches(monkeypatch, tmp_path) -> None:
    """fetch_us_net_buy: 순매수 내림차순 정렬 + 당일 캐시 재사용(2회차 네트워크 미호출)."""
    monkeypatch.setattr(ss, "_CACHE", tmp_path / "seibro_cache.json")
    rows = ss.parse_netbuy_xml(_FIXTURE.read_bytes())
    calls = {"n": 0}

    def fake_fetch(start_dt, end_dt, top, d_type="4"):
        calls["n"] += 1
        return list(rows)

    monkeypatch.setattr(ss, "_fetch_sync", fake_fetch)

    first = asyncio.run(ss.fetch_us_net_buy(trading_days=5, top=50))
    assert calls["n"] == 1
    # 순매수 내림차순(MICRON 824M > ARM 264M > MARVELL 240M > ALPHABET 225M > ROUNDHILL ETF? )
    nets = [r.net_buy_amt for r in first]
    assert nets == sorted(nets, reverse=True)
    assert first[0].isin == "US5951121038"  # MICRON 최상위

    second = asyncio.run(ss.fetch_us_net_buy(trading_days=5, top=50))
    assert calls["n"] == 1  # 캐시 히트 → 재호출 없음
    assert [r.isin for r in second] == [r.isin for r in first]


def test_fetch_sync_hard_stop_on_429(monkeypatch) -> None:
    """HTTP 429 → 즉시 빈 리스트(자동 재시도 금지, 전역 §7)."""
    class FakeResp:
        status_code = 429
        content = b""

    def fake_post(*a, **k):
        return FakeResp()

    monkeypatch.setattr(ss.requests, "post", fake_post)
    assert ss._fetch_sync("20260530", "20260604", 50) == []


def test_fetch_sync_ok(monkeypatch) -> None:
    """정상 200 → 파싱된 행 반환."""
    class FakeResp:
        status_code = 200
        content = _FIXTURE.read_bytes()

    monkeypatch.setattr(ss.requests, "post", lambda *a, **k: FakeResp())
    rows = ss._fetch_sync("20260530", "20260604", 50)
    assert len(rows) == 5 and rows[0].isin == "US5951121038"


def test_telegram_section_splits_stock_etf_with_ticker() -> None:
    """개별종목/ETF 칸 분리 + 종목명 옆 티커 표시(사용자 2026-06-05), pre/post 모두."""
    from datetime import datetime

    from src.market_report.models import MarketSnapshot
    from src.market_report.telegram_notify import (
        _format_kr_us_netbuy,
        _format_post_summary,
        _format_pre_summary,
    )

    snap = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 5, 14, 50))
    snap.kr_us_netbuy = [
        {"ticker": "MU", "name": "마이크론", "net_buy_usd": 8.2e8, "net_buy_eok": 11960, "is_etf": False},
        {"ticker": "", "name": "Roundhill Memory ETF", "net_buy_usd": 3.8e8, "net_buy_eok": 5639, "is_etf": True},
        {"ticker": "ARM", "name": "ARM홀딩스", "net_buy_usd": 2.6e8, "net_buy_eok": 4281, "is_etf": False},
    ]
    sect = _format_kr_us_netbuy(snap)
    txt = "\n".join(sect)
    assert "개별종목" in txt and "ETF" in txt          # 칸 분리
    assert "마이크론(MU)" in txt                         # 티커 병기
    assert "11,960억" in txt
    # ETF 칸엔 ETF만, 개별종목 칸엔 개별만
    assert "Roundhill Memory ETF" in txt

    # 서학개미 TOP5는 한국장 리포트(장전/장후)에서 제외됨(사용자 2026-06-05, 미국 데이터라 부적절).
    assert "한국인 매수" not in _format_pre_summary(snap)
    snap.mode = "post_close"
    assert "한국인 매수" not in _format_post_summary(snap)


def test_is_etf_name() -> None:
    from src.market_report.pipeline import _is_etf_name

    assert _is_etf_name("ROUNDHILL MEMORY ETF") is True
    assert _is_etf_name("IPATH SERIES B ETN") is True
    assert _is_etf_name("MICRON TECHNOLOGY INC") is False
    assert _is_etf_name("ARM HOLDINGS PLC SPON ADS") is False


def test_prev_trading_day_skips_weekend() -> None:
    from datetime import date

    # 2026-06-08 월요일 → 전일=6/7(일)→6/6(토)→6/5(금)
    assert ss.prev_trading_day(end=date(2026, 6, 7)) == "20260605"  # 일
    assert ss.prev_trading_day(end=date(2026, 6, 6)) == "20260605"  # 토
    assert ss.prev_trading_day(end=date(2026, 6, 4)) == "20260604"  # 목(평일 그대로)


def test_fetch_explicit_range_key(monkeypatch, tmp_path) -> None:
    """start_dt/end_dt 지정 시 그 구간으로 조회·캐시(key에 반영)."""
    monkeypatch.setattr(ss, "_CACHE", tmp_path / "c.json")
    seen = {}

    def fake_fetch(start_dt, end_dt, top, d_type="4"):
        seen["range"] = (start_dt, end_dt)
        return ss.parse_netbuy_xml(_FIXTURE.read_bytes())

    monkeypatch.setattr(ss, "_fetch_sync", fake_fetch)
    asyncio.run(ss.fetch_us_net_buy(top=50, start_dt="20260604", end_dt="20260604"))
    assert seen["range"] == ("20260604", "20260604")


def test_attach_kr_netbuy_to_picks(monkeypatch) -> None:
    """Top3/스크린 픽에 서학개미 순매수금액(전일+5일)이 티커 교차로 부착되는지."""
    from datetime import datetime

    from src.datasource.us import fdr_source, seibro_source
    from src.datasource.us import seibro_symbols  # noqa: F401
    from src.market_report.models import MarketSnapshot
    from src.market_report.pipeline import _attach_kr_netbuy_to_picks

    rows = ss.parse_netbuy_xml(_FIXTURE.read_bytes())  # MU, ARM, MRVL, GOOGL 등(매핑됨)

    async def fake_netbuy(*a, **k):
        return rows

    async def fake_rate():
        return 1450.0

    monkeypatch.setattr(seibro_source, "fetch_us_net_buy", fake_netbuy)
    monkeypatch.setattr(fdr_source, "fetch_usd_krw", fake_rate)

    snap = MarketSnapshot(mode="us_morning", generated_at=datetime(2026, 6, 5, 7, 0))
    snap.us_top3 = [{"symbol": "MU", "name": "마이크론", "price": 1.0, "change_pct": 0.0}]
    snap.us_screen_groups = [{"label": "C", "initial": "C", "picks": [
        {"symbol": "ZZZZ", "name": "권외", "price": 1.0, "change_pct": 0.0}]}]
    asyncio.run(_attach_kr_netbuy_to_picks(snap))

    mu = snap.us_top3[0]
    assert mu["kr_netbuy_5d_eok"] is not None and mu["kr_netbuy_5d_eok"] > 0  # MICRON 824M×1450/1e8
    # TOP50 권외(미매칭) 종목엔 부착 안 됨
    assert "kr_netbuy_5d_eok" not in snap.us_screen_groups[0]["picks"][0]


def test_telegram_section_empty_when_no_data() -> None:
    from datetime import datetime

    from src.market_report.models import MarketSnapshot
    from src.market_report.telegram_notify import _format_kr_us_netbuy

    snap = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 5, 14, 50))
    assert _format_kr_us_netbuy(snap) == []
