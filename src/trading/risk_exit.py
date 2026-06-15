"""실전 리스크관리 청산 레이어 — 순수 함수 (외부 의존 0).

기존 MA 단계청산(ma_exit)은 '추세청산'으로 유지하고, 이 모듈을 **추가 레이어**로 결합한다.
트레이더 표준 결합:
  ① 진입 즉시 하드스톱: 진입가 −mult×ATR(14) 또는 −8% 중 '가까운(손실 작은) 것'
     전략별 mult: A/B(타이트)=1.5 · C/D(와이드)=2.5 · 정보없음=2.0
  ② ATR 트레일링(샹들리에): 보유 최고가 −3×ATR. 초기손절보다 높아지면 상향.
  ③ +1R 도달 시 절반익절 & 손절 본전 이동(리스크 제거)
  ④ 변동성 포지션사이징: 1트레이드 위험 = 계좌×risk_pct ÷ (진입−손절)
MA 단계청산과는 '먼저 닿는 것' 우선(auto_trader에서 결합).
"""
from __future__ import annotations

# 전략별 ATR 배수 (decide_exit '넓은 쪽 우선' 컨벤션과 동일)
_TIGHT = {"A", "B"}
_WIDE = {"C", "D"}
DEFAULT_PCT_CAP = 0.08      # 하드스톱 최대 손실폭(오닐 −7~8%)
CHANDELIER_MULT = 3.0       # ATR 트레일링 배수
PARTIAL_R = 1.0             # +1R 도달 시 절반 익절


def atr_stop_mult(strategies: list[str] | None) -> float:
    """전략별 ATR 손절 배수. C/D 있으면 와이드(2.5), A/B만이면 타이트(1.5), 없으면 2.0."""
    s = set(strategies or [])
    if s & _WIDE:
        return 2.5
    if s & _TIGHT:
        return 1.5
    return 2.0


def hard_stop_price(
    entry: float, atr: float | None, strategies: list[str] | None = None,
    pct_cap: float = DEFAULT_PCT_CAP,
) -> float:
    """초기 하드스톱가. ATR 손절과 −pct_cap% 손절 중 진입가에 가까운(높은) 쪽.

    ATR None이면 퍼센트 손절만 사용. 최대손실 한도(−pct_cap%)를 항상 보장.
    """
    pct_stop = entry * (1 - pct_cap)
    if atr is None or atr <= 0:
        return pct_stop
    atr_stop = entry - atr_stop_mult(strategies) * atr
    return max(atr_stop, pct_stop)


def chandelier_stop(
    highest: float, atr: float | None, mult: float = CHANDELIER_MULT
) -> float | None:
    """샹들리에 트레일링스톱 = 보유 최고가 − mult×ATR. ATR 없으면 None."""
    if atr is None or atr <= 0:
        return None
    return highest - mult * atr


def r_multiple(entry: float, current: float, initial_stop: float) -> float | None:
    """현재가의 R 배수 = (현재−진입) / (진입−초기손절). 위험단위 0 이하면 None."""
    risk = entry - initial_stop
    if risk <= 0:
        return None
    return (current - entry) / risk


def position_size(equity: float, risk_pct: float, entry: float, stop: float) -> int:
    """변동성 포지션사이징 — 1트레이드 위험을 계좌의 risk_pct로 균일화.

    수량 = floor(계좌×risk_pct / (진입−손절)). 위험·예산 비정상이면 0.
    """
    per_share_risk = entry - stop
    if equity <= 0 or risk_pct <= 0 or per_share_risk <= 0:
        return 0
    return int((equity * risk_pct) // per_share_risk)


def active_stop(
    entry: float, initial_stop: float, highest: float, atr: float | None, *,
    partial_taken: bool, chandelier_mult: float = CHANDELIER_MULT,
) -> float:
    """현재 유효 손절가 = max(초기 하드스톱, 샹들리에 트레일링, [절반익절 후]본전).

    셋 중 가장 높은(보호 강한) 값. 절반익절 뒤에는 최소 본전(entry)까지 손절 상향.
    """
    candidates = [initial_stop]
    ch = chandelier_stop(highest, atr, chandelier_mult)
    if ch is not None:
        candidates.append(ch)
    if partial_taken:
        candidates.append(entry)
    return max(candidates)


def decide_risk_exit(
    entry: float, initial_stop: float, highest: float, atr: float | None, current: float,
    strategies: list[str] | None = None, partial_taken: bool = False,
) -> tuple[str, str, float]:
    """리스크 레이어 청산 판정. 반환: (action, reason, new_stop).

    우선순위:
      1. 유효 손절가 이탈 → SELL_ALL (하드/트레일링/본전 스톱)
      2. +1R 도달(미익절) → SELL_HALF + 손절 본전 이동(new_stop=entry)
      3. 그 외 → HOLD (new_stop=현 유효 손절가)
    action ∈ {HOLD, SELL_HALF, SELL_ALL}.
    """
    stop = active_stop(entry, initial_stop, highest, atr, partial_taken=partial_taken)
    if current <= stop:
        if partial_taken and stop == entry:
            return ("SELL_ALL", "본전스톱 이탈", stop)
        if stop > initial_stop:
            return ("SELL_ALL", "트레일링스톱 이탈", stop)
        return ("SELL_ALL", "하드스톱 이탈", stop)
    if not partial_taken:
        r = r_multiple(entry, current, initial_stop)
        if r is not None and r >= PARTIAL_R:
            return ("SELL_HALF", f"+{PARTIAL_R:.0f}R 절반익절·손절 본전이동", entry)
    return ("HOLD", "", stop)


_SEVERITY = {"HOLD": 0, "SELL_HALF": 1, "SELL_ALL": 2}


def combine_exits(ma: tuple[str, str], risk: tuple[str, str]) -> tuple[str, str]:
    """MA 추세청산과 리스크 청산을 '먼저 닿는 것'(더 강한 매도)으로 결합.

    강도: SELL_ALL > SELL_HALF > HOLD. 동일 강도면 두 사유를 모두 보존.
    """
    ma_action, ma_reason = ma
    risk_action, risk_reason = risk
    sm, sr = _SEVERITY[ma_action], _SEVERITY[risk_action]
    if sm == sr:
        reasons = [x for x in (ma_reason, risk_reason) if x]
        return (ma_action, " · ".join(reasons))
    return (ma_action, ma_reason) if sm > sr else (risk_action, risk_reason)


def should_buy_pyramid(held: bool, last_entry_date: str | None, today: str) -> bool:
    """Top3 재추천 시 매수 여부. 미보유면 매수, 보유여도 과거 진입이면 추가매수(피라미딩).

    같은 날(last_entry_date == today) 중복 매수만 금지 → 크론 재실행/중복 호출 안전.
    """
    if not held:
        return True
    return last_entry_date != today
