"""B 전략(is_ma20_pullback) 역검증 — 사용자 매수 사례가 그날 포착되는지 확인.

실행: python scripts/verify_ma20_pullback.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_ma20_pullback

CASES = [
    ("006800", "미래에셋증권", ["20260206", "20260212", "20260304"]),
    ("009830", "한화솔루션", ["20260212", "20260213"]),
    ("000270", "기아", ["20260506"]),
    ("009150", "삼성전기", ["20260303"]),
    ("272210", "한화시스템", ["20260209"]),
]


def _idx(candles, date):
    best = -1
    for i, c in enumerate(candles):
        if c.date <= date:
            best = i
    return best


async def main():
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print("B 전략 역검증 — 이평선+거래량+캔들 (MACD/일목 제외)")
    print("=" * 70)
    total = 0
    hit = 0
    for ticker, name, dates in CASES:
        for d in dates:
            total += 1
            # 매수일 기준 그 시점까지 80봉 확보 (과거 역검증)
            candles = await adapter.get_ohlcv(ticker, days=80, end_date=d)
            if len(candles) < 60:
                print(f"  {name} {d}: 데이터 부족 ({len(candles)}봉)")
                continue
            r = is_ma20_pullback(candles)
            mark = "✅ 포착" if r.matched else "❌ 미포착"
            if r.matched:
                hit += 1
            print(f"  {mark}  {name} {d}: {r.reason}")
    print("=" * 70)
    print(f"  역검증 결과: {hit}/{total} 포착 ({hit/total*100:.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
