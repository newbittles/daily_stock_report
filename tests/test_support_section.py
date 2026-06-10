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


def test_support_section_always_shown_with_placeholder() -> None:
    """support_picks가 비어도 F 섹션은 pre/post에서 항상 노출 + '없음' 표기(사용자 2026-06-10).

    예전엔 빈 날 섹션이 통째로 사라져 '누락'처럼 보이던 문제 → E 섹션과 동일 패턴으로 통일."""
    snap = _base_snap()
    snap.support_picks = []
    html = _render(snap)
    assert "F. 60일선 지지" in html
    assert "60일선 지지 마감 종목 없음" in html


def test_support_section_renders_us_stock_dollar() -> None:
    """미국 리포트(us_premarket)에서도 F 섹션이 symbol·$로 렌더(사용자 2026-06-10)."""
    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime(2026, 6, 9, 19, 0))
    snap.support_picks = [{"symbol": "AAPL", "name": "애플", "price": 230.55,
                           "change_pct": 1.2, "reason": "60일선 지지"}]
    html = _render(snap)
    assert "F. 60일선 지지" in html
    assert "애플" in html and "AAPL" in html
    assert "$230.55" in html  # 달러 표기(원 아님)


def test_support_section_hidden_in_non_close_modes() -> None:
    """F는 전략스크린이 도는 pre/post에서만 노출(프리장·장중 등에선 미노출)."""
    snap = _base_snap()
    snap.mode = "midday"  # type: ignore[assignment]
    snap.support_picks = []
    assert "F. 60일선 지지" not in _render(snap)


def test_collect_screen_picks_has_support_out_param() -> None:
    """collect_screen_picks가 support_out 수집 파라미터를 노출한다(와이어링 계약)."""
    import inspect

    from src.market_report.strategy_section import collect_screen_picks

    params = inspect.signature(collect_screen_picks).parameters
    assert "support_out" in params
