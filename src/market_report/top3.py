"""Top3 종합 추천 — A/B/C/D 스크린 + 주도주/거래량/수급 종합 점수로 딱 3종목 선정.

선정 기준 P4(추세+끝물회피) — 한 달 백테스트 최고(평균 +21.9%, 승률 87%, 손절 1/54):
  점수 = strat(C3·D2.5·B2·A1.5) + mom·상승률 + liq·거래대금 + align·20선이격
        + nh·신고가근접 + supply·수급(외인/기관 순매수) - end·끝물
종목이 여러 전략에 잡히면 최고 strat 채택. 수급(외인/기관 순매수 상위)은 가산.
백테스트 근거: scripts/backtest_top3.py
"""
from __future__ import annotations

import re

_PREF_RE = re.compile(r"우[A-C]?$")  # 우선주(삼성전기우, 한화3우B 등) — Top3에서 제외(보통주 우선)

# P4 가중치 (백테스트 확정) + supply(수급, 실시간 보강)
WEIGHTS = {"strat": 3.0, "mom": 0.5, "liq": 0.5, "align": 0.1, "nh": 1.0, "supply": 2.0, "end": 6.0}
_STRAT_W = {"D. 추세 반전": 2.5, "C. 대세 정배열 추세추종": 3.0,
            "B. 주도주 20일선 눌림목": 2.0, "A. 수렴 후 대세상승 시작": 1.5}


def _strat_weight(name: str) -> float:
    for k, v in _STRAT_W.items():
        if name.startswith(k[:2]):  # "A."/"B."/"C."/"D." 접두 매칭
            return v
    return 1.0


def select_top3(screen_picks: list[dict], foreign_buy: set[str] | None = None,
                inst_buy: set[str] | None = None, w: dict | None = None) -> list[dict]:
    """A/B/C/D 스크린 결과 → 종합점수 상위 3종목. 추천 이유 동반.

    foreign_buy/inst_buy: 외국인/기관 순매수 상위 종목코드 집합(수급 가산).
    """
    w = w or WEIGHTS
    fb, ib = foreign_buy or set(), inst_buy or set()

    # 종목별 집계 (여러 전략 매칭 → 최고 strat 전략 채택). 우선주 제외(보통주 우선).
    by_ticker: dict[str, dict] = {}
    for p in screen_picks:
        if _PREF_RE.search(p.get("name", "")):
            continue
        tk = p["ticker"]
        sw = _strat_weight(p["strategy"])
        cur = by_ticker.get(tk)
        if cur is None or sw > cur["_sw"]:
            by_ticker[tk] = {**p, "_sw": sw, "_strats": set()}
        by_ticker[tk]["_strats"].add(p["strategy"].split(".")[0].strip())

    ranked = []
    for tk, p in by_ticker.items():
        supply = (1 if tk in fb else 0) + (1 if tk in ib else 0)  # 0~2
        score = (
            w["strat"] * p["_sw"]
            + w["mom"] * p.get("change_pct", 0)
            + w["liq"] * p.get("_liq", 0)
            + w["align"] * min(p.get("_gap20", 0), 30)
            + w["nh"] * p.get("_nh", 0)
            + w["supply"] * supply
            - w["end"] * (1 if p.get("endstage") else 0)
        )
        # 추천 이유 구성
        why = []
        strats = "·".join(sorted(p["_strats"]))
        why.append(f"{strats} 시그널")
        if tk in fb and tk in ib:
            why.append("외국인+기관 동반 순매수")
        elif tk in fb:
            why.append("외국인 순매수")
        elif tk in ib:
            why.append("기관 순매수")
        if p.get("change_pct", 0) >= 3:
            why.append(f"당일 +{p['change_pct']:.1f}% 강세")
        if p.get("_nh", 0) >= 2:
            why.append("신고가권")
        if p.get("theme"):
            why.append(f"테마:{p['theme']}")
        if p.get("endstage"):
            why.append("⚠️끝물주의")
        ranked.append({
            "ticker": tk, "name": p["name"], "price": p["price"],
            "change_pct": p.get("change_pct", 0), "score": round(score, 1),
            "reason": " · ".join(why), "theme": p.get("theme", ""),
            "theme_kind": p.get("theme_kind", ""), "theme_idx": p.get("theme_idx", ""),
            "endstage": bool(p.get("endstage")),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:3]
