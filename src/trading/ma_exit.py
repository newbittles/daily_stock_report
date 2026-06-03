"""일봉 이동평균 기반 청산 판정 — 순수 함수.

2차: 20MA 2거래일 연속 종가 이탈 → 50% 매도(SELL_HALF)
3차: 60MA 2거래일 연속 종가 이탈 → 전량 매도(SELL_ALL, 우선)
"""
from __future__ import annotations

from src.indicators.core import moving_average


def consecutive_below(closes: list[float], ma: list[float | None], n: int = 2) -> bool:
    """최근 n개 종가가 모두 대응 MA 아래면 True. MA None이면 False."""
    if len(closes) < n or len(ma) < n:
        return False
    for i in range(-n, 0):
        m = ma[i]
        if m is None or closes[i] >= m:
            return False
    return True


def exit_decision(closes: list[float]) -> str:
    """일봉 종가 시계열 → 'SELL_ALL' | 'SELL_HALF' | 'HOLD'. 60MA(전량) 우선."""
    if consecutive_below(closes, moving_average(closes, 60), 2):
        return "SELL_ALL"
    if consecutive_below(closes, moving_average(closes, 20), 2):
        return "SELL_HALF"
    return "HOLD"
