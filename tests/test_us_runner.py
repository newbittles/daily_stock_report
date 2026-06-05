"""us_report_runner — 장전/장중 공용 러너가 모드별 차별점(overlay·premarket_top)을
올바로 주입하는지 검증(오프라인, 2026-06-06 리팩토링 회귀 가드).

us_premarket=프리장 오버레이+급등TOP5, us_intraday=장중 오버레이(TOP5 없음), per_group=3, mode 세팅.
"""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import MarketSnapshot


def _mock_pipeline(monkeypatch, seen: dict) -> None:
    from src.market_report import pipeline as P

    base = MarketSnapshot(mode="seed", generated_at=datetime(2026, 6, 5, 19, 0))

    async def _snap() -> MarketSnapshot:
        return base

    async def _screen(s, per_group: int = 8) -> None:
        seen["per_group"] = per_group

    async def _pre(s) -> None:
        seen["overlay"] = "premarket"

    async def _intra(s) -> None:
        seen["overlay"] = "intraday"

    async def _noop(s) -> None:
        return None

    async def _analyze(s):
        seen["analyze"] = True
        return s

    monkeypatch.setattr(P, "collect_us_snapshot", _snap)
    monkeypatch.setattr(P, "_collect_us_screening", _screen)
    monkeypatch.setattr(P, "_overlay_premarket", _pre)
    monkeypatch.setattr(P, "_overlay_intraday", _intra)
    monkeypatch.setattr(P, "_collect_sector_leaders", _noop)
    monkeypatch.setattr(P, "_attach_kr_netbuy_to_picks", _noop)
    monkeypatch.setattr(P, "_render_candles", _noop)
    monkeypatch.setattr("src.market_report.analyzer.analyze", _analyze)
    monkeypatch.setattr("src.market_report.render.render_report", lambda s: None)


async def test_premarket_runner_wiring(monkeypatch) -> None:
    seen: dict = {}
    _mock_pipeline(monkeypatch, seen)
    import src.market_report.us_premarket as PM
    monkeypatch.setattr(PM, "_build_premarket_top", lambda s, n=5: seen.__setitem__("top", True))

    out = await PM.run_us_premarket(do_telegram=False, do_publish=False, force=True)
    assert out is not None and out.mode == "us_premarket"
    assert seen["overlay"] == "premarket"   # 프리장 오버레이 주입
    assert seen.get("top") is True          # 프리장 급등 TOP5 호출
    assert seen["per_group"] == 3           # ABCD 3개씩
    assert seen.get("analyze") is True


async def test_intraday_runner_wiring(monkeypatch) -> None:
    seen: dict = {}
    _mock_pipeline(monkeypatch, seen)
    import src.market_report.us_premarket as PM
    monkeypatch.setattr(PM, "_build_premarket_top", lambda s, n=5: seen.__setitem__("top", True))
    import src.market_report.us_intraday as IT

    out = await IT.run_us_intraday(do_telegram=False, do_publish=False, force=True)
    assert out is not None and out.mode == "us_intraday"
    assert seen["overlay"] == "intraday"    # 장중 오버레이 주입
    assert seen.get("top") is None          # 장중엔 프리장 TOP5 없음(차별점)
    assert seen["per_group"] == 3


async def test_weekend_skip_returns_none(monkeypatch) -> None:
    """주말이면 None(스킵) — force=False."""
    from src.market_report import us_report_runner as R

    class _FakeDT:
        @staticmethod
        def now():
            return datetime(2026, 6, 6, 19, 0)  # 토요일

    monkeypatch.setattr(R, "datetime", _FakeDT)

    async def _overlay(s) -> None:
        return None

    out = await R.run_us_report("us_premarket", _overlay, do_telegram=False, do_publish=False)
    assert out is None


async def test_overlay_intraday_shares_logic(monkeypatch) -> None:
    """_overlay_intraday — 공용 _overlay_live_quote 경유: change_pct=장중, close_pct=마감 보존, intraday 플래그."""
    from src.market_report import pipeline as P

    snap = MarketSnapshot(mode="us_intraday", generated_at=datetime(2026, 6, 5, 23, 50))
    snap.us_top3 = [{"symbol": "NVDA", "change_pct": 1.0, "price": 450.0}]
    snap.us_screen_groups = [{"initial": "C", "picks": [
        {"symbol": "AAPL", "change_pct": -1.0, "price": 310.0},
        {"symbol": "ZZZZ", "change_pct": 3.0, "price": 50.0},  # 미체결
    ]}]

    async def fake_iq(syms):
        return {"NVDA": {"price": 462.0, "change_pct": 2.7}, "AAPL": {"price": 312.0, "change_pct": 0.5}}
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_intraday", fake_iq)

    await P._overlay_intraday(snap)
    nv = snap.us_top3[0]
    assert nv["change_pct"] == 2.7 and nv["close_pct"] == 1.0 and nv["intraday"] is True
    assert nv["price"] == 450.0 and nv["intraday_price"] == 462.0  # 가격은 마감 유지
    zz = snap.us_screen_groups[0]["picks"][1]
    assert zz["change_pct"] == 3.0 and zz["intraday"] is False  # 미체결 → 마감값 유지
