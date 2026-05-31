"""C 전략 신호일·손절일 추적 — 정배열+신고가 진입, 20일선 손절.

진입 클러스터(연속 신호 구간)의 첫날을 진입으로, 20일선 2일연속 이탈을 손절(익일시가)로.
사용법: python scripts/scan_C.py <종목코드> <시작> <종료>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average


def scan(candles, start, end, nh_lookback=60, nh_tol=0.03, stop_ma=60,
         endstage_filter=False, rise120_min=10.0, rise120_max=80.0, gap60_max=35.0):
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma_stop = moving_average(closes, stop_ma)  # 손절선 (기본 60일선)

    # 진입 신호일 (정배열 5>10>20>60 + 종가 신고가 근처)
    sig = []
    for i in range(120, len(candles)):
        c = candles[i]
        if c.date < start or c.date > end:
            continue
        if None in (ma5[i], ma10[i], ma20[i], ma60[i]):
            continue
        if not (ma5[i] > ma10[i] > ma20[i] > ma60[i]):
            continue
        hi = max(highs[max(0, i - nh_lookback):i + 1])
        if c.close < hi * (1 - nh_tol):
            continue
        # 끝물 필터: 120일 상승률 밴드 + 60선 이격 상한 (과열·미약 제외)
        if endstage_filter and i >= 120 and ma60[i]:
            rise120 = (c.close - closes[i - 120]) / closes[i - 120] * 100
            gap60 = (c.close - ma60[i]) / ma60[i] * 100
            if not (rise120_min <= rise120 <= rise120_max and gap60 <= gap60_max):
                continue
        sig.append(i)

    # 클러스터(연속 신호) 첫날만 진입 → 20일선 2일연속 이탈 손절
    trades = []
    prev = -100
    for i in sig:
        if i - prev <= 3:
            prev = i
            continue
        prev = i
        buy = candles[i].close
        ex_px = ex_date = None
        for j in range(i + 1, len(candles)):
            if ma_stop[j] is not None and ma_stop[j - 1] is not None and \
               candles[j].close < ma_stop[j] and candles[j - 1].close < ma_stop[j - 1]:
                if j + 1 < len(candles):
                    ex_px, ex_date = candles[j + 1].open, candles[j + 1].date
                else:
                    ex_px, ex_date = candles[j].close, candles[j].date
                break
        if ex_px is None:
            ex_px, ex_date = candles[-1].close, "보유중"
        ret = (ex_px - buy) / buy * 100
        trades.append((candles[i].date, buy, ex_date, ex_px, ret))
    return trades


async def main():
    if len(sys.argv) < 4:
        print("사용법: python scripts/scan_C.py <종목코드> <시작> <종료>")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    candles = await a.get_ohlcv(ticker, days=400, end_date=end)
    if len(candles) < 130:
        print(f"데이터 부족 ({len(candles)}봉)")
        return

    trades = scan(candles, start, end)
    print(f"C 전략 — {ticker} {start}~{end} (진입:정배열+신고가 / 손절:60선 2일이탈)")
    print("=" * 70)
    if not trades:
        print("진입 신호 없음")
        return
    print(f"{'진입일':<10}{'진입가':>11}{'손절일':<11}{'청산가':>11}{'수익률':>8}")
    print("-" * 70)
    for bd, bpx, ed, epx, ret in trades:
        print(f"{bd:<10}{bpx:>11,.0f}{ed:<11}{epx:>11,.0f}{ret:>+7.1f}%")
    wins = [t for t in trades if t[4] > 0]
    print("-" * 70)
    print(f"진입 {len(trades)}회 | 승률 {len(wins)/len(trades)*100:.0f}% | 평균 {sum(t[4] for t in trades)/len(trades):+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
