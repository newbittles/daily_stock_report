"""사용자 눌림목 매수 시점 역산 분석.

사용자가 제시한 종목·매수일의 지표를 계산해 공통 패턴을 추출.
실행: python scripts/analyze_buypoints.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import ichimoku, macd, moving_average, rsi

# (종목코드, 종목명, [매수일 YYYYMMDD...])
CASES = [
    ("006800", "미래에셋증권", ["20260206", "20260212", "20260304"]),
    ("009830", "한화솔루션", ["20260212", "20260213"]),
    ("000270", "기아", ["20260506"]),
    ("009150", "삼성전기", ["20260303"]),
    ("272210", "한화시스템", ["20260209"]),
]


def _find_idx(candles, date: str) -> int:
    for i, c in enumerate(candles):
        if c.date == date:
            return i
    # 정확한 날짜 없으면 가장 가까운 이전 거래일
    best = -1
    for i, c in enumerate(candles):
        if c.date <= date:
            best = i
    return best


async def analyze_case(adapter, ticker: str, name: str, dates: list[str]):
    candles = await adapter.get_ohlcv(ticker, days=160)
    if len(candles) < 60:
        print(f"  {name}({ticker}): 데이터 부족 ({len(candles)}봉)")
        return []

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    rsi14 = rsi(closes, 14)
    macd_line, macd_sig, _h = macd(closes)
    cloud = ichimoku(highs, lows, closes)

    rows = []
    for d in dates:
        i = _find_idx(candles, d)
        if i < 0 or i >= len(candles):
            print(f"  {name} {d}: 데이터 없음 (보유범위 {candles[0].date}~{candles[-1].date})")
            continue
        c = candles[i]
        m5, m20, m60, r = ma5[i], ma20[i], ma60[i], rsi14[i]
        if m20 is None:
            print(f"  {name} {d}: MA20 계산불가")
            continue

        # 20일선 이격
        gap20 = (c.close - m20) / m20 * 100
        # 종가가 20일선 위? (사용자 핵심 기준)
        above_ma20 = c.close >= m20
        # 정배열 여부
        align = (m5 is not None and m60 is not None and m5 > m20 > m60)
        # 직전 20일 고점 대비 (급등 후 조정 정도)
        hi20 = max(closes[max(0, i - 20):i + 1])
        from_high = (c.close - hi20) / hi20 * 100
        # 직전 거래량 급증 이력 (최근 15일 내 5일평균 2배+)
        vols = [x.volume for x in candles]
        max_vol_ratio = 0.0
        for j in range(max(5, i - 15), i + 1):
            avg5 = sum(vols[j - 5:j]) / 5 if j >= 5 else 0
            if avg5 > 0:
                max_vol_ratio = max(max_vol_ratio, vols[j] / avg5)
        # 직전 며칠 음봉 수 (최근 5일)
        bearish_5 = sum(1 for x in candles[max(0, i - 4):i + 1] if x.close < x.open)
        # 당일 거래량비
        avg5_today = sum(vols[i - 5:i]) / 5 if i >= 5 else 0
        vol_today = vols[i] / avg5_today if avg5_today > 0 else 0

        # MACD 상태
        ml, ms = macd_line[i], macd_sig[i]
        macd_above_signal = (ml is not None and ms is not None and ml > ms)
        macd_above_zero = (ml is not None and ml > 0)
        # 최근 5봉 내 골든크로스 발생?
        macd_gc_recent = False
        for k in range(max(1, i - 4), i + 1):
            a0, b0, a1, b1 = macd_line[k-1], macd_sig[k-1], macd_line[k], macd_sig[k]
            if None not in (a0, b0, a1, b1) and a0 <= b0 and a1 > b1:
                macd_gc_recent = True
                break

        # 일목 구름 대비 위치
        sa, sb = cloud["senkou_a"][i], cloud["senkou_b"][i]
        ich = "?"
        if sa is not None and sb is not None:
            top, bot = max(sa, sb), min(sa, sb)
            ich = "위" if c.close > top else "아래" if c.close < bot else "내부"

        rows.append({
            "name": name, "date": c.date, "close": c.close,
            "gap20": gap20, "above_ma20": above_ma20, "align": align,
            "rsi": r, "from_high": from_high, "max_vol_ratio": max_vol_ratio,
            "bearish_5": bearish_5, "vol_today": vol_today,
            "macd_sig": macd_above_signal, "macd_zero": macd_above_zero,
            "macd_gc": macd_gc_recent, "ich": ich,
        })
    return rows


async def main():
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print("=" * 90)
    print("  사용자 눌림목 매수 시점 역산 분석")
    print("=" * 90)
    print(f"{'종목':<11}{'매수일':<10}{'20선이격':>8}{'20선위':>6}{'RSI':>5}{'고점대비':>8}"
          f"{'거래량급증':>9}{'MACD>시그':>9}{'MACD>0':>8}{'MACD GC':>8}{'일목':>6}")
    print("-" * 95)

    all_rows = []
    for ticker, name, dates in CASES:
        rows = await analyze_case(adapter, ticker, name, dates)
        all_rows.extend(rows)
        for r in rows:
            print(f"{r['name']:<11}{r['date']:<10}{r['gap20']:>+7.1f}%"
                  f"{'O' if r['above_ma20'] else 'X':>6}{r['rsi']:>5.0f}{r['from_high']:>+7.1f}%"
                  f"{r['max_vol_ratio']:>8.1f}배"
                  f"{'O' if r['macd_sig'] else 'X':>9}{'O' if r['macd_zero'] else 'X':>8}"
                  f"{'O' if r['macd_gc'] else 'X':>8}{r['ich']:>6}")

    # 공통 패턴 집계
    print("\n" + "=" * 90)
    print("  공통 패턴")
    print("=" * 90)
    if all_rows:
        n = len(all_rows)
        above = sum(1 for r in all_rows if r["above_ma20"])
        align = sum(1 for r in all_rows if r["align"])
        gaps = [r["gap20"] for r in all_rows]
        rsis = [r["rsi"] for r in all_rows if r["rsi"]]
        highs = [r["from_high"] for r in all_rows]
        vols = [r["max_vol_ratio"] for r in all_rows]
        print(f"  표본 {n}건")
        print(f"  종가 20일선 위: {above}/{n} ({above/n*100:.0f}%)")
        print(f"  정배열(5>20>60): {align}/{n} ({align/n*100:.0f}%)")
        print(f"  20일선 이격: 평균 {sum(gaps)/len(gaps):+.1f}% (범위 {min(gaps):+.1f}~{max(gaps):+.1f}%)")
        if rsis:
            print(f"  RSI: 평균 {sum(rsis)/len(rsis):.0f} (범위 {min(rsis):.0f}~{max(rsis):.0f})")
        print(f"  직전20일 고점대비: 평균 {sum(highs)/len(highs):+.1f}% (범위 {min(highs):+.1f}~{max(highs):+.1f}%)")
        print(f"  거래량 급증 이력: 평균 {sum(vols)/len(vols):.1f}배 (범위 {min(vols):.1f}~{max(vols):.1f}배)")
        # MACD·일목 집계
        ms = sum(1 for r in all_rows if r["macd_sig"])
        mz = sum(1 for r in all_rows if r["macd_zero"])
        mg = sum(1 for r in all_rows if r["macd_gc"])
        ich_up = sum(1 for r in all_rows if r["ich"] == "위")
        print(f"  MACD > 시그널선: {ms}/{n} ({ms/n*100:.0f}%)")
        print(f"  MACD > 0 (0선 위): {mz}/{n} ({mz/n*100:.0f}%)")
        print(f"  MACD 최근5봉 GC: {mg}/{n} ({mg/n*100:.0f}%)")
        print(f"  일목 구름 위: {ich_up}/{n} ({ich_up/n*100:.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
