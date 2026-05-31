"""C 전략 손절→재진입 진단. 60선 이탈 후 언제 다시 진입신호가 뜨는가?

사용법: python scripts/diag_C_reentry.py <종목코드> <시작> <종료>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average, rsi
from src.patterns.core import is_trend_follow


async def main():
    if len(sys.argv) < 4:
        print("사용법: python scripts/diag_C_reentry.py <코드> <시작> <종료>")
        return
    tk, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    c = await a.get_ohlcv(tk, days=400, end_date=end)
    closes = [x.close for x in c]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    rsi_v = rsi(closes, 14)

    print(f"C 진단 — {tk} {start}~{end}  (정배열+신고가=진입 / 60선2일이탈=손절)")
    print("=" * 92)
    print(f"{'일자':<10}{'종가':>10}{'20선이격':>9}{'60선이격':>9}{'RSI':>6}  {'상태':<10}판정")
    print("-" * 92)

    below60_streak = 0
    held = False  # 진입 상태
    for i in range(len(c)):
        d = c[i].date
        if d < start or d > end or ma60[i] is None:
            continue
        px = closes[i]
        g20 = (px - ma20[i]) / ma20[i] * 100 if ma20[i] else 0
        g60 = (px - ma60[i]) / ma60[i] * 100
        r = is_trend_follow(c[: i + 1])
        rv = rsi_v[i] or 0

        below60_streak = below60_streak + 1 if px < ma60[i] else 0
        tag = ""
        if held and below60_streak >= 2:
            tag = "🔴손절(60선2일이탈)"
            held = False
        elif not held and r.matched:
            tag = "🟢진입(정배열+신고가)"
            held = True
        elif r.matched:
            tag = "  · 신호유지"
        elif px < ma60[i]:
            tag = "  60선아래"

        warn = " ⚠️끝물" if (r.matched and r.metrics.get("endstage")) else ""
        state = "보유중" if held else "관망"
        print(f"{d:<10}{px:>10,.0f}{g20:>+8.0f}%{g60:>+8.0f}%{rv:>6.0f}  {state:<10}{tag}{warn}")


if __name__ == "__main__":
    asyncio.run(main())
