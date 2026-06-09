"""F. 60일선 지지(참고용) 리포트 섹션 — 수집 + 템플릿 렌더링 검증.

배경(사용자 2026-06-09): F(is_ma60_support)는 백테스트상 다음날 반등 엣지가 없어
'추천 시그널'이 아닌 '참고용' 별도 섹션으로만 노출한다(가중치 0·Top3 미반영).
순수 패턴 로직은 test_ma60_support.py가 검증 → 여기선 리포트 와이어링/표시를 검증.
"""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.render import _env


def _render(snap: MarketSnapshot) -> str:
    return _env().get_template("report.html").render(title="테스트 리포트", snap=snap)


def _base_snap() -> MarketSnapshot:
    return MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 9, 16, 30))


def test_support_section_renders_when_picks_present() -> None:
    """support_picks가 있으면 F 참고 섹션이 표시된다(종목명·근거수치·면책)."""
    snap = _base_snap()
    snap.support_picks = [{
        "ticker": "319660", "name": "피에스케이", "price": 38000.0,
        "change_pct": 1.23, "reason": "60일선 지지 (저가 -0.5%·종가 +0.3%·꼬리지지 70%)",
        "volume": 120000, "trade_value": 4_560_000_000,
    }]
    html = _render(snap)
    assert "F. 60일선 지지" in html
    assert "피에스케이" in html
    assert "319660" in html
    assert "꼬리지지" in html  # 근거수치(reason) 노출
    # 면책 + 미추천 표기 (참고용·Top3 미반영)
    assert "매수 추천" in html
    assert "미반영" in html


def test_support_section_absent_when_no_picks() -> None:
    """support_picks가 비면 F 섹션 자체가 렌더되지 않는다."""
    snap = _base_snap()
    snap.support_picks = []
    html = _render(snap)
    assert "F. 60일선 지지" not in html


def test_collect_screen_picks_has_support_out_param() -> None:
    """collect_screen_picks가 support_out 수집 파라미터를 노출한다(와이어링 계약)."""
    import inspect

    from src.market_report.strategy_section import collect_screen_picks

    params = inspect.signature(collect_screen_picks).parameters
    assert "support_out" in params
