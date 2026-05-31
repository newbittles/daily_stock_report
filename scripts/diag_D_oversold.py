"""D 전략(주도주 과매도 반등) 일별 진단.

사용법: python scripts/diag_D_oversold.py <종목코드> <시작> <종료>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average, rsi
from src.patterns.core import is_leader_oversold_bounce


async def main():
    if len(sys.argv) < 4:
        print("사용법: python scripts/diag_D_oversold.py <코드> <시작> <종료>")
        return
    tk, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    c = await a.get_ohlcv(tk, days=400, end_date=end)
    closes = [x.close for x in c]
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    rsi_v = rsi(closes, 14)

    print(f"D 진단 — {tk} {start}~{end} (정배열이력+120선위+RSI과매도반등+60선권)")
    print("=" * 96)
    print(f"{'일자':<10}{'시가':>9}{'종가':>9}{'캔들':>5}{'60선이격':>9}{'120선이격':>10}{'RSI':>6}  판정")
    print("-" * 96)
    for i in range(len(c)):
        d = c[i].date
        if d < start or d > end or None in (ma60[i], ma120[i], rsi_v[i]):
            continue
        px, op = closes[i], c[i].open
        g60 = (px - ma60[i]) / ma60[i] * 100
        g120 = (px - ma120[i]) / ma120[i] * 100
        candle = "양" if px > op else "음"
        r = is_leader_oversold_bounce(c[: i + 1])
        tag = "🟢D신호 " + r.reason if r.matched else ("  " + r.reason[:48])
        print(f"{d:<10}{op:>9,.0f}{px:>9,.0f}{candle:>5}{g60:>+8.0f}%{g120:>+9.0f}%{rsi_v[i]:>6.0f}  {tag}")


if __name__ == "__main__":
    asyncio.run(main())
