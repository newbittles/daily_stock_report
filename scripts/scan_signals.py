"""종목+기간 매수신호 검증 — B 전략(20일선 눌림목) 신호일 탐색 + 매수/손절 추적.

사용법:
  python scripts/scan_signals.py <종목코드> <시작일> <종료일> [--compare]
  예: python scripts/scan_signals.py 010170 20260201 20260530 --compare

--compare: 눌림깊이 상한 보강 미포함 vs 포함(5일고점 -10% 초과 제외) 비교.
매수일(눌림 신호일) 기준 — 텔레그램 발송일과 동일. 급등일 정보 병기.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average
from src.patterns.core import is_ma20_pullback

MAX_PULLBACK_ON = 13.0   # 보강: 5일고점 대비 -13% 초과 눌림 제외 (깊은 눌림=추세약화)


def _find_surge_day(window, surge_lookback=10):
    seg = window[-surge_lookback:]
    if not seg:
        return None, 0.0
    hi_c = max(seg, key=lambda c: c.high)
    lo = min(c.low for c in seg)
    rate = (hi_c.high - lo) / lo * 100 if lo > 0 else 0.0
    return hi_c.date, rate


def compute_trades(candles_all, start, end, max_pullback_pct=None):
    """신호 스캔 + 클러스터 진입 + 2일연속 20일선 손절 추적. (signal_days, trades) 반환."""
    closes_all = [c.close for c in candles_all]
    ma20_all = moving_average(closes_all, 20)
    date_to_idx = {c.date: i for i, c in enumerate(candles_all)}

    signal_days = []
    for i in range(len(candles_all)):
        c = candles_all[i]
        if c.date < start or c.date > end or len(candles_all[: i + 1]) < 60:
            continue
        r = is_ma20_pullback(candles_all[: i + 1], max_pullback_pct=max_pullback_pct)
        if r.matched:
            sd, sr = _find_surge_day(candles_all[: i + 1])
            signal_days.append((c.date, c.close, r, sd, sr))

    trades = []
    prev_idx = -100
    for d, px, r, sd, sr in signal_days:
        bi = date_to_idx[d]
        if (bi - prev_idx) <= 3:   # 같은 눌림 클러스터 → 첫날만
            prev_idx = bi
            continue
        prev_idx = bi
        exit_px = exit_date = None
        hold = 0
        for j in range(bi + 1, len(candles_all)):
            m, mp = ma20_all[j], ma20_all[j - 1]
            if (m and candles_all[j].close < m) and (mp and candles_all[j - 1].close < mp):
                exit_px, exit_date, hold = candles_all[j].close, candles_all[j].date, j - bi
                break
        if exit_px is None:
            exit_px, exit_date, hold = candles_all[-1].close, "보유중", len(candles_all) - 1 - bi
        ret = (exit_px - px) / px * 100
        trades.append((d, px, exit_date, exit_px, ret, hold, sd, sr))
    return signal_days, trades


def _print_trades(title, trades):
    print(f"\n[{title}]")
    if not trades:
        print("  진입 없음")
        return
    print(f"  {'급등고점일':<12}{'매수일':<10}{'매수가':>10}{'청산일':<10}{'청산가':>10}{'수익률':>8}{'보유':>5}")
    for bd, bpx, ed, epx, ret, hold, sd, sr in trades:
        surge = f"{sd}(+{sr:.0f}%)" if sd else "-"
        print(f"  {surge:<12}{bd:<10}{bpx:>10,.0f}{ed:<10}{epx:>10,.0f}{ret:>+7.1f}%{hold:>5}")
    wins = [t for t in trades if t[4] > 0]
    print(f"  → 진입 {len(trades)}회 | 승률 {len(wins)/len(trades)*100:.0f}% | "
          f"평균 {sum(t[4] for t in trades)/len(trades):+.1f}%")


async def main() -> None:
    if len(sys.argv) < 4:
        print("사용법: python scripts/scan_signals.py <종목코드> <시작일> <종료일> [--compare]")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    compare = "--compare" in sys.argv

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    candles_all = await adapter.get_ohlcv(ticker, days=320, end_date=end)
    if len(candles_all) < 60:
        print(f"데이터 부족 ({len(candles_all)}봉)")
        return

    print(f"종목 {ticker} · 구간 {start}~{end} · B 전략(20일선 눌림목)")
    print("=" * 80)

    if compare:
        _, trades_off = compute_trades(candles_all, start, end, max_pullback_pct=None)
        _print_trades("① 보강 미포함 (눌림깊이 제한 없음)", trades_off)
        _, trades_on = compute_trades(candles_all, start, end, max_pullback_pct=MAX_PULLBACK_ON)
        _print_trades(f"② 보강 포함 (5일고점 -{MAX_PULLBACK_ON:.0f}% 초과 눌림 제외)", trades_on)
    else:
        _, trades = compute_trades(candles_all, start, end)
        _print_trades("결과", trades)


if __name__ == "__main__":
    asyncio.run(main())
