"""Top3 종합추천 백테스트 — 여러 선정기준 프리셋의 수익률 비교.

유니버스(시총상위+주도주) 일봉 1회 수집 → 각 백테스트일에 종목별 종합점수로 Top3 선정
→ 추천일 종가 매수 → 최신(6/1) 종가까지 수익률 → 프리셋별 평균 비교.

점수 = strat(전략매칭 C3/D2.5/B2/A1.5) + mom(당일상승률) + liq(거래대금)
      + align(20선이격) + nh(신고가근접) - end(끝물).
"""
from __future__ import annotations

import asyncio
import sys
from math import log10
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.indicators.core import moving_average
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

PRESETS = {
    "P1_추세우선":   {"strat": 3.0, "mom": 0.3, "liq": 0.5, "align": 0.1, "nh": 1.0, "end": 2.0},
    "P2_모멘텀":     {"strat": 1.0, "mom": 1.5, "liq": 1.0, "align": 0.0, "nh": 0.5, "end": 1.0},
    "P3_강세돌파":   {"strat": 2.0, "mom": 0.5, "liq": 0.3, "align": 0.5, "nh": 2.0, "end": 1.0},
    "P4_추세+끝물회피": {"strat": 3.0, "mom": 0.5, "liq": 0.5, "align": 0.1, "nh": 1.0, "end": 6.0},
    "P5_균형":       {"strat": 2.0, "mom": 0.8, "liq": 0.6, "align": 0.3, "nh": 1.0, "end": 2.0},
    "P6_모멘텀+추세": {"strat": 2.5, "mom": 1.2, "liq": 0.8, "align": 0.2, "nh": 1.0, "end": 1.5},
}


def metrics_at(c, i):
    """i 시점 종목 지표 (없으면 None)."""
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
    nh = price / hi60 if hi60 else 0  # 1.0이면 신고가
    # 전략 매칭 → strat 가중 (복수면 최대) + 대표전략(손절선 결정)
    sc = 0.0
    end = 0
    kind = ""
    if is_trend_follow(sub).matched:
        tf = is_trend_follow(sub)
        if 3.0 > sc:
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


def exit_by_stop(c, i, stop_ma):
    """i 추천 후 stop_ma선 종가 2일연속 이탈 → 청산. 없으면 보유중(최신종가)."""
    closes = [x.close for x in c]
    ma = moving_average(closes, stop_ma)
    for j in range(i + 1, len(c)):
        if ma[j] is not None and ma[j - 1] is not None and closes[j] < ma[j] and closes[j - 1] < ma[j - 1]:
            exitp = c[j + 1].open if j + 1 < len(c) else closes[j]
            return exitp, c[j].date, "손절"
    return closes[-1], c[-1].date, "보유중"


def score(m, w):
    return (w["strat"] * m["strat"] + w["mom"] * m["chg"] + w["liq"] * m["liq"]
            + w["align"] * min(m["gap20"], 30) + w["nh"] * m["nh"] - w["end"] * m["end"])


async def main():
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    print("유니버스 일봉 수집...")
    cmap, names = {}, {}
    # 시총 상위 추가
    uni = dict(LEADERS)
    try:
        import FinanceDataReader as fdr
        for mkt, top in (("KOSPI", 60), ("KOSDAQ", 30)):
            df = fdr.StockListing(mkt).dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False).head(top)
            for _, r in df.iterrows():
                uni.setdefault(str(r["Code"]).zfill(6), str(r["Name"]))
    except Exception as e:
        print("FDR 실패:", e)
    for tk, nm in uni.items():
        try:
            c = await a.get_ohlcv(tk, days=220, end_date="20260601")
        except Exception:
            continue
        if len(c) >= 135:
            cmap[tk] = c; names[tk] = nm
    print(f"유니버스 {len(cmap)}종목")

    STOP_MA = {"C": 60, "D": 60, "B": 20, "A": 20}  # 전략별 손절선
    sample = max(cmap.values(), key=len)
    dates = [x.date for x in sample]
    # 한 달 전부 (5/2~5/29) 추천 → 손절기준대로 현재(6/1)까지 보유/손절 시뮬
    bt_dates = [d for d in dates if "20260502" <= d <= "20260529"]
    last_date = sample[-1].date
    print(f"백테스트일 {len(bt_dates)}일: {bt_dates[0]}~{bt_dates[-1]} · 손절선 청산(C/D=60선·B/A=20선 2일이탈) → {last_date}까지\n")

    idx_of = {tk: {x.date: k for k, x in enumerate(c)} for tk, c in cmap.items()}
    results = []
    show = None  # 최고 프리셋 상세 종목
    for name, w in PRESETS.items():
        rets, held, stopped = [], 0, 0
        detail = []
        for d in bt_dates:
            cand = []
            for tk, c in cmap.items():
                i = idx_of[tk].get(d)
                if i is None:
                    continue
                m = metrics_at(c, i)
                if m is None or m["strat"] == 0:
                    continue
                cand.append((score(m, w), tk, i, m["kind"], c[i].close))
            cand.sort(key=lambda x: x[0], reverse=True)
            for _, tk, i, kind, buy in cand[:3]:
                exitp, exitd, status = exit_by_stop(cmap[tk], i, STOP_MA.get(kind, 20))
                r = (exitp - buy) / buy * 100
                rets.append(r)
                if status == "보유중":
                    held += 1
                else:
                    stopped += 1
                detail.append((d, names[tk], kind, buy, status, exitd, r))
        avg = sum(rets) / len(rets) if rets else 0
        win = sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else 0
        results.append((avg, win, len(rets), held, stopped, name))
        print(f"[{name:14}] 평균 {avg:+.2f}% · 승률 {win:.0f}% · 보유중 {held}/손절 {stopped} · n={len(rets)}")
        if name == "P6_모멘텀+추세":
            show = detail

    best = max(results)
    print(f"\n>>> 최고 수익률: {best[5]} (평균 {best[0]:+.2f}%, 승률 {best[1]:.0f}%, 보유중 {best[3]}/손절 {best[4]})")
    if show:
        print(f"\n=== P6 종목별 (한달전 매수→손절기준 청산) ===")
        for d, nm, kind, buy, status, exitd, r in show:
            print(f"  {d[4:]} {nm}({kind}) {buy:,.0f} → {status}({exitd[4:]}) {r:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
