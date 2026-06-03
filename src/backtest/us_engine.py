"""미국 종목 백테스트 엔진 — 순수 (candles + 진입함수 + 청산함수 → 거래·성과).

한국 backtest_A.py(복합청산)·backtest_C.py(MA 2일이탈)를 candles 기반으로 일반화.
진입은 patterns 순수함수(A/B/C/D)를 entry_fn 으로, 청산은 전략별 exit factory를 주입:
  - A: 일목구름 하향이탈 / MACD<sig+종가<20선 / 20선2일이탈 (backtest_A 로직)
  - B: 20선 2일연속 이탈
  - C·D: 60선 2일연속 이탈
거래비용(왕복 cost_pct%)을 수익률에서 차감해 현실화.

외부 의존 없음(값 in → 값 out) → 결정론 단위 테스트. (CLAUDE.md §4)
design: docs/02-design/features/us-screening.design.md §7 P3 / §12 고도화
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.datasource.base import Candle
from src.indicators.core import ichimoku, macd, moving_average

EntryFn = Callable[[list[Candle]], bool]
ExitFn = Callable[[int], "tuple[float, str] | None"]  # j → (exit_px, reason) or None
ExitFactory = Callable[[list[Candle]], ExitFn]


@dataclass(frozen=True)
class Trade:
    entry_date: str
    entry_px: float
    exit_date: str
    exit_px: float
    ret_pct: float
    hold_days: int
    exit_reason: str


# ─── 청산 factory ────────────────────────────────────────────────────────
def make_ma_stop_exit(ma_period: int) -> ExitFactory:
    """MA 2일연속 종가 이탈 → 익일 시가 청산 (B=20, C·D=60)."""
    def factory(candles: list[Candle]) -> ExitFn:
        closes = [c.close for c in candles]
        ma = moving_average(closes, ma_period)
        n = len(candles)

        def fn(j: int) -> tuple[float, str] | None:
            if (ma[j] is not None and ma[j - 1] is not None
                    and candles[j].close < ma[j] and candles[j - 1].close < ma[j - 1]):
                px = candles[j + 1].open if j + 1 < n else candles[j].close
                return px, f"MA{ma_period} 2일이탈"
            return None
        return fn
    return factory


def make_a_exit() -> ExitFactory:
    """A 전략 복합청산 (backtest_A.py): 구름이탈 / MACD약화+20선 / 20선2일."""
    def factory(candles: list[Candle]) -> ExitFn:
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        ma20 = moving_average(closes, 20)
        ml, ms, _ = macd(closes)
        cl = ichimoku(highs, lows, closes)
        sa, sb = cl["senkou_a"], cl["senkou_b"]
        n = len(candles)

        def fn(j: int) -> tuple[float, str] | None:
            # 1. 일목 구름 하향이탈 (당일 종가)
            if sa[j] is not None and sb[j] is not None and candles[j].close < min(sa[j], sb[j]):
                return candles[j].close, "구름이탈"
            below20 = ma20[j] is not None and candles[j].close < ma20[j]
            # 2. MACD 시그널 아래 + 종가 20선 이탈 → 익일 시가
            weak = ml[j] is not None and ms[j] is not None and ml[j] < ms[j]
            if below20 and weak:
                px = candles[j + 1].open if j + 1 < n else candles[j].close
                return px, "MACD약화+20선"
            # 3. 20선 2일연속 이탈 → 익일 시가
            if below20 and ma20[j - 1] is not None and candles[j - 1].close < ma20[j - 1]:
                px = candles[j + 1].open if j + 1 < n else candles[j].close
                return px, "20선2일이탈"
            return None
        return fn
    return factory


# ─── 백테스트 ────────────────────────────────────────────────────────────
def backtest_with_exit(
    candles: list[Candle],
    entry_fn: EntryFn,
    exit_factory: ExitFactory,
    start: str | None = None,
    end: str | None = None,
    warmup: int = 130,
    min_gap_days: int = 3,
    cost_pct: float = 0.1,
) -> list[Trade]:
    """진입 시그널마다 매수 → exit_factory 청산. 수익률에서 왕복 cost_pct% 차감.

    entry_fn(candles[:i+1]) → bool, exit_factory(candles) → (j → (px, reason)|None).
    min_gap_days: 직전 진입과 이 일수 이내면 스킵(연속 시그널 클러스터 첫날만).
    """
    exit_fn = exit_factory(candles)
    trades: list[Trade] = []
    prev = -(10**9)
    for i in range(warmup, len(candles)):
        d = candles[i].date
        if (start and d < start) or (end and d > end):
            continue
        if not entry_fn(candles[: i + 1]):
            continue
        if i - prev <= min_gap_days:
            prev = i
            continue
        prev = i

        buy = candles[i].close
        exit_px = exit_idx = None
        reason = ""
        for j in range(i + 1, len(candles)):
            r = exit_fn(j)
            if r is not None:
                exit_px, reason = r
                exit_idx = j
                break
        if exit_px is None:
            exit_px, exit_idx, reason = candles[-1].close, len(candles) - 1, "미청산"

        ret = ((exit_px - buy) / buy * 100 - cost_pct) if buy else 0.0
        trades.append(Trade(
            entry_date=candles[i].date, entry_px=round(buy, 2),
            exit_date=candles[exit_idx].date, exit_px=round(exit_px, 2),
            ret_pct=round(ret, 2), hold_days=exit_idx - i, exit_reason=reason,
        ))
    return trades


def backtest_ma_stop(
    candles: list[Candle],
    entry_fn: EntryFn,
    ma_stop: int = 20,
    start: str | None = None,
    end: str | None = None,
    warmup: int = 130,
    min_gap_days: int = 3,
    cost_pct: float = 0.0,
) -> list[Trade]:
    """MA stop 청산 백테스트 (하위호환 wrapper). 기본 cost_pct=0.0."""
    return backtest_with_exit(
        candles, entry_fn, make_ma_stop_exit(ma_stop),
        start=start, end=end, warmup=warmup, min_gap_days=min_gap_days, cost_pct=cost_pct,
    )


def summarize(trades: list[Trade]) -> dict[str, float]:
    """거래 리스트 → 성과 요약 (진입수·승률·평균/중앙수익·최악·최고·평균보유)."""
    if not trades:
        return {"n": 0, "win_pct": 0.0, "avg_ret": 0.0, "median_ret": 0.0,
                "worst": 0.0, "best": 0.0, "avg_hold": 0.0}
    rets = sorted(t.ret_pct for t in trades)
    wins = [r for r in rets if r > 0]
    mid = len(rets) // 2
    median = rets[mid] if len(rets) % 2 else (rets[mid - 1] + rets[mid]) / 2
    return {
        "n": len(trades),
        "win_pct": round(len(wins) / len(trades) * 100, 1),
        "avg_ret": round(sum(rets) / len(rets), 2),
        "median_ret": round(median, 2),
        "worst": round(rets[0], 2),
        "best": round(rets[-1], 2),
        "avg_hold": round(sum(t.hold_days for t in trades) / len(trades), 1),
    }
