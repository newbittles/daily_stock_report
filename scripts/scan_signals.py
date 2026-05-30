"""종목+기간 매수신호 검증 — 지정 구간에서 B 전략(20일선 눌림목) 신호일 탐색.

사용법:
  python scripts/scan_signals.py <종목코드> <시작일> <종료일>
  예: python scripts/scan_signals.py 006800 20260201 20260331

각 거래일에서 그 시점까지의 데이터로 B 신호가 떴는지 하나씩 판정.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_ma20_pullback


async def main() -> None:
    if len(sys.argv) < 4:
        print("사용법: python scripts/scan_signals.py <종목코드> <시작일YYYYMMDD> <종료일YYYYMMDD>")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    # 종료일 기준 충분한 데이터 확보 (구간 + 20일선 워밍업 60봉)
    candles_all = await adapter.get_ohlcv(ticker, days=200, end_date=end)
    if len(candles_all) < 60:
        print(f"데이터 부족 ({len(candles_all)}봉)")
        return

    print(f"종목 {ticker} · 구간 {start}~{end} · B 전략(20일선 눌림목) 신호 스캔")
    print("=" * 78)
    print(f"{'날짜':<10}{'신호':<8}{'종가':>10}{'20선이격':>9}{'급등율':>8}  근거/사유")
    print("-" * 78)

    def _find_surge_day(window, surge_lookback=10):
        """급등 고점일 = 최근 surge_lookback일 내 최고가 기록일 (가격 급등 정점)."""
        seg = window[-surge_lookback:]
        if not seg:
            return None, 0.0
        hi_candle = max(seg, key=lambda c: c.high)
        lo = min(c.low for c in seg)
        rate = (hi_candle.high - lo) / lo * 100 if lo > 0 else 0.0
        return hi_candle.date, rate

    signal_days = []
    for i in range(len(candles_all)):
        c = candles_all[i]
        if c.date < start or c.date > end:
            continue
        window = candles_all[: i + 1]
        if len(window) < 60:
            print(f"{c.date:<10}{'데이터부족':<8}")
            continue
        r = is_ma20_pullback(window)
        mark = "🟢 신호" if r.matched else "─"
        gap = r.metrics.get("ma20_gap_pct", 0)
        surge = r.metrics.get("surge_pct", 0)
        if r.matched:
            sd, sr = _find_surge_day(window)
            surge_info = f"  [급등고점 {sd} +{sr:.0f}%]" if sd else ""
            print(f"{c.date:<10}{mark:<8}{c.close:>10,.0f}{gap:>+8.1f}%{surge:>+7.0f}%{surge_info}")
            signal_days.append((c.date, c.close, r, sd, sr))
        else:
            print(f"{c.date:<10}{mark:<8}{c.close:>10,.0f}{gap:>+8.1f}%{surge:>+7.0f}%  {r.reason}")

    print("=" * 78)
    if not signal_days:
        print("해당 구간 매수신호 없음")
        return

    # 매수 → 손절(20일선 이탈) 추적: 연속 신호는 첫 진입만 1건으로 묶음
    from src.indicators.core import moving_average
    closes_all = [c.close for c in candles_all]
    ma20_all = moving_average(closes_all, 20)
    date_to_idx = {c.date: i for i, c in enumerate(candles_all)}

    print(f"🟢 매수신호 {len(signal_days)}일 → 매수/손절 추적 (손절=2일연속 종가 20일선 이탈)")
    print("-" * 78)

    trades = []
    last_exit_idx = -1
    for d, px, r, sd, sr in signal_days:
        bi = date_to_idx[d]
        if bi <= last_exit_idx:
            continue  # 이전 매수의 보유 구간 → 신규 진입 스킵
        # 매수 후 손절: 2일 연속 종가 20일선 이탈 (일시적 1일 이탈은 무시)
        exit_idx, exit_px, exit_date = None, None, None
        for j in range(bi + 1, len(candles_all)):
            m, m_prev = ma20_all[j], ma20_all[j - 1]
            below_today = m is not None and candles_all[j].close < m
            below_prev = m_prev is not None and candles_all[j - 1].close < m_prev
            if below_today and below_prev:  # 2일 연속 이탈 → 손절
                exit_idx, exit_px, exit_date = j, candles_all[j].close, candles_all[j].date
                break
        if exit_idx is not None:
            ret = (exit_px - px) / px * 100
            hold = exit_idx - bi
            trades.append((d, px, exit_date, exit_px, ret, hold, sd, sr))
            last_exit_idx = exit_idx
        else:
            cur = candles_all[-1]
            ret = (cur.close - px) / px * 100
            trades.append((d, px, "보유중", cur.close, ret, len(candles_all) - 1 - bi, sd, sr))
            last_exit_idx = len(candles_all)

    print(f"{'급등고점일':<11}{'매수일':<10}{'매수가':>10}{'손절일':<12}{'청산가':>10}{'수익률':>8}{'보유':>5}")
    print("-" * 78)
    for bd, bpx, ed, epx, ret, hold, sd, sr in trades:
        surge = f"{sd}(+{sr:.0f}%)" if sd else "-"
        print(f"{surge:<11}{bd:<10}{bpx:>10,.0f}{ed:<12}{epx:>10,.0f}{ret:>+7.1f}%{hold:>5}")

    wins = [t for t in trades if t[4] > 0]
    print("-" * 78)
    print(f"진입 {len(trades)}회 | 승률 {len(wins)/len(trades)*100:.0f}% | "
          f"평균 {sum(t[4] for t in trades)/len(trades):+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
