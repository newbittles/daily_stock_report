"""C 전략 손절 기준 비교 — 어느 손절선이 추세 추종에 최적인가.

진입: 정배열(5>10>20>60) + 신고가 근처 (클러스터 첫날).
손절 5종 비교:
  S20   : 20일선 2일연속 이탈 (현재)
  A식   : MACD 시그널아래 + 20선 이탈 (or 일목 구름하단 이탈)
  S60   : 60일선 2일연속 이탈
  S120  : 120일선 2일연속 이탈
  일목  : 일목 구름 하단 이탈

사용법: python scripts/compare_C_stop.py <종목코드> <시작> <종료>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import ichimoku, macd, moving_average


def _entries(candles, start, end, nh_lookback=60, nh_tol=0.03):
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
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
        sig.append(i)
    # 클러스터 첫날
    out, prev = [], -100
    for i in sig:
        if i - prev > 3:
            out.append(i)
        prev = i
    return out


def _exit(candles, bi, mode, ma_series, macd_line, macd_sig, span_a, span_b):
    """진입 bi 이후 mode별 청산 인덱스·가격 반환."""
    for j in range(bi + 1, len(candles)):
        cl = candles[j].close
        if mode in ("S20", "S60", "S120"):
            ma = ma_series[j]
            mp = ma_series[j - 1]
            if ma is not None and mp is not None and cl < ma and candles[j - 1].close < mp:
                return (candles[j + 1].open, candles[j + 1].date) if j + 1 < len(candles) else (cl, candles[j].date)
        elif mode == "A식":
            ma = ma_series[j]  # 20일선
            weak = macd_line[j] is not None and macd_sig[j] is not None and macd_line[j] < macd_sig[j]
            if ma is not None and cl < ma and weak:
                return (candles[j + 1].open, candles[j + 1].date) if j + 1 < len(candles) else (cl, candles[j].date)
        elif mode == "일목":
            sa, sb = span_a[j], span_b[j]
            if sa is not None and sb is not None and cl < min(sa, sb):
                return (cl, candles[j].date)
    return (candles[-1].close, "보유중")


def _stats(rets):
    if not rets:
        return (0, 0.0, 0.0, 0.0)
    wins = [r for r in rets if r > 0]
    return (len(rets), len(wins) / len(rets) * 100, sum(rets) / len(rets), min(rets))


async def main():
    if len(sys.argv) < 4:
        print("사용법: python scripts/compare_C_stop.py <종목코드> <시작> <종료>")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    candles = await a.get_ohlcv(ticker, days=500, end_date=end)
    if len(candles) < 130:
        print(f"데이터 부족 ({len(candles)}봉)")
        return

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    ml, ms, _ = macd(closes)
    cloud = ichimoku(highs, lows, closes)
    sa, sb = cloud["senkou_a"], cloud["senkou_b"]

    entries = _entries(candles, start, end)
    print(f"C 손절기준 비교 — {ticker} {start}~{end} (진입 {len(entries)}회)")
    print("=" * 60)
    print(f"{'손절기준':<8}{'진입':>5}{'승률':>7}{'평균':>9}{'최악':>8}")
    print("-" * 60)
    modes = {"S20": ma20, "A식": ma20, "S60": ma60, "S120": ma120, "일목": None}
    for mode, ma_series in modes.items():
        rets = []
        for bi in entries:
            ex_px, _ = _exit(candles, bi, mode, ma_series, ml, ms, sa, sb)
            rets.append((ex_px - candles[bi].close) / candles[bi].close * 100)
        n, w, avg, mn = _stats(rets)
        print(f"{mode:<8}{n:>5}{w:>6.0f}%{avg:>+8.1f}%{mn:>+7.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
