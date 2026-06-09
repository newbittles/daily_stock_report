"""G. 삼각수렴(코일) 임박 — 리포트 연동(섹션 렌더·수집 시그니처·추세선 산출) 검증.

배경(사용자 2026-06-09): 코일을 돌파 임박 전 선제 포착, 차트에 고점/저점 추세선 표시.
순수 검출 로직은 test_coil_squeeze.py가 검증 → 여기선 와이어링·표시·작도좌표를 검증.
"""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.render import _env


def _render(snap: MarketSnapshot) -> str:
    return _env().get_template("report.html").render(title="테스트", snap=snap)


def _snap() -> MarketSnapshot:
    return MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 9, 16, 30))


def test_coil_section_renders_with_picks() -> None:
    snap = _snap()
    snap.coil_picks = [{
        "ticker": "087010", "name": "펩트론", "price": 200000.0, "change_pct": 0.5,
        "shape": "바닥지지수렴", "bb_width": 12.8, "ma_conv": 1.41,
        "reason": "삼각수렴 바닥지지수렴 (BB폭 12.8%·이격 1.4%)",
        "chart_url": "charts/2026-06-09-087010-coil.png",
    }]
    html = _render(snap)
    assert "삼각수렴 임박" in html
    assert "펩트론" in html
    assert "바닥지지수렴" in html
    assert "charts/2026-06-09-087010-coil.png" in html  # 차트 임베드
    assert "매수추천 아님" in html and "미반영" in html   # 면책


def test_coil_section_absent_when_empty() -> None:
    snap = _snap()
    snap.coil_picks = []
    assert "삼각수렴 임박" not in _render(snap)


def test_collect_has_coil_out_param() -> None:
    import inspect

    from src.market_report.strategy_section import collect_screen_picks

    assert "coil_out" in inspect.signature(collect_screen_picks).parameters


def test_coil_trendlines_converge() -> None:
    """삼각수렴선: 상단(고점) 우하향 + 하단(저점) 우상향 → 수렴."""
    from src.market_report.chart import _coil_trendlines

    # 대칭 수렴 합성 지그재그: 6봉 주기, 고점 피벗 하락 + 저점 피벗 상승 → 수렴
    n = 60
    hi: list[float] = []
    lo: list[float] = []
    for i in range(n):
        if i % 6 == 0:        # 고점 피벗(하락)
            hi.append(225 - i * 0.4); lo.append(225 - i * 0.4 - 4)
        elif i % 6 == 3:      # 저점 피벗(상승)
            hi.append(184 + i * 0.3); lo.append(180 + i * 0.3)
        else:                 # 중간봉(피벗 아님)
            hi.append(200.0); lo.append(199.0)
    w0, (su, iu), (sl_lo, a1), ph, la1, la2 = _coil_trendlines(hi, lo, n - 1)
    assert su < 0      # 상단선 우하향(고점 피벗 연결)
    assert sl_lo > 0   # 하단선 우상향(깊은저점→꼭짓점 저점, 수렴)
