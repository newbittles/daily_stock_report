"""일봉 이동평균 기반 청산 판정 — 순수 함수.

2차: 20MA 2거래일 연속 종가 이탈 → 50% 매도(SELL_HALF)
3차: 60MA 2거래일 연속 종가 이탈 → 전량 매도(SELL_ALL, 우선)
"""
from __future__ import annotations

from src.indicators.core import moving_average
from src.patterns.core import CROSS_CORRECTION, CROSS_PULLBACK, ma_cross_signal


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


WIDE_STRATEGIES = {"C", "D"}  # 60일선 손절 체계(단계청산) — screener.yaml 전략별 손절


def decide_exit(closes: list[float], strategies: list[str] | None = None) -> tuple[str, str]:
    """청산 행동 + 사유. cross_signal(5·10 데드)과 일봉 MA 손절을 종합.

    체계 선택(2026-06-07, screener.yaml 전략별 손절 반영):
      - wide(C/D 포함 또는 전략정보 없음): 기존 단계청산 — 20MA 50% → 60MA 전량
      - tight(A/B만 매칭): 20일선 2일이탈이 최종 손절선 → 전량
      다중매칭(예: A·C·D)은 넓은 쪽 우선(C/D 있으면 wide, 사용자 2026-06-07).

    우선순위:
      1. 🟢 PULLBACK(추세 위 단기눌림) → HOLD (건강한 눌림이라 보호, 매도 안 함)
      2. 60MA 2연속 이탈 → SELL_ALL (가장 심각, 전량)
      3. [tight] 20MA 2연속 이탈 → SELL_ALL (A/B 최종 손절선)
      4. ⚠️ CORRECTION(조정시작) → SELL_HALF (20MA 이탈 전 선제 50% 익절/손절)
      5. [wide] 20MA 2연속 이탈 → SELL_HALF
      6. 그 외 → HOLD
    반환: (action, reason). action ∈ {HOLD, SELL_HALF, SELL_ALL}.
    """
    cs = ma_cross_signal(closes)
    if cs == CROSS_PULLBACK:
        return ("HOLD", "🟢단기눌림(추세 위)")
    if consecutive_below(closes, moving_average(closes, 60), 2):
        return ("SELL_ALL", "60MA 2연속이탈")
    tight = bool(strategies) and not (set(strategies) & WIDE_STRATEGIES)
    below20 = consecutive_below(closes, moving_average(closes, 20), 2)
    if tight and below20:
        return ("SELL_ALL", "20MA 2연속이탈(A/B 최종손절)")
    if cs == CROSS_CORRECTION:
        return ("SELL_HALF", "⚠️조정시작(5<10·20이격≤7%)")
    if below20:
        return ("SELL_HALF", "20MA 2연속이탈")
    return ("HOLD", "")
