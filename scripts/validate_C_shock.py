"""C 전략 시장충격 내성 검증.

질문: 이미 보유 중인 대세주가 일시적 시장충격(3/26~4/13)에 손절(60선 2일이탈) 당하는가?
20선 손절과 비교해 60선이 충격을 견디는지 확인.

사용법: python scripts/validate_C_shock.py [시작] [종료]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average

LEADERS = {
    "000660": "SK하이닉스", "005930": "삼성전자", "009150": "삼성전기",
    "011070": "LG이노텍", "066570": "LG전자", "005380": "현대차",
    "307950": "현대오토에버", "018260": "삼성에스디에스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "012450": "한화에어로스페이스", "042660": "한화오션",
}


def stop_fired(candles, ma, start, end):
    """기간 내 종가 ma 2일연속 이탈 발생일 반환 (없으면 None)."""
    for j in range(1, len(candles)):
        d = candles[j].date
        if d < start or d > end:
            continue
        if ma[j] is None or ma[j - 1] is None:
            continue
        if candles[j].close < ma[j] and candles[j - 1].close < ma[j - 1]:
            return d
    return None


def min_gap(candles, ma, start, end):
    """기간 내 종가가 ma 대비 가장 낮았던 이격%(저점 근접도)."""
    worst = None
    for j in range(len(candles)):
        d = candles[j].date
        if d < start or d > end or ma[j] is None:
            continue
        g = (candles[j].close - ma[j]) / ma[j] * 100
        worst = g if worst is None else min(worst, g)
    return worst


async def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "20260326"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260413"
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print(f"C 전략 시장충격 내성 검증 — {start}~{end}")
    print("질문: 이미 보유한 대세주가 일시적 충격에 60선 손절당하는가? (20선과 비교)")
    print("=" * 88)
    print(f"{'종목':<14}{'60선손절':<12}{'60선최저이격':>12}{'20선손절':<12}{'20선최저이격':>12}")
    print("-" * 88)

    n60 = n20 = total = 0
    for tk, name in LEADERS.items():
        try:
            c = await a.get_ohlcv(tk, days=300, end_date=end)
        except Exception:
            continue
        if len(c) < 130:
            continue
        closes = [x.close for x in c]
        ma60 = moving_average(closes, 60)
        ma20 = moving_average(closes, 20)
        # 충격 직전 정배열(보유 중) 종목만 대상
        # start 직전 봉 인덱스
        pre = next((i for i in range(len(c) - 1, -1, -1) if c[i].date < start), None)
        if pre is None or ma60[pre] is None or c[pre].close < ma60[pre]:
            continue  # 충격 직전 이미 60선 아래면 대세주 아님 (제외)
        total += 1
        s60 = stop_fired(c, ma60, start, end)
        s20 = stop_fired(c, ma20, start, end)
        g60 = min_gap(c, ma60, start, end)
        g20 = min_gap(c, ma20, start, end)
        if s60:
            n60 += 1
        if s20:
            n20 += 1
        print(f"{name:<14}{(s60 or '버팀'):<12}{(f'{g60:+.0f}%' if g60 is not None else '-'):>12}"
              f"{(s20 or '버팀'):<12}{(f'{g20:+.0f}%' if g20 is not None else '-'):>12}")

    print("-" * 88)
    if total:
        print(f"대상(충격직전 보유중) {total}종목 | "
              f"60선 손절 {n60}종목({n60/total*100:.0f}%) | "
              f"20선 손절 {n20}종목({n20/total*100:.0f}%)")
        print(f"\n결론: 60선 손절은 일시적 충격에 {'대부분 버팀' if n60/total < 0.5 else '상당수 이탈'} "
              f"(20선은 {n20/total*100:.0f}% 이탈 → 단기선일수록 충격에 취약)")


if __name__ == "__main__":
    asyncio.run(main())
