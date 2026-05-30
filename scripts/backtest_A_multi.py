"""A 전략 다종목 백테스트 — 진입(A3) + 청산(MACD/20선/일목).

실행: python scripts/backtest_A_multi.py <시작> <종료>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from scripts.backtest_A import backtest

CASES = [
    ("000660", "SK하이닉스"), ("005930", "삼성전자"), ("066570", "LG전자"),
    ("009150", "삼성전기"), ("005380", "현대차"), ("035420", "NAVER"),
    ("011070", "LG이노텍"), ("035720", "카카오"), ("006400", "삼성SDI"),
    ("000150", "두산"), ("242040", "나무기술"), ("353200", "대덕전자"),
]


def _stats(trades):
    if not trades:
        return (0, 0.0, 0.0, 0.0)
    rets = [t[4] for t in trades]
    wins = [r for r in rets if r > 0]
    return (len(trades), len(wins) / len(trades) * 100, sum(rets) / len(rets), max(rets))


async def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "20250701"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260530"
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print(f"A 전략 다종목 백테스트 ({start}~{end})")
    print("=" * 70)
    print(f"{'종목':<12}{'진입':>5}{'승률':>7}{'평균':>9}{'최고':>9}")
    print("-" * 70)

    agg = []
    for ticker, name in CASES:
        try:
            candles = await adapter.get_ohlcv(ticker, days=400, end_date=end)
        except Exception as exc:
            print(f"{name:<12} 조회실패: {exc}")
            continue
        if len(candles) < 130:
            print(f"{name:<12} 데이터부족")
            continue
        trades = backtest(candles, start, end)
        agg.extend(trades)
        n, w, a, mx = _stats(trades)
        if n == 0:
            print(f"{name:<12}{'0':>5}  (신호없음)")
        else:
            print(f"{name:<12}{n:>5}{w:>6.0f}%{a:>+8.1f}%{mx:>+8.1f}%")

    print("-" * 70)
    N, W, A, MX = _stats(agg)
    if N:
        print(f"{'전체합산':<12}{N:>5}{W:>6.0f}%{A:>+8.1f}%{MX:>+8.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
