"""A 전략 백테스트 — 진입(A3: 수렴+상승전환) + 청산(MACD+20선+일목 3신호).

청산 우선순위:
  1. 일목 구름대 하향 이탈 → 당일 종가 100% 매도 (최종)
  2. MACD 시그널 아래(데드크로스 이후) + 종가 20일선 이탈 → 다음날 시가 매도
  3. (손절) 종가 20일선 2일연속 이탈 → 다음날 시가 매도

사용법: python scripts/backtest_A.py <종목코드> <시작> <종료>
  예: python scripts/backtest_A.py 047040 20260101 20260530
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import ichimoku, macd, moving_average
from src.patterns.core import is_convergence_breakout


def backtest(candles, start, end):
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    ma20 = moving_average(closes, 20)
    macd_line, macd_sig, _ = macd(closes)
    cloud = ichimoku(highs, lows, closes)
    span_a, span_b = cloud["senkou_a"], cloud["senkou_b"]

    # 진입 신호 (A3) 클러스터 첫날
    sig_idx = []
    for i in range(130, len(candles)):
        if candles[i].date < start or candles[i].date > end:
            continue
        r = is_convergence_breakout(candles[: i + 1], strict_align=False, require_long_align=False)
        if r.matched:
            sig_idx.append(i)

    trades = []
    prev = -100
    for bi in sig_idx:
        if (bi - prev) <= 3:
            prev = bi
            continue
        prev = bi
        buy = candles[bi].close
        exit_px = exit_date = exit_reason = None
        for j in range(bi + 1, len(candles)):
            # 1. 일목 구름 하향 이탈 (당일 종가)
            sa, sb = span_a[j], span_b[j]
            if sa is not None and sb is not None and candles[j].close < min(sa, sb):
                exit_px, exit_date, exit_reason = candles[j].close, candles[j].date, "구름이탈"
                break
            # 2. MACD 시그널 아래 + 종가 20선 이탈 → 익일 시가
            below20 = ma20[j] is not None and candles[j].close < ma20[j]
            macd_weak = macd_line[j] is not None and macd_sig[j] is not None and macd_line[j] < macd_sig[j]
            if below20 and macd_weak:
                if j + 1 < len(candles):
                    exit_px, exit_date, exit_reason = candles[j + 1].open, candles[j + 1].date, "MACD약화+20선이탈→익일시가"
                else:
                    exit_px, exit_date, exit_reason = candles[j].close, candles[j].date, "MACD약화+20선이탈(종가)"
                break
            # 3. 손절: 20선 2일연속 이탈 → 익일 시가
            below20_prev = ma20[j - 1] is not None and candles[j - 1].close < ma20[j - 1]
            if below20 and below20_prev:
                if j + 1 < len(candles):
                    exit_px, exit_date, exit_reason = candles[j + 1].open, candles[j + 1].date, "20선2일이탈손절→익일시가"
                else:
                    exit_px, exit_date, exit_reason = candles[j].close, candles[j].date, "20선2일이탈손절"
                break
        if exit_px is None:
            exit_px, exit_date, exit_reason = candles[-1].close, "보유중", "미청산"
        ret = (exit_px - buy) / buy * 100
        trades.append((candles[bi].date, buy, exit_date, exit_px, ret, exit_reason))
    return trades


async def main():
    if len(sys.argv) < 4:
        print("사용법: python scripts/backtest_A.py <종목코드> <시작> <종료>")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    candles = await adapter.get_ohlcv(ticker, days=320, end_date=end)
    if len(candles) < 130:
        print(f"데이터 부족 ({len(candles)}봉)")
        return

    trades = backtest(candles, start, end)
    print(f"A 전략 백테스트 — {ticker} {start}~{end}")
    print("=" * 84)
    if not trades:
        print("진입 신호 없음")
        return
    print(f"{'매수일':<10}{'매수가':>10}{'청산일':<10}{'청산가':>10}{'수익률':>8}  청산사유")
    for bd, bpx, ed, epx, ret, rs in trades:
        print(f"{bd:<10}{bpx:>10,.0f}{ed:<10}{epx:>10,.0f}{ret:>+7.1f}%  {rs}")
    wins = [t for t in trades if t[4] > 0]
    print("-" * 84)
    print(f"진입 {len(trades)}회 | 승률 {len(wins)/len(trades)*100:.0f}% | 평균 {sum(t[4] for t in trades)/len(trades):+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
