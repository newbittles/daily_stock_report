"""D 전략(하락추세 전환) 역산 — 일목 구름 상향 돌파 가설 검증.

사용자 판단 추세전환일 부근에서 실제로 일목 구름(양운) 상향 돌파가 있었는지,
그 시점의 캔들/이평선/RSI/MACD 공통 상태를 출력해 패턴을 추출한다.

일목 구름은 선행스팬을 26봉 미래로 시프트 → 위치 i의 구름 = i-26에서 계산된 senkou.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import ichimoku, macd, moving_average, rsi

# 종목 + 사용자 판단 추세전환일 (FDR 코드 검증 완료)
CASES = [
    ("373220", "LG에너지솔루션", "20250714", "20250815"),
    ("001740", "SK네트웍스",     "20250529", "20250630"),
    ("086520", "에코프로",       "20250623", "20250725"),
    ("064400", "LG씨엔에스",     "20251204", "20260105"),
    ("066570", "LG전자",        "20250610", "20250711"),
    ("035420", "NAVER",        "20240923", "20241025"),
]
SHIFT = 26  # 선행스팬 미래 시프트


async def analyze(a, ticker, name, pivot, end):
    c = await a.get_ohlcv(ticker, days=400, end_date=end)
    if len(c) < 90:
        print(f"\n### {name}({ticker}) — 데이터 부족({len(c)})")
        return
    closes = [x.close for x in c]
    highs = [x.high for x in c]
    lows = [x.low for x in c]
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    rsi_v = rsi(closes, 14)
    macd_line, sig_line, hist = macd(closes)
    cl = ichimoku(highs, lows, closes)
    sa, sb = cl["senkou_a"], cl["senkou_b"]

    def cloud_at(i):
        """위치 i에 그려지는 구름 (i-26에서 계산된 선행스팬)."""
        j = i - SHIFT
        if j < 0 or sa[j] is None or sb[j] is None:
            return None, None
        return max(sa[j], sb[j]), min(sa[j], sb[j])

    # 구름 상향 돌파일 탐지 (전체)
    breakouts = []
    for i in range(1, len(c)):
        top, bot = cloud_at(i)
        ptop, _ = cloud_at(i - 1)
        if top is None or ptop is None:
            continue
        if closes[i] > top and closes[i - 1] <= ptop:
            breakouts.append(i)

    # pivot 인덱스
    pdate_idx = next((i for i, x in enumerate(c) if x.date >= pivot), None)

    print(f"\n{'='*86}\n### {name}({ticker})  사용자판단 추세전환 ≈ {pivot}")
    # 사용자 판단일 부근 가장 가까운 구름돌파
    if breakouts and pdate_idx is not None:
        nearest = min(breakouts, key=lambda i: abs(i - pdate_idx))
        diff = (next((j for j,_ in enumerate(c)), 0))
        bd = c[nearest].date
        gap_days = nearest - pdate_idx
        print(f"  → 가장 가까운 일목 구름 상향돌파일: {bd} (사용자판단 대비 {gap_days:+d}거래일)")
    else:
        print("  → 구름 상향돌파 없음 또는 pivot 범위 밖")

    # pivot ±8거래일 일별 상태
    if pdate_idx is None:
        return
    lo = max(SHIFT + 1, pdate_idx - 8)
    hi = min(len(c), pdate_idx + 9)
    print(f"  {'일자':<10}{'종가':>9}{'구름':>6}{'5>20':>6}{'20>60':>7}{'RSI':>5}{'MACD':>7}  돌파")
    for i in range(lo, hi):
        top, bot = cloud_at(i)
        if top is None:
            continue
        pos = "위" if closes[i] > top else ("내부" if closes[i] >= bot else "아래")
        a520 = "O" if (ma5[i] and ma20[i] and ma5[i] > ma20[i]) else "x"
        a2060 = "O" if (ma20[i] and ma60[i] and ma20[i] > ma60[i]) else "x"
        rv = rsi_v[i] or 0
        mc = "GC" if (hist[i] is not None and hist[i] > 0 and hist[i-1] is not None and hist[i-1] <= 0) else ("+" if (hist[i] or 0) > 0 else "-")
        brk = "★구름돌파" if i in breakouts else ""
        star = " ◀pivot" if i == pdate_idx else ""
        print(f"  {c[i].date:<10}{closes[i]:>9,.0f}{pos:>6}{a520:>6}{a2060:>7}{rv:>5.0f}{mc:>7}  {brk}{star}")


async def main():
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    for tk, nm, pivot, end in CASES:
        try:
            await analyze(a, tk, nm, pivot, end)
        except Exception as e:
            print(f"\n{nm}({tk}) 실패: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
