"""순수 패턴 판정 — 외부 의존 없음 (CLAUDE.md §5).

각 함수는 Candle 리스트(또는 종가 리스트)를 받아 bool 또는 판정 dict 반환.
"""
from __future__ import annotations

from src.patterns.core import (
    PatternResult,
    is_above_ichimoku_cloud,
    is_bollinger_breakout,
    is_breakout,
    is_consecutive_bearish,
    is_downtrend_reversal,
    is_leader_oversold_bounce,
    is_macd_golden_cross,
    is_ma20_pullback,
    is_ma_alignment,
    is_near_high,
    is_pullback,
    is_trend_follow,
    is_volume_surge,
    is_weekly_ma_alignment,
    resample_weekly,
)

__all__ = [
    "PatternResult",
    "is_ma_alignment",
    "is_pullback",
    "is_breakout",
    "is_volume_surge",
    "is_above_ichimoku_cloud",
    "is_bollinger_breakout",
    "is_macd_golden_cross",
    "is_ma20_pullback",
    "is_consecutive_bearish",
    "is_trend_follow",
    "is_leader_oversold_bounce",
    "is_downtrend_reversal",
    "is_weekly_ma_alignment",
    "is_near_high",
    "resample_weekly",
]
