"""극단적 급등 상한(max_surge_pct) 보강의 전 종목 승률 영향 비교.

지금까지 검증한 종목들을 보강 미포함 vs 포함으로 돌려 표로 비교.
실행: python scripts/compare_surge_cap.py [상한%]  (기본 130)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from scripts.scan_signals import compute_trades

# (종목코드, 종목명, 시작, 종료) — 그동안 검증한 종목들
CASES = [
    ("006800", "미래에셋증권", "20260201", "20260315"),
    ("009150", "삼성전기", "20260101", "20260331"),
    ("000660", "SK하이닉스", "20260101", "20260331"),
    ("307950", "현대오토에버", "20251001", "20260530"),
    ("001740", "SK네트웍스", "20260401", "20260530"),
    ("010170", "대한광통신", "20260201", "20260530"),
    ("043260", "성호전자", "20251201", "20260530"),
    ("018260", "삼성SDS", "20250601", "20260530"),
    ("001820", "삼화콘덴서", "20260201", "20260331"),
]


def _stats(trades):
    if not trades:
        return (0, 0.0, 0.0)
    rets = [t[4] for t in trades]
    wins = [r for r in rets if r > 0]
    return (len(trades), len(wins) / len(trades) * 100, sum(rets) / len(rets))


async def main() -> None:
    cap = float(sys.argv[1]) if len(sys.argv) > 1 else 130.0
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print(f"극단 급등 상한 보강 비교 (상한 +{cap:.0f}%)")
    print("=" * 86)
    print(f"{'종목':<14}│{'  미포함 (진입/승률/평균)':>26}│{'  포함 (진입/승률/평균)':>26}")
    print("-" * 86)

    agg_off = []
    agg_on = []
    for ticker, name, start, end in CASES:
        try:
            candles = await adapter.get_ohlcv(ticker, days=320, end_date=end)
        except Exception as exc:
            print(f"{name:<14}│ 조회 실패: {exc}")
            continue
        if len(candles) < 60:
            print(f"{name:<14}│ 데이터 부족")
            continue
        _, t_off = compute_trades(candles, start, end, max_surge_pct=None)
        _, t_on = compute_trades(candles, start, end, max_surge_pct=cap)
        agg_off.extend(t_off)
        agg_on.extend(t_on)
        n0, w0, a0 = _stats(t_off)
        n1, w1, a1 = _stats(t_on)
        print(f"{name:<14}│{n0:>6}회 {w0:>4.0f}% {a0:>+8.1f}%   │{n1:>6}회 {w1:>4.0f}% {a1:>+8.1f}%")

    print("-" * 86)
    N0, W0, A0 = _stats(agg_off)
    N1, W1, A1 = _stats(agg_on)
    print(f"{'전체 합산':<14}│{N0:>6}회 {W0:>4.0f}% {A0:>+8.1f}%   │{N1:>6}회 {W1:>4.0f}% {A1:>+8.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
