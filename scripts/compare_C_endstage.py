"""C 끝물 필터 ON/OFF 비교 — 6종목, 가짜 거르고 진짜 보존하는지.

실행: python scripts/compare_C_endstage.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from scripts.scan_C import scan

CASES = [
    ("277810", "레인보우①(끝물섞임)", "20241201", "20250401"),
    ("277810", "레인보우②(진짜)", "20250901", "20260201"),
    ("347850", "디엔디파마텍(진짜)", "20250401", "20260530"),
    ("066570", "LG전자(가짜섞임)", "20250901", "20260420"),
    ("373220", "LG에너지솔루션(가짜)", "20250701", "20251201"),
    ("005380", "현대차(진짜)", "20251001", "20260530"),
]


def _stat(trades):
    if not trades:
        return "진입0"
    rets = [t[4] for t in trades]
    wins = [r for r in rets if r > 0]
    return f"{len(trades)}회 {len(wins)/len(trades)*100:.0f}% {sum(rets)/len(rets):+.1f}%"


async def main():
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    print("C 끝물필터 ON/OFF 비교 (120일상승 10~80% + 60선이격≤35%)")
    print("=" * 76)
    print(f"{'종목':<22}{'필터OFF':<22}{'필터ON':<22}")
    print("-" * 76)
    agg_off, agg_on = [], []
    for tk, name, st, en in CASES:
        candles = await a.get_ohlcv(tk, days=500, end_date=en)
        if len(candles) < 130:
            print(f"{name:<22}데이터부족")
            continue
        t_off = scan(candles, st, en, endstage_filter=False)
        t_on = scan(candles, st, en, endstage_filter=True)
        agg_off.extend(t_off)
        agg_on.extend(t_on)
        print(f"{name:<22}{_stat(t_off):<22}{_stat(t_on):<22}")
    print("-" * 76)
    print(f"{'전체합산':<22}{_stat(agg_off):<22}{_stat(agg_on):<22}")


if __name__ == "__main__":
    asyncio.run(main())
