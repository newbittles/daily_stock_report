"""미국 A/B/C/D 백테스트 (P3 고도화) — 정확청산·거래비용·장기간·생존편향 완화.

고도화(P3 한계 개선):
  - 유니버스: nasdaq hot(거래대금 급등주) → S&P500 대형주 (급등주 편향 완화)
  - 기간: 300일 → 750일(약 3년, 조정·하락 구간 포함)
  - 청산: MA 2일이탈 단순화 → 전략별 정확청산 (A 복합: 구름/MACD/20선)
  - 거래비용: 왕복 cost_pct% 차감
  - 생존편향: S&P500 '현재 구성'이라 잔존(명시적 한계) — nasdaq 급등주보단 완화

사용법: python scripts/backtest_us.py [종목수=120] [days=750] [cost_pct=0.1]
design: docs/02-design/features/us-screening.design.md §12
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.us_engine import (  # noqa: E402
    backtest_with_exit,
    make_a_exit,
    make_ma_stop_exit,
    summarize,
)
from src.datasource.us.fdr_source import fetch_us_ohlcv_batch  # noqa: E402
from src.datasource.us.universe import get_sp500_universe  # noqa: E402
from src.patterns.core import (  # noqa: E402
    is_convergence_breakout,
    is_downtrend_reversal,
    is_ma20_pullback,
    is_trend_follow,
)

# (entry_fn, exit_factory) — screener_us.yaml 파라미터 + 전략별 실제 청산
STRATEGIES = {
    "A": (lambda cs: is_convergence_breakout(
        cs, conv_max=6.0, gap120_min=2.0, strict_align=False,
        require_new_high=False, require_ma120_rising=False,
        reject_macd_falling=True, enable_vol_breakout=False).matched, make_a_exit()),
    "B": (lambda cs: is_ma20_pullback(
        cs, ma_period=20, surge_lookback=10, surge_pct=15.0, max_gap=0.45,
        require_below_ma5=True, min_pullback_pct=2.0, require_ma20_rising=True,
        require_new_high=True, new_high_lookback=60).matched, make_ma_stop_exit(20)),
    "C": (lambda cs: is_trend_follow(
        cs, nh_lookback=60, nh_tol=0.03, div_lookback=40, div_rsi_margin=5.0,
        rollover_peak_min=50.0, rollover_ratio=0.55).matched, make_ma_stop_exit(60)),
    "D": (lambda cs: is_downtrend_reversal(cs, downtrend_lookback=20).matched, make_ma_stop_exit(60)),
}


def _fmt(name: str, s: dict) -> str:
    if s["n"] == 0:
        return f"  {name:<7} 진입 없음"
    return (f"  {name:<7} 진입 {s['n']:>4} | 승률 {s['win_pct']:>5.1f}% | "
            f"평균 {s['avg_ret']:>+6.2f}% | 중앙 {s['median_ret']:>+6.2f}% | "
            f"최악 {s['worst']:>+7.2f}% | 보유 {s['avg_hold']:>4.1f}일")


async def main() -> None:
    n_sym = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 750
    cost = float(sys.argv[3]) if len(sys.argv) > 3 else 0.1

    uni = get_sp500_universe()[:n_sym]
    syms = [u.symbol for u in uni]
    print(f"백테스트: S&P500 상위 {len(syms)}종목 × {days}일, 거래비용 왕복 {cost}% 차감")
    print("(생존편향: S&P500 현재 구성 — 급등주 편향은 완화, 현재 생존종목 편향은 잔존)")
    data = await fetch_us_ohlcv_batch(syms, days=days)
    usable = {s: cs for s, cs in data.items() if len(cs) >= 200}
    print(f"수집 완료: {len(usable)}종목 사용 (200봉+)\n")

    print("=== A/B/C/D 전략 성과 (전략별 정확청산 + 거래비용) ===")
    for name, (fn, exit_factory) in STRATEGIES.items():
        trades = []
        for cs in usable.values():
            trades += backtest_with_exit(cs, fn, exit_factory, min_gap_days=5, cost_pct=cost)
        print(_fmt(name, summarize(trades)))

    print("\n=== D downtrend_lookback 스윕 (60선 청산) ===")
    for lb in (20, 10, 5, 3):
        trades = []
        for cs in usable.values():
            trades += backtest_with_exit(
                cs, lambda c, lb=lb: is_downtrend_reversal(c, downtrend_lookback=lb).matched,
                make_ma_stop_exit(60), min_gap_days=5, cost_pct=cost)
        print(_fmt(f"lb={lb}", summarize(trades)))


if __name__ == "__main__":
    asyncio.run(main())
