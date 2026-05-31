"""D 전략(하락추세 전환) 전 기간 스캔 — 진입·손절·수익률 (A/B/C scan과 동일 포맷).

진입: is_downtrend_reversal 충족 클러스터 첫날.
손절: use_ichimoku=True면 종가 2일연속 일목 구름 하단(26봉 시프트) 이탈, False면 20일선 2일이탈.
청산가: 손절일 익일 시가 (손절 없으면 마지막 종가=보유중). 익절 기준 별도 없음(추세추종).

사용법: python scripts/scan_D.py            (검증 6종목 전체)
        python scripts/scan_D.py <코드> <시작> <종료>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import ichimoku, moving_average
from src.patterns.core import is_downtrend_reversal

CASES = [
    ("373220", "LG에너지솔루션", "20250714", "20260101"),
    ("001740", "SK네트웍스",     "20250529", "20251101"),
    ("086520", "에코프로",       "20250623", "20251101"),
    ("064400", "LG씨엔에스",     "20251204", "20260601"),
    ("066570", "LG전자",        "20250610", "20251101"),
    ("035420", "NAVER",        "20240923", "20250301"),
]
SHIFT = 26


def scan(candles, start, end, use_ichimoku=True, stop_ma=60):
    """진입=D패턴, 손절=stop_ma선(기본 60) 종가 2일연속 이탈."""
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    ma_stop = moving_average(closes, stop_ma)
    cl = ichimoku(highs, lows, closes)
    sa, sb = cl["senkou_a"], cl["senkou_b"]

    # 진입 신호일
    sig = []
    for i in range(90, len(candles)):
        d = candles[i].date
        if d < start or d > end:
            continue
        if is_downtrend_reversal(candles[: i + 1], use_ichimoku=use_ichimoku).matched:
            sig.append(i)

    # 클러스터 첫날만 진입 → 손절(구름하단 or 20선 2일연속 이탈)
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
            broke = (ma_stop[j] is not None and ma_stop[j - 1] is not None
                     and closes[j] < ma_stop[j] and closes[j - 1] < ma_stop[j - 1])
            if broke:
                if j + 1 < len(candles):
                    ex_px, ex_date = candles[j + 1].open, candles[j + 1].date
                else:
                    ex_px, ex_date = closes[j], candles[j].date
                break
        if ex_px is None:
            ex_px, ex_date = closes[-1], "보유중"
        trades.append((candles[i].date, buy, ex_date, ex_px, (ex_px - buy) / buy * 100))
    return trades


async def run(a, tk, nm, start, end):
    c = await a.get_ohlcv(tk, days=400, end_date=end)
    if len(c) < 100:
        print(f"\n### {nm}({tk}) 데이터 부족"); return
    trades = scan(c, start, end)
    print(f"\n### {nm}({tk})  {start}~{end}  (진입:D패턴 / 손절:60일선 2일이탈)")
    if not trades:
        print("  진입 신호 없음"); return
    print(f"  {'진입일':<10}{'진입가':>11}{'청산일':<11}{'청산가':>11}{'수익률':>8}")
    for bd, bpx, ed, epx, ret in trades:
        print(f"  {bd:<10}{bpx:>11,.0f}{ed:<11}{epx:>11,.0f}{ret:>+7.1f}%")
    wins = [t for t in trades if t[4] > 0]
    print(f"  → 진입 {len(trades)}회 | 승률 {len(wins)/len(trades)*100:.0f}% | 평균 {sum(t[4] for t in trades)/len(trades):+.1f}%")


async def main():
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    if len(sys.argv) >= 4:
        await run(a, sys.argv[1], sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("=" * 70 + "\nD 전략 전 기간 스캔 (지정일 이후 모든 진입 + 손절/수익률)")
        for tk, nm, start, end in CASES:
            await run(a, tk, nm, start, end)


if __name__ == "__main__":
    asyncio.run(main())
