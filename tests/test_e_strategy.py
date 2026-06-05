"""E전략(과매도 반등 후보) — 일봉 순수판정 + 4H RSI + 텔레그램 섹션(사용자 2026-06-05).

E = 최근 주도주(신고가 경신)였다가 일봉 RSI≤30 (+ 4시간봉 RSI≤30은 pipeline 결합).
"""
from __future__ import annotations

from datetime import datetime

from src.datasource.base import Candle
from src.datasource.kr_4h import judge_4h_rsi_oversold
from src.market_report.models import MarketSnapshot
from src.market_report.telegram_notify import _format_e_picks
from src.patterns.core import oversold_leader


def _c(close: float, high: float | None = None) -> Candle:
    h = high if high is not None else close
    return Candle(date="20260101", open=close, high=h, low=close, close=close, volume=1000)


def test_oversold_leader_true_after_rally_then_drop() -> None:
    """신고가까지 랠리(주도주) → 이후 하락으로 RSI≤30 → E 매칭."""
    rally = [_c(100 + i) for i in range(100)]          # 100→199 신고가 행진(주도주)
    drop = [_c(199 - i * 2) for i in range(1, 31)]     # 이후 급락 → RSI 바닥
    candles = rally + drop
    res = oversold_leader(candles)
    assert res.matched is True
    assert res.metrics["rsi"] <= 30


def test_oversold_leader_false_when_rsi_high() -> None:
    """계속 상승(RSI 높음)이면 과매도 아님 → E 미매칭."""
    candles = [_c(100 + i) for i in range(140)]
    assert oversold_leader(candles).matched is False


def test_oversold_leader_false_when_not_leader() -> None:
    """신고가 경신 없이 장기 하락만(주도주 아님)이면 RSI 낮아도 미매칭."""
    candles = [_c(200 - i * 0.5) for i in range(140)]  # 처음부터 계속 하락(고점이 맨앞=주도 아님)
    res = oversold_leader(candles)
    assert res.matched is False


def test_judge_4h_rsi_oversold() -> None:
    falling = [100.0 - i for i in range(30)]   # 지속 하락 → RSI 낮음
    rising = [100.0 + i for i in range(30)]
    assert judge_4h_rsi_oversold(falling) is True
    assert judge_4h_rsi_oversold(rising) is False
    assert judge_4h_rsi_oversold([1, 2, 3]) is None


def test_format_e_picks_kr_and_us() -> None:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 5, 16, 30))
    snap.e_picks = [{"ticker": "009150", "name": "삼성전기", "price": 120000,
                     "change_pct": 1.2, "rsi": 28, "reason": "과매도 반등후보"}]
    out = "\n".join(_format_e_picks(snap))
    assert "E 과매도 반등 후보" in out and "삼성전기" in out and "RSI28" in out

    snap.e_picks = [{"symbol": "MU", "name": "마이크론", "price": 95.5,
                     "change_pct": -0.5, "rsi": 25, "reason": "x"}]
    out = "\n".join(_format_e_picks(snap))
    assert "마이크론(MU)" in out and "$95.50" in out
