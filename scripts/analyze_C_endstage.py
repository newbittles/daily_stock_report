"""C 끝물 필터 — 진짜 대세 vs 가짜/끝물 진입의 차이 역산.

각 사례의 첫 진입 시점에서 '끝물 의심' 지표 측정:
  - 60일선 이격 (진입가가 60선 대비 얼마 위) — 과열도
  - 120일 상승률 (이미 얼마나 올랐나)
  - 60일선 기울기 (추세 강도)
  - 신고가 이후 경과일 (직전 신고가 갱신 후 며칠) — 횡보 길면 끝물
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average

# (종목, 라벨, 진입대표일, 결과) — 진짜/가짜 구분 라벨
CASES = [
    ("347850", "디엔디파마텍", "20250430", "진짜+122%"),
    ("277810", "레인보우②", "20250922", "진짜+92%"),
    ("005380", "현대차", "20251016", "진짜+36%"),
    ("003230", "삼양식품24", "20240403", "진짜+78%"),
    ("373220", "LG에너지솔루션", "20250724", "가짜0%"),
    ("066570", "LG전자끝물", "20260211", "가짜-13%"),
    ("277810", "레인보우①끝물", "20250218", "가짜-31%"),
    ("003230", "삼양식품끝물", "20250708", "가짜손절"),
]


async def main():
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    print(f"{'종목':<16}{'결과':<12}{'60선이격':>8}{'120일상승':>9}{'60선기울기':>10}{'신고가후일':>9}")
    print("-" * 72)
    rows = []
    for tk, label, date, result in CASES:
        candles = await a.get_ohlcv(tk, days=400, end_date=date)
        if len(candles) < 130:
            print(f"{label:<16}데이터부족")
            continue
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        ma60 = moving_average(closes, 60)
        i = len(candles) - 1
        price = closes[i]
        gap60 = (price - ma60[i]) / ma60[i] * 100 if ma60[i] else 0
        # 120일 상승률 (120일전 대비)
        rise120 = (price - closes[i - 120]) / closes[i - 120] * 100 if i >= 120 else 0
        # 60선 기울기 (10일)
        slope60 = (ma60[i] - ma60[i - 10]) / ma60[i - 10] * 100 if ma60[i] and ma60[i - 10] else 0
        # 신고가 후 경과일 (최근 60일 최고가가 며칠 전)
        win = highs[i - 59:i + 1]
        hi = max(win)
        days_since_high = len(win) - 1 - max(j for j, v in enumerate(win) if v == hi)
        rows.append((label, result, gap60, rise120, slope60, days_since_high))
        print(f"{label:<16}{result:<12}{gap60:>+7.1f}%{rise120:>+8.0f}%{slope60:>+9.1f}%{days_since_high:>8}일")

    print("\n[진짜 vs 가짜 평균]")
    real = [r for r in rows if "진짜" in r[1]]
    fake = [r for r in rows if "가짜" in r[1]]
    for grp, nm in ((real, "진짜"), (fake, "가짜")):
        if grp:
            print(f"  {nm}: 60선이격 {sum(r[2] for r in grp)/len(grp):+.1f}% / "
                  f"120일상승 {sum(r[3] for r in grp)/len(grp):+.0f}% / "
                  f"60선기울기 {sum(r[4] for r in grp)/len(grp):+.1f}% / "
                  f"신고가후 {sum(r[5] for r in grp)/len(grp):.0f}일")


if __name__ == "__main__":
    asyncio.run(main())
