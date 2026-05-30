"""조건 매칭 엔진 — 순수 (외부 의존 없음, CLAUDE.md §5).

전략 conditions dict → Candle 리스트에 적용 → 충족 여부 + 근거 수치.

지원 조건 키:
  ma_alignment {periods}
  pullback {ma_period, tolerance, rsi_max}
  breakout {lookback, vol_mult}
  volume_surge {lookback, mult}
  above_ichimoku (bool)
  bollinger_breakout {period, num_std}
  rsi_between [min, max]
  price_above_ma {period}
  change_pct_between [min, max]   ← 당일 등락률, quote 필요
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.datasource.base import Candle
from src.indicators.core import moving_average, rsi
from src.patterns.core import (
    is_above_ichimoku_cloud,
    is_bollinger_breakout,
    is_breakout,
    is_ma_alignment,
    is_pullback,
    is_volume_surge,
)


@dataclass
class ScreenMatch:
    """단일 종목 × 단일 전략 매칭 결과."""
    matched: bool
    strategy_name: str
    opinion: str = ""
    reasons: list[str] = field(default_factory=list)   # 충족 근거
    metrics: dict[str, float] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)     # 미충족 항목


def _check_condition(
    key: str, params: Any, candles: list[Candle], change_pct: float | None
) -> tuple[bool, str, dict[str, float]]:
    """단일 조건 평가 → (충족, 설명, 근거수치)."""
    closes = [c.close for c in candles]

    if key == "ma_alignment":
        periods = tuple(params.get("periods", [5, 20, 60])) if isinstance(params, dict) else (5, 20, 60)
        r = is_ma_alignment(candles, periods)
        return r.matched, r.reason, r.metrics

    if key == "pullback":
        p = params if isinstance(params, dict) else {}
        r = is_pullback(
            candles,
            ma_period=p.get("ma_period", 20),
            tolerance=p.get("tolerance", 0.03),
            rsi_max=p.get("rsi_max", 55.0),
        )
        return r.matched, r.reason, r.metrics

    if key == "breakout":
        p = params if isinstance(params, dict) else {}
        r = is_breakout(candles, lookback=p.get("lookback", 20), vol_mult=p.get("vol_mult", 1.5))
        return r.matched, r.reason, r.metrics

    if key == "volume_surge":
        p = params if isinstance(params, dict) else {}
        r = is_volume_surge(candles, lookback=p.get("lookback", 5), mult=p.get("mult", 2.0))
        return r.matched, r.reason, r.metrics

    if key == "above_ichimoku":
        if not params:  # false면 조건 무시 → 통과
            return True, "", {}
        r = is_above_ichimoku_cloud(candles)
        return r.matched, r.reason, r.metrics

    if key == "bollinger_breakout":
        p = params if isinstance(params, dict) else {}
        r = is_bollinger_breakout(candles, period=p.get("period", 20), num_std=p.get("num_std", 2.0))
        return r.matched, r.reason, r.metrics

    if key == "rsi_between":
        lo, hi = (params + [0, 100])[:2] if isinstance(params, list) else (0, 100)
        rsi_val = rsi(closes, 14)[-1]
        if rsi_val is None:
            return False, "RSI 계산 불가", {}
        ok = lo <= rsi_val <= hi
        return ok, f"RSI {rsi_val:.0f} ({'OK' if ok else f'{lo}~{hi} 밖'})", {"rsi": round(rsi_val, 1)}

    if key == "price_above_ma":
        period = params.get("period", 20) if isinstance(params, dict) else 20
        ma = moving_average(closes, period)[-1]
        if ma is None:
            return False, f"MA{period} 계산 불가", {}
        price = closes[-1]
        ok = price > ma
        return ok, f"현재가 {'>' if ok else '<'} MA{period}", {"price": round(price, 1), f"ma{period}": round(ma, 1)}

    if key == "change_pct_between":
        if change_pct is None:
            return True, "", {}  # 등락률 정보 없으면 통과 (조건 무시)
        lo, hi = (params + [-100, 100])[:2] if isinstance(params, list) else (-100, 100)
        ok = lo <= change_pct <= hi
        return ok, f"등락률 {change_pct:+.1f}% ({'OK' if ok else f'{lo}~{hi} 밖'})", {"change_pct": round(change_pct, 2)}

    # 알 수 없는 조건 → 무시 (통과)
    return True, f"(미지원 조건 {key} 무시)", {}


def evaluate_strategy(
    strategy_name: str,
    opinion: str,
    conditions: dict[str, Any],
    candles: list[Candle],
    change_pct: float | None = None,
) -> ScreenMatch:
    """전략의 모든 조건을 AND로 평가."""
    reasons: list[str] = []
    failed: list[str] = []
    metrics: dict[str, float] = {}
    all_pass = True

    for key, params in conditions.items():
        ok, desc, m = _check_condition(key, params, candles, change_pct)
        metrics.update(m)
        if ok:
            if desc:
                reasons.append(desc)
        else:
            all_pass = False
            failed.append(desc or key)

    return ScreenMatch(
        matched=all_pass,
        strategy_name=strategy_name,
        opinion=opinion if all_pass else "",
        reasons=reasons,
        metrics=metrics,
        failed=failed,
    )


def screen_stock(
    strategies: list[Any],   # list[Strategy]
    candles: list[Candle],
    change_pct: float | None = None,
) -> list[ScreenMatch]:
    """한 종목을 모든 활성 전략에 대해 평가. 매칭된 전략만 반환."""
    matches: list[ScreenMatch] = []
    for s in strategies:
        result = evaluate_strategy(s.name, s.opinion, s.conditions, candles, change_pct)
        if result.matched:
            matches.append(result)
    return matches
