"""종가베팅 + ATR 손절 백테스트 — 전날 Top3 종가매수 → 다음날 ATR 손절 발동 여부.

시나리오(사용자 메이저 기법 = 종가베팅):
  1. 매 영업일 d에 P4 점수로 Top3 선정
  2. d 종가에 매수
  3. 손절가 = d 종가 - 2×ATR(14)  (퍼센트는 -2×ATR/종가)
  4. 다음날(d+1) 장중 저가가 손절가 이탈 → "손절 발동" (장중 터치 기준)
     + d+1 종가가 손절가 하회도 별도 집계
  5. 한 달 누적: 총 매수건, 다음날 손절발동 건수·비율·케이스 상세

backtest_top3.py 와 동일 유니버스·점수(P4)·일봉. ATR 손절만 추가 검증.
"""
from __future__ import annotations

import asyncio
import sys
from collections import namedtuple
from math import log10
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.indicators.core import average_true_range, moving_average
from src.patterns.core import (
    is_convergence_breakout, is_downtrend_reversal, is_ma20_pullback, is_trend_follow,
)

LEADERS = {
    "000660": "SK하이닉스", "005930": "삼성전자", "009150": "삼성전기", "011070": "LG이노텍",
    "066570": "LG전자", "005380": "현대차", "307950": "현대오토에버", "018260": "삼성에스디에스",
    "000270": "기아", "005490": "POSCO홀딩스", "035420": "NAVER", "035720": "카카오",
    "207940": "삼성바이오로직스", "373220": "LG에너지솔루션", "006400": "삼성SDI",
    "012450": "한화에어로스페이스", "042660": "한화오션", "064400": "LG씨엔에스",
}

# P4 (운영 확정 가중치) — backtest_top3 P4와 동일 (supply는 과거데이터 불가라 제외)
W = {"strat": 3.0, "mom": 0.5, "liq": 0.5, "align": 0.1, "nh": 1.0, "end": 6.0}
ATR_MULT = 2.0


def metrics_at(c, i):
    if i < 130:
        return None
    sub = c[: i + 1]
    closes = [x.close for x in sub]
    highs = [x.high for x in sub]
    ma20 = moving_average(closes, 20)[-1]
    ma60 = moving_average(closes, 60)[-1]
    if None in (ma20, ma60):
        return None
    price = closes[-1]
    chg = (price - closes[-2]) / closes[-2] * 100 if closes[-2] else 0
    liq = log10(max(price * c[i].volume, 1))
    gap20 = (price - ma20) / ma20 * 100
    hi60 = max(highs[-60:])
    nh = price / hi60 if hi60 else 0
    sc, end, kind = 0.0, 0, ""
    if is_trend_follow(sub).matched:
        tf = is_trend_follow(sub)
        sc, kind = 3.0, "C"
        end = 1 if tf.metrics.get("endstage") else end
    if is_downtrend_reversal(sub).matched and 2.5 > sc:
        sc, kind = 2.5, "D"
    if is_ma20_pullback(sub).matched and 2.0 > sc:
        sc, kind = 2.0, "B"
    if is_convergence_breakout(sub).matched and 1.5 > sc:
        sc, kind = 1.5, "A"
    return {"strat": sc, "kind": kind, "chg": chg, "liq": liq, "gap20": gap20,
            "nh": (nh - 0.97) * 100, "end": end}


def score(m):
    return (W["strat"] * m["strat"] + W["mom"] * m["chg"] + W["liq"] * m["liq"]
            + W["align"] * min(m["gap20"], 30) + W["nh"] * m["nh"] - W["end"] * m["end"])


Candle = namedtuple("Candle", "date open high low close volume")


def load_fdr(fdr, code, start="2025-08-01", end="2026-06-01"):
    """FinanceDataReader 일봉 → Candle 리스트 (KIS paper 500 회피, 과거 일봉만 필요)."""
    df = fdr.DataReader(code, start, end)
    out = []
    for d, r in df.iterrows():
        try:
            out.append(Candle(d.strftime("%Y%m%d"), float(r["Open"]), float(r["High"]),
                              float(r["Low"]), float(r["Close"]), float(r["Volume"])))
        except Exception:
            continue
    return out


async def main():
    import FinanceDataReader as fdr
    print("유니버스 일봉 수집 (FinanceDataReader)...")
    cmap, names = {}, {}
    uni = dict(LEADERS)
    try:
        for mkt, top in (("KOSPI", 80), ("KOSDAQ", 40)):
            df = fdr.StockListing(mkt).dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False).head(top)
            for _, r in df.iterrows():
                uni.setdefault(str(r["Code"]).zfill(6), str(r["Name"]))
    except Exception as e:
        print("FDR 상장목록 실패(주도주만):", e)
    for tk, nm in uni.items():
        try:
            c = load_fdr(fdr, tk)
        except Exception:
            continue
        if len(c) >= 135:
            cmap[tk] = c
            names[tk] = nm
    print(f"유니버스 {len(cmap)}종목")

    sample = max(cmap.values(), key=len)
    dates = [x.date for x in sample]
    # 한 달: 다음날(d+1)이 존재해야 하므로 마지막 직전일까지
    bt_dates = [d for d in dates if "20260502" <= d <= "20260529"]
    idx_of = {tk: {x.date: k for k, x in enumerate(c)} for tk, c in cmap.items()}

    # 매 백테스트일 Top3 선정 (배수 무관 — 한 번만)
    picks = []  # (d, tk, i, kind)
    for d in bt_dates:
        cand = []
        for tk, c in cmap.items():
            i = idx_of[tk].get(d)
            if i is None or i + 1 >= len(c):
                continue
            m = metrics_at(c, i)
            if m is None or m["strat"] == 0:
                continue
            cand.append((score(m), tk, i, m["kind"]))
        cand.sort(key=lambda x: x[0], reverse=True)
        for _, tk, i, kind in cand[:3]:
            picks.append((d, tk, i, kind))

    # 다음날 종가 수익률(손절 무관) — 종가베팅 단기 성과 참고
    nextday_ret = []
    for d, tk, i, kind in picks:
        c = cmap[tk]
        nextday_ret.append((c[i + 1].close - c[i].close) / c[i].close * 100)
    avg_nx = sum(nextday_ret) / len(nextday_ret) if nextday_ret else 0
    win_nx = sum(1 for r in nextday_ret if r > 0) / len(nextday_ret) * 100 if nextday_ret else 0

    print(f"\n백테스트 {bt_dates[0]}~{bt_dates[-1]} · Top3 종가매수 → 다음날 ATR 손절 판정")
    print(f"총 매수(일×Top3): {len(picks)}건")
    print(f"[참고] 종가베팅 다음날 종가 수익률 평균 {avg_nx:+.2f}% · 승률 {win_nx:.0f}%\n")
    print(f"{'배수':>5} {'평균손절폭':>9} {'손절폭범위':>15} {'익일장중터치':>12} {'익일종가이탈':>12}")

    detail_cases = {}
    for mult in (1.0, 1.5, 2.0):
        total, touch_lo, breach_close = 0, 0, 0
        stop_pcts, cases = [], []
        for d, tk, i, kind in picks:
            c = cmap[tk]
            buy = c[i].close
            sub = c[: i + 1]
            atr = average_true_range([x.high for x in sub], [x.low for x in sub],
                                     [x.close for x in sub], 14)
            if not atr or not buy:
                continue
            stop = max(buy - mult * atr, 0.0)
            stop_pct = (stop - buy) / buy * 100
            stop_pcts.append(stop_pct)
            total += 1
            nx = c[i + 1]
            if nx.low <= stop:
                touch_lo += 1
                cases.append((d, names[tk], kind, buy, stop, stop_pct, nx.low, nx.close,
                              nx.close <= stop))
            if nx.close <= stop:
                breach_close += 1
        avg_stop = sum(stop_pcts) / len(stop_pcts) if stop_pcts else 0
        rng = f"{min(stop_pcts):+.1f}~{max(stop_pcts):+.1f}%" if stop_pcts else "-"
        print(f"{mult:>4.1f}× {avg_stop:>+8.1f}% {rng:>15} "
              f"{touch_lo:>6}건({touch_lo/total*100:>4.1f}%) {breach_close:>6}건({breach_close/total*100:>4.1f}%)"
              if total else f"{mult}× (데이터없음)")
        detail_cases[mult] = cases

    # 손절 발동 케이스 상세 (배수별)
    for mult in (1.0, 1.5, 2.0):
        cs = detail_cases[mult]
        if cs:
            print(f"\n=== {mult}×ATR 다음날 손절 발동 ({len(cs)}건) ===")
            for d, nm, kind, buy, stop, spct, lo, cl, cb in cs:
                print(f"  {d[4:]} {nm}({kind}) 매수{buy:,.0f}→손절{stop:,.0f}({spct:+.1f}%) "
                      f"익일저가{lo:,.0f} {'종가이탈O' if cb else '장중만'}")


if __name__ == "__main__":
    asyncio.run(main())
