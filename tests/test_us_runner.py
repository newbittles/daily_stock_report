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
