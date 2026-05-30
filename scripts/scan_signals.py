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
    print(f"{'날짜':<10}{'신호':<8}{'종가':>10}{'20선이격':>9}{'거래량급증':>10}  근거/사유")
    print("-" * 78)

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
        vol = r.metrics.get("max_vol_ratio", 0)
        print(f"{c.date:<10}{mark:<8}{c.close:>10,.0f}{gap:>+8.1f}%{vol:>9.1f}배  {r.reason}")
        if r.matched:
            signal_days.append((c.date, c.close, r))

    print("=" * 78)
    if signal_days:
        print(f"🟢 매수신호 {len(signal_days)}일:")
        for d, px, r in signal_days:
            print(f"   {d}  {px:,.0f}원  —  {r.reason}")
    else:
        print("해당 구간 매수신호 없음")


if __name__ == "__main__":
    asyncio.run(main())
