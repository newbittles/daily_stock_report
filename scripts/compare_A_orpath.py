"""A3 OR경로 효과 비교 — 순수 vs OR(현재) vs OR타이트.

① 15개 사용자 사례 포착률
② 5/25~29 일별 신호 수 (과다 여부)

순수: breakout_vol_mult=999 (OR경로 사실상 차단)
OR현재: vol_conv_lookback=5, breakout_vol_mult=1.5
OR타이트: vol_conv_lookback=2, breakout_vol_mult=2.0

실행: python scripts/compare_A_orpath.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.base import RankingKind
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_convergence_breakout
from src.screener.pipeline import _is_etf

CASES = [
    ("001740", "SK네트웍스", "20260424"), ("011070", "LG이노텍", "20260318"),
    ("018260", "삼성에스디에스", "20260521"), ("000660", "SK하이닉스", "20250901"),
    ("009150", "삼성전기", "20250728"), ("066570", "LG전자", "20260413"),
    ("005380", "현대차", "20251015"), ("047040", "대우건설", "20260115"),
    ("353200", "대덕전자(1월)", "20260128"), ("012330", "현대모비스", "20260507"),
    ("307950", "현대오토에버", "20260507"), ("319400", "현대무벡스", "20260529"),
    ("018880", "한온시스템", "20260415"), ("402340", "SK스퀘어", "20260413"),
    ("000720", "현대건설", "20260106"),
]

VERSIONS = {
    "순수(OR없음)":   dict(vol_conv_lookback=5, breakout_vol_mult=999),
    "OR현재(5일,1.5x)": dict(vol_conv_lookback=5, breakout_vol_mult=1.5),
    "OR타이트(2일,2x)": dict(vol_conv_lookback=2, breakout_vol_mult=2.0),
}

WINDOW = 5


def _hit_near(candles, date, kw):
    idx = -1
    for i, c in enumerate(candles):
        if c.date <= date:
            idx = i
    if idx < 135:
        return False
    for j in range(max(135, idx - WINDOW), min(len(candles), idx + WINDOW + 1)):
        if is_convergence_breakout(candles[: j + 1], strict_align=False, **kw).matched:
            return True
    return False


async def main():
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    # ① 15개 사례 포착률
    print("① 15개 사용자 사례 포착률")
    print("=" * 60)
    case_candles = {}
    for tk, nm, d in CASES:
        case_candles[(tk, nm, d)] = await a.get_ohlcv(tk, days=200, end_date=d)
    for vname, kw in VERSIONS.items():
        hits = sum(1 for (tk, nm, d), c in case_candles.items()
                   if len(c) >= 135 and _hit_near(c, d, kw))
        print(f"  {vname:<18}: {hits}/15 ({hits/15*100:.0f}%)")

    # ② 5/25~29 일별 신호 수 (유니버스: 핫종목+검증종목)
    print("\n② 5/25~29 일별 신호 수 (과다 여부)")
    print("=" * 60)
    EXTRA = {"010170": "대한광통신", "307950": "현대오토에버", "001740": "SK네트웍스",
             "043260": "성호전자", "066570": "LG전자", "242040": "나무기술", "018260": "삼성SDS"}
    universe = dict(EXTRA)
    for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
        for r in await a.get_ranking(kind, top=30):
            if r.ticker and not _is_etf(r.name):
                universe[r.ticker] = r.name
    cm = {}
    for tk in universe:
        try:
            c = await a.get_ohlcv(tk, days=150, end_date="20260529")
            if len(c) >= 135:
                cm[tk] = c
        except Exception:
            pass
    sample = next(iter(cm.values()))
    dates = sorted({c.date for c in sample if "20260525" <= c.date <= "20260529"})
    print(f"  유니버스 {len(cm)}종목")
    print(f"  {'버전':<18}" + "".join(f"{d[4:]:>7}" for d in dates) + f"{'합계':>7}")
    for vname, kw in VERSIONS.items():
        counts = []
        for d in dates:
            cnt = 0
            for tk, c in cm.items():
                idx = next((i for i, x in enumerate(c) if x.date == d), None)
                if idx is None or idx < 135:
                    continue
                if is_convergence_breakout(c[: idx + 1], strict_align=False, **kw).matched:
                    cnt += 1
            counts.append(cnt)
        print(f"  {vname:<18}" + "".join(f"{n:>7}" for n in counts) + f"{sum(counts):>7}")


if __name__ == "__main__":
    asyncio.run(main())
