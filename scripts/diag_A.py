"""특정 종목·시점의 A 신호 판정 진단 — 미포착 사유 분석.

사용법: python scripts/diag_A.py <종목코드> <날짜1> [날짜2 ...]
각 날짜 ±5거래일 구간의 A3 판정 + 지표 출력.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average
from src.patterns.core import is_convergence_breakout


async def main():
    if len(sys.argv) < 3:
        print("사용법: python scripts/diag_A.py <종목코드> <날짜1> [날짜2 ...]")
        return
    ticker = sys.argv[1]
    dates = sys.argv[2:]

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    # 가장 이른 날짜 기준 충분한 데이터
    candles = await adapter.get_ohlcv(ticker, days=400, end_date="20260530")
    closes = [c.close for c in candles]
    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)

    print(f"종목 {ticker} — A3 신호 진단")
    print("=" * 100)

    for target in dates:
        print(f"\n📌 {target} 부근 (±5거래일)")
        print(f"{'날짜':<10}{'종가':>9}{'5선':>9}{'20선':>9}{'120선':>9}{'수렴%':>7}{'120이격':>8}  A3판정")
        # 타겟 부근 인덱스
        ti = -1
        for i, c in enumerate(candles):
            if c.date <= target:
                ti = i
        if ti < 130:
            print("  데이터 부족")
            continue
        for j in range(max(130, ti - 5), min(len(candles), ti + 6)):
            c = candles[j]
            m5, m20, m120 = ma5[j], ma20[j], ma120[j]
            if None in (m5, m20, m120):
                continue
            conv = (max(ma5[j], ma10[j], ma20[j]) - min(ma5[j], ma10[j], ma20[j])) / ma20[j] * 100
            gap120 = (c.close - m120) / m120 * 100
            r = is_convergence_breakout(candles[: j + 1], strict_align=False)
            mark = "🟢포착" if r.matched else f"X {r.reason}"
            print(f"{c.date:<10}{c.close:>9,.0f}{m5:>9,.0f}{m20:>9,.0f}{m120:>9,.0f}"
                  f"{conv:>6.1f}%{gap120:>+7.1f}%  {mark}")


if __name__ == "__main__":
    asyncio.run(main())
