"""익절 시그널(take_profit_signal) — 볼린저 상단 분출 2단계 결정론 검증 (사용자 2026-06-17).

CLIMAX  🔥 종가가 BB상단 위 마감(익절권 경고).
REENTRY 💰 전일 상단 위였다가 당일 밴드 안 복귀(분출 종료 — 익절 신호, REENTRY 우선).
"""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import TP_CLIMAX, TP_REENTRY, take_profit_signal


def _series(closes: list[float]) -> list[Candle]:
    return [Candle(date=str(i), open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000)
            for i, c in enumerate(closes)]


def test_climax_when_close_above_upper() -> None:
    # 잔잔한 베이스 + 마지막 봉 급등 → BB상단 강돌파(익절권)
    s = _series([100.0] * 21 + [200.0])
    r = take_profit_signal(s)
    assert r.metrics["state"] == TP_CLIMAX
    assert r.metrics["over_pct"] > 0


def test_reentry_when_band_reentry_after_breakout() -> None:
    # 직전 봉 상단 강돌파(200) → 당일 밴드 안 복귀(110) → 분출 종료(익절 신호)
    s = _series([100.0] * 20 + [200.0, 110.0])
    r = take_profit_signal(s)
    assert r.metrics["state"] == TP_REENTRY


def test_none_inside_band() -> None:
    s = _series([100.0] * 25)
    r = take_profit_signal(s)
    assert r.metrics["state"] is None
    assert not r.matched


def test_insufficient_data() -> None:
    r = take_profit_signal(_series([100.0] * 5))
    assert r.metrics["state"] is None


def test_reentry_priority_over_climax() -> None:
    # 전일 돌파 + 당일에도 살짝 상단 위면 CLIMAX, 밴드 안이면 REENTRY가 우선임을 구분 확인
    reentry = take_profit_signal(_series([100.0] * 20 + [200.0, 105.0]))
    assert reentry.metrics["state"] == TP_REENTRY
