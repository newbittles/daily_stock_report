"""C 전략 검증 — 대세 정배열 신고가주, 구간 내 임의 진입 + 손절선별 성과.

핵심 질문: 이미 대세상승(정배열+신고가) 중인 종목을 늦게 진입해도,
어느 손절선(5/10/20일선)으로 홀딩하면 안 흔들리고 수익 나는가?

방식: 구간 내 정배열 유지일을 매일 진입 → 손절선 종가 2일연속 이탈 시 청산(익일 시가).
손절선 3종(5/10/20) 각각 승률·평균수익·평균보유일 비교.

사용법: python scripts/backtest_C.py <종목코드> <시작> <종료>
  예: python scripts/backtest_C.py 009150 20250730 20260530
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average


def _run(candles, start, end, ma_stop, require_new_high_entry=False, nh_lookback=60):
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma_s = moving_average(closes, ma_stop)

    trades = []
    for i in range(120, len(candles)):
        c = candles[i]
        if c.date < start or c.date > end:
            continue
        # 진입 조건: 대세 정배열 (5>10>20>60) 유지 중
        if None in (ma5[i], ma10[i], ma20[i], ma60[i]):
            continue
        if not (ma5[i] > ma10[i] > ma20[i] > ma60[i]):
            continue
        # (옵션) 신고가 돌파 후 진입만
        if require_new_high_entry:
            hi = max(highs[max(0, i - nh_lookback):i + 1])
            if c.close < hi * 0.97:
                continue

        buy = c.close
        exit_px = exit_idx = None
        for j in range(i + 1, len(candles)):
            m, mp = ma_s[j], ma_s[j - 1]
            if m is not None and mp is not None and \
               candles[j].close < m and candles[j - 1].close < mp:
                # 2일연속 손절선 이탈 → 익일 시가 청산
                if j + 1 < len(candles):
                    exit_px, exit_idx = candles[j + 1].open, j + 1
                else:
                    exit_px, exit_idx = candles[j].close, j
                break
        if exit_px is None:
            exit_px, exit_idx = candles[-1].close, len(candles) - 1
        ret = (exit_px - buy) / buy * 100
        trades.append((c.date, buy, ret, exit_idx - i))
    return trades


def _stats(trades):
    if not trades:
        return (0, 0.0, 0.0, 0.0, 0.0)
    rets = [t[2] for t in trades]
    wins = [r for r in rets if r > 0]
    holds = [t[3] for t in trades]
    return (len(trades), len(wins) / len(trades) * 100,
            sum(rets) / len(rets), min(rets), sum(holds) / len(holds))


async def main():
    if len(sys.argv) < 4:
        print("사용법: python scripts/backtest_C.py <종목코드> <시작> <종료>")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    candles = await a.get_ohlcv(ticker, days=400, end_date=end)
    if len(candles) < 130:
        print(f"데이터 부족 ({len(candles)}봉)")
        return

    print(f"C 전략 — {ticker} {start}~{end}")
    print(f"대세 정배열(5>10>20>60) 구간 매일 진입 → 손절선 이탈 시 청산")
    print("=" * 72)
    print(f"{'손절선':<10}{'진입수':>7}{'승률':>7}{'평균수익':>9}{'최악':>8}{'평균보유':>9}")
    print("-" * 72)
    for ma_stop in (5, 10, 20):
        trades = _run(candles, start, end, ma_stop)
        n, w, avg, mn, hold = _stats(trades)
        if n == 0:
            print(f"{ma_stop}일선     진입 없음 (정배열 구간 없음)")
            continue
        print(f"{ma_stop}일선{'':<6}{n:>7}{w:>6.0f}%{avg:>+8.1f}%{mn:>+7.1f}%{hold:>8.1f}일")

    print("\n[신고가 돌파 후 진입만]")
    for ma_stop in (5, 10, 20):
        trades = _run(candles, start, end, ma_stop, require_new_high_entry=True)
        n, w, avg, mn, hold = _stats(trades)
        if n == 0:
            print(f"{ma_stop}일선     진입 없음")
            continue
        print(f"{ma_stop}일선{'':<6}{n:>7}{w:>6.0f}%{avg:>+8.1f}%{mn:>+7.1f}%{hold:>8.1f}일")


if __name__ == "__main__":
    asyncio.run(main())
