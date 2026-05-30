"""A 전략(수렴→정배열 대세상승 시작) 매수 사례 역산.

핵심 가설: 일봉+주봉 정배열 + 이평선 수렴(박스권) → 돌파 시작 + MACD 확인.
실행: python scripts/analyze_A.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import macd, moving_average
from src.patterns.core import resample_weekly

# (종목코드, 종목명, 매수일 부근)
CASES = [
    ("001740", "SK네트웍스", "20260424"),
    ("011070", "LG이노텍", "20260318"),
    ("018260", "삼성에스디에스", "20260521"),
    ("000660", "SK하이닉스", "20250901"),
    ("009150", "삼성전기", "20250728"),
    ("066570", "LG전자", "20260413"),
    ("005380", "현대차", "20251015"),
    ("047040", "대우건설", "20260115"),
]


def _idx_near(candles, date):
    best = -1
    for i, c in enumerate(candles):
        if c.date <= date:
            best = i
    return best


async def analyze(adapter, ticker, name, date):
    # 매수일 + 여유 30일까지 데이터 (지표 워밍업 위해 충분히)
    end = (int(date[:4]), int(date[4:6]))
    candles = await adapter.get_ohlcv(ticker, days=200, end_date=date)
    if len(candles) < 120:
        return None
    closes = [c.close for c in candles]
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    macd_line, macd_sig, _ = macd(closes)

    weekly = resample_weekly(candles)
    wcloses = [c.close for c in weekly]
    wma20 = moving_average(wcloses, 20)
    wma60 = moving_average(wcloses, 60)

    i = _idx_near(candles, date)
    if i < 120:
        return None
    c = candles[i]
    m5, m20, m60, m120 = ma5[i], ma20[i], ma60[i], ma120[i]
    if None in (m5, m20, m60, m120):
        return None

    # 일봉 정배열 (120 포함 / 60까지)
    align_d = m5 > m20 > m60 > m120
    align_d60 = m5 > m20 > m60
    # 120일선 이격 (종가 대비)
    gap120 = (c.close - m120) / m120 * 100
    # 이평선 수렴도 — (max-min)/ma20 (작을수록 모여있음)
    conv = (max(m5, m20, m60) - min(m5, m20, m60)) / m20 * 100
    # 직전 20일 박스권 폭 (변동성 수축 확인)
    box_hi = max(closes[i - 20:i + 1])
    box_lo = min(closes[i - 20:i + 1])
    box_range = (box_hi - box_lo) / box_lo * 100
    # 주봉 정배열
    wi = len(wma20) - 1
    wm20, wm60 = wma20[wi], wma60[wi]
    align_w = (wm20 is not None and wm60 is not None and wm20 > wm60)
    # MACD
    ml, ms = macd_line[i], macd_sig[i]
    macd_above_sig = (ml is not None and ms is not None and ml > ms)
    macd_above_zero = (ml is not None and ml > 0)
    gc_recent = False
    for k in range(max(1, i - 5), i + 1):
        a0, b0, a1, b1 = macd_line[k-1], macd_sig[k-1], macd_line[k], macd_sig[k]
        if None not in (a0, b0, a1, b1) and a0 <= b0 and a1 > b1:
            gc_recent = True
            break
    # 거래량
    vols = [x.volume for x in candles]
    vol_ratio = vols[i] / (sum(vols[i-5:i]) / 5) if i >= 5 else 0

    return {
        "name": name, "date": c.date, "close": c.close,
        "align_d": align_d, "align_d60": align_d60, "gap120": gap120,
        "conv": conv, "box": box_range,
        "align_w": align_w, "macd_sig": macd_above_sig,
        "macd_zero": macd_above_zero, "gc": gc_recent, "vol": vol_ratio,
    }


async def main():
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print("A 전략 매수 사례 역산 (수렴→정배열 대세상승 시작)")
    print("=" * 100)
    print(f"{'종목':<14}{'매수일':<10}{'5>20>60':>8}{'+120':>6}{'120이격':>8}{'MA수렴%':>8}"
          f"{'박스폭%':>8}{'MACD>0':>7}{'GC':>5}{'거래량':>7}")
    print("-" * 100)
    rows = []
    for ticker, name, date in CASES:
        r = await analyze(adapter, ticker, name, date)
        if not r:
            print(f"{name:<14}{date:<10} 데이터 부족")
            continue
        rows.append(r)
        print(f"{r['name']:<14}{r['date']:<10}{'O' if r['align_d60'] else 'X':>8}"
              f"{'O' if r['align_d'] else 'X':>6}{r['gap120']:>+7.1f}%{r['conv']:>7.1f}%"
              f"{r['box']:>7.1f}%{'O' if r['macd_zero'] else 'X':>7}"
              f"{'O' if r['gc'] else 'X':>5}{r['vol']:>6.1f}x")

    if rows:
        n = len(rows)
        print("\n공통 패턴:")
        print(f"  정배열 5>20>60: {sum(r['align_d60'] for r in rows)}/{n}")
        print(f"  정배열 5>20>60>120: {sum(r['align_d'] for r in rows)}/{n}")
        print(f"  종가>120일선: {sum(1 for r in rows if r['gap120']>0)}/{n} (이격 평균 {sum(r['gap120'] for r in rows)/n:+.1f}%, 범위 {min(r['gap120'] for r in rows):+.1f}~{max(r['gap120'] for r in rows):+.1f}%)")
        print(f"  주봉 정배열(20>60): {sum(r['align_w'] for r in rows)}/{n}")
        print(f"  MA 수렴도: 평균 {sum(r['conv'] for r in rows)/n:.1f}% (범위 {min(r['conv'] for r in rows):.1f}~{max(r['conv'] for r in rows):.1f}%)")
        print(f"  직전 20일 박스폭: 평균 {sum(r['box'] for r in rows)/n:.1f}%")
        print(f"  MACD > 0: {sum(r['macd_zero'] for r in rows)}/{n}  /  GC: {sum(r['gc'] for r in rows)}/{n}")
        print(f"  거래량: 평균 {sum(r['vol'] for r in rows)/n:.1f}배")


if __name__ == "__main__":
    asyncio.run(main())
