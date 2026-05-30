"""A 전략 3버전 역검증 — 사용자 8개 사례가 매수일 부근에 포착되는지.

A1: 5>10>20 정배열 (장기 완화)
A2: 5>10>20 정배열 + 60>120
A3: 수렴 + 종가가 이평선 위 상승전환 (장기 완화) — 8/8 목표

실행: python scripts/verify_A.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_convergence_breakout

CASES = [
    ("001740", "SK네트웍스", "20260424"),
    ("011070", "LG이노텍", "20260318"),
    ("018260", "삼성에스디에스", "20260521"),
    ("000660", "SK하이닉스", "20250901"),
    ("009150", "삼성전기", "20250728"),
    ("066570", "LG전자", "20260413"),
    ("005380", "현대차", "20251015"),
    ("047040", "대우건설", "20260115"),
    ("353200", "대덕전자(1월)", "20260128"),
    ("012330", "현대모비스", "20260507"),
    ("307950", "현대오토에버", "20260507"),
    ("319400", "현대무벡스", "20260529"),
    ("018880", "한온시스템", "20260415"),
    ("402340", "SK스퀘어", "20260413"),
    ("000720", "현대건설", "20260106"),
]

# 매수일 ±5거래일(해당 주 전후) 내 신호 뜨면 포착으로 인정
WINDOW = 5


def _check_near(candles, date, **kwargs):
    """매수일 ±WINDOW 거래일 내 A 신호 발생 여부."""
    idx = -1
    for i, c in enumerate(candles):
        if c.date <= date:
            idx = i
    if idx < 130:
        return None, "데이터 부족"
    for j in range(max(130, idx - WINDOW), min(len(candles), idx + WINDOW + 1)):
        r = is_convergence_breakout(candles[: j + 1], **kwargs)
        if r.matched:
            return candles[j].date, r.reason
    # 미포착 시 매수일 당일 사유
    return None, is_convergence_breakout(candles[: idx + 1], **kwargs).reason


async def main():
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    versions = {
        "A1 (5>10>20)": dict(strict_align=True, require_long_align=False),
        "A2 (+60>120)": dict(strict_align=True, require_long_align=True),
        "A3 (수렴+상승전환)": dict(strict_align=False, require_long_align=False),
    }

    print("A 전략 3버전 역검증 (매수일 ±3거래일 내 신호 = 포착)")
    print("=" * 80)

    results = {v: 0 for v in versions}
    detail = {v: [] for v in versions}
    for ticker, name, date in CASES:
        candles = await adapter.get_ohlcv(ticker, days=200, end_date=date)
        if len(candles) < 130:
            print(f"{name}: 데이터 부족")
            continue
        line = f"{name:<12} {date}"
        for vname, kw in versions.items():
            hit, reason = _check_near(candles, date, **kw)
            mark = "✅" if hit else "❌"
            if hit:
                results[vname] += 1
                detail[vname].append(f"{name}({hit})")
            line += f"  {vname[:4]}{mark}"
        print(line)

    n = len(CASES)
    print("\n" + "=" * 80)
    print("버전별 포착률:")
    for vname in versions:
        print(f"  {vname:<20}: {results[vname]}/{n} ({results[vname]/n*100:.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
