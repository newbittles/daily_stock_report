"""지수 약세 경고(index_regime_warn) — 정배열 깨짐 + 60일선 아래(하락전환)면 추천 섹션 상단 경고.

사용자 2026-06-19 피드백: 코스닥 지수가 정배열 아님 + 60일선 저항받고 떨어진 국면을
추천 종목 옆 '주의신호'로 띄워야 한다(코스닥 반도체주 고점물림). 개별종목 정배열 필터는
종목 자기 차트만 보고, 종목이 속한 '지수'의 약세는 안 봤던 공백을 보완한다.
"""
from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.pipeline import _fill_market_phase

# 하락전환(60일선 아래·하락방향·정배열 깨짐) — 경고 대상
_WEAK = {5: -2.0, 10: -1.0, 20: -1.5, 60: -3.0, 120: -1.0,
         "rsi": 45, "ret5": -2.0, "ma5_up": False, "aligned": False}
# 정상(정배열·60일선 위) — 경고 미대상
_OK = {5: 1.0, 10: 1.0, 20: 1.0, 60: 2.0, 120: 3.0,
       "rsi": 55, "ret5": 1.0, "ma5_up": True, "aligned": True}


def _snap(ma_gaps: dict) -> MarketSnapshot:
    snap = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 19, 14, 50))
    snap.ma_gaps = ma_gaps
    _fill_market_phase(snap)
    return snap


def test_kosdaq_downtrend_emits_warning():
    """코스닥 하락전환이면 index_regime_warn에 코스닥이 들어가고 60일선 이격이 담긴다."""
    snap = _snap({"코스피": _OK, "코스닥": _WEAK})
    warns = snap.index_regime_warn
    assert [w["label"] for w in warns] == ["코스닥"]
    w = warns[0]
    assert w["name"] == "하락전환"
    assert w["g60"] == -3.0
    assert w["emoji"]  # 이모지 동반(🔻)


def test_healthy_market_no_warning():
    """코스피·코스닥 둘 다 정상이면 경고는 비어 있다(오탐 방지)."""
    snap = _snap({"코스피": _OK, "코스닥": _OK})
    assert snap.index_regime_warn == []


def test_both_weak_emits_both():
    """두 지수 모두 하락전환이면 둘 다 경고에 포함된다."""
    snap = _snap({"코스피": _WEAK, "코스닥": _WEAK})
    assert {w["label"] for w in snap.index_regime_warn} == {"코스피", "코스닥"}


def test_warning_consistent_with_market_phase():
    """경고에 담긴 라벨은 market_phase에서 '하락전환'으로 표기된 라벨과 정확히 일치한다."""
    snap = _snap({"코스피": _OK, "코스닥": _WEAK})
    weak_phase = {lbl for lbl, ph in snap.market_phase.items() if ph["name"] == "하락전환"}
    assert {w["label"] for w in snap.index_regime_warn} == weak_phase


def test_empty_ma_gaps_safe():
    """ma_gaps가 비어도 경고는 빈 리스트(예외 없음)."""
    snap = _snap({})
    assert snap.index_regime_warn == []
