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

# P4 가중치 (백테스트 확정) + supply(수급) + overheat(과열 강등, 사용자 2026-06-05)
# overheat=5.0: 과열(BB상단 돌파)은 강등하되 완전 제외는 아님(표시 유지). 끝물(6.0) 다음 강한 패널티.
WEIGHTS = {"strat": 3.0, "mom": 0.5, "liq": 0.5, "align": 0.1, "nh": 1.0, "supply": 2.0,
           "end": 6.0, "overheat": 5.0}
# B(주도주 눌림목) 가중치 상향 2.0→2.8 (사용자 2026-06-05: 3일 조정 후 반등 눌림목 선호, 삼성전기 사례).
# ※ 원래 P4 백테스트 최적은 B=2.0 — 사용자 선호 반영(추후 백테스트로 재튜닝 가능).
_STRAT_W = {"D. 추세 반전": 2.5, "C. 대세 정배열 추세추종": 3.0,
            "B. 주도주 20일선 눌림목": 2.8, "A. 수렴 후 대세상승 시작": 1.5}

# B 눌림목 모멘텀 페널티 면제 허용 낙폭(60일 고점 대비 %). 이보다 깊으면 추세 꺾임 의심 → 면제 안 함.
# 25%: 삼성전기(~22%)는 면제, LG전자(~31%)는 면제 안 함(추천 과열 방지, 사용자 2026-06-05 검증).
B_PULLBACK_MAX_DD = 25.0


def _strat_weight(name: str) -> float:
    for k, v in _STRAT_W.items():
        if name.startswith(k[:2]):  # "A."/"B."/"C."/"D." 접두 매칭
            return v
    return 1.0


def select_top3(screen_picks: list[dict], foreign_buy: set[str] | None = None,
                inst_buy: set[str] | None = None, w: dict | None = None,
                us_keywords: set[str] | None = None, w_us: float = 0.0,
                us_sectors: list[dict] | None = None, return_all: bool = False) -> list[dict]:
    """A/B/C/D 스크린 결과 → 종합점수 상위 3종목. 추천 이유 동반.

    foreign_buy/inst_buy: 외국인/기관 순매수 상위 종목코드 집합(수급 가산).
    us_keywords/w_us: 미국 강세테마 연동 가중(us_morning 시초 Top3 전용. 국장은 w_us=0).
    us_sectors: 추천이유에 연결된 미국 섹터명 표기용.
    """
    w = w or WEIGHTS
    fb, ib = foreign_buy or set(), inst_buy or set()
    us_kw = us_keywords or set()

    # 종목별 집계 (여러 전략 매칭 → 최고 strat 전략 채택). 우선주 제외(보통주 우선).
    by_ticker: dict[str, dict] = {}
    for p in screen_picks:
        if _PREF_RE.search(p.get("name", "")):
            continue
        tk = p["ticker"]
        sw = _strat_weight(p["strategy"])
        cur = by_ticker.get(tk)
        if cur is None or sw > cur["_sw"]:
            # 대표 픽 교체 시 기존 _strats를 승계(누적 유지) — 안 그러면 먼저 매칭된 전략 유실(버그 수정)
            _strats = cur["_strats"] if cur else set()
            by_ticker[tk] = {**p, "_sw": sw, "_strats": _strats}
        by_ticker[tk]["_strats"].add(p["strategy"].split(".")[0].strip())

    ranked = []
    for tk, p in by_ticker.items():
        supply = (1 if tk in fb else 0) + (1 if tk in ib else 0)  # 0~2
        # 과열 = 일봉 BB상단 종가돌파(overheat) OR 4시간봉 BB상단 음봉(overheat_4h). 강등(사용자 2026-06-05).
        is_overheat = bool(p.get("overheat") or p.get("overheat_4h"))
        # B(눌림목) 모멘텀 페널티 완화(사용자 2026-06-05): 당일 하락(눌림)을 점수에서 안 깎음.
        # 단 '얕은 눌림'(60일 고점 대비 낙폭 ≤ B_PULLBACK_MAX_DD%)만 — 깊은 낙폭(추세 꺾임 의심,
        # LG전자 -30% 사례)은 페널티 유지해 추천 과열 방지(사용자 우려). drawdown = 1-close/hi60 = 3-_nh.
        _mom_pct = p.get("change_pct", 0)
        _drawdown = 3 - p.get("_nh", 0)
        if "B" in p["_strats"] and _mom_pct < 0 and _drawdown <= B_PULLBACK_MAX_DD:
            _mom_pct = 0.0  # 얕은 B 눌림목: 당일 하락 페널티 면제
        from src.market_report.theme_bridge import matched_us_sector, us_theme_match
        us_hit = us_theme_match(p.get("theme", ""), us_kw)  # 미국 강세테마 연동
        score = (
            w["strat"] * p["_sw"]
            + w["mom"] * _mom_pct
            + w["liq"] * p.get("_liq", 0)
            + w["align"] * min(p.get("gap20", 0), 30)
            + w["nh"] * max(p.get("_nh", 0), 0)  # 신고가 아래(눌림목)는 감점 안 함
            + w["supply"] * supply
            + w_us * (1 if us_hit else 0)
            - w["end"] * (1 if p.get("endstage") else 0)
            - w["overheat"] * (1 if is_overheat else 0)  # 과열 강등
        )
        # 추천 이유 구성
        why = []
        strats = "·".join(sorted(p["_strats"]))
        why.append(f"{strats} 시그널")
        if "B" in p["_strats"] and p.get("high_dd") is not None:  # B 설명에 고점대비 낙폭(사용자 2026-06-05)
            why.append(f"고점대비 {p.get('high_dd', 0):+.1f}%")
        if us_hit:
            sec = matched_us_sector(p.get("theme", ""), us_sectors or [])
            why.append(f"미국 {sec} 강세 연동" if sec else "미국 강세테마 연동")
        # 수급은 supply_str(연속 순매수일)로 별도 표기 — pipeline에서 주입
        if p.get("change_pct", 0) >= 3:
            why.append(f"당일 +{p['change_pct']:.1f}% 강세")
        if p.get("_nh", 0) >= 2:
            why.append("신고가권")
        # 테마는 별도 줄로 표기 (reason에서 제외)
        if p.get("endstage"):
            why.append("⚠️끝물주의")
        if is_overheat:
            _tf = "·".join([t for t, on in (("일봉", p.get("overheat")),
                                            ("4시간봉", p.get("overheat_4h"))) if on])
            why.append(f"🔥과열({_tf} 볼밴상단)—강등")
        ranked.append({
            "ticker": tk, "name": p["name"], "price": p["price"],
            "change_pct": p.get("change_pct", 0), "score": round(score, 1),
            "reason": " · ".join(why), "theme": p.get("theme", ""),
            "theme_kind": p.get("theme_kind", ""), "theme_idx": p.get("theme_idx", ""),
            "is_theme_leader": bool(p.get("is_theme_leader")),  # 종목이 테마 주도주인가 (AI 라벨용)
            "is_leading_theme": bool(p.get("is_leading_theme")),  # 종목 테마가 강세 주도테마인가 (주도테마여부 O/X)
            "endstage": bool(p.get("endstage")),
            "stop_price": p.get("stop_price", 0), "stop_pct": p.get("stop_pct", 0),
            "gap20": round(p.get("gap20", 0), 1),  # 20일선 이격도(%)
            "high_dd": round(p.get("high_dd", 0), 1),  # 60일 고점 대비 낙폭(%) — B 표시용
            "overheat": is_overheat,               # 🔥과열(일봉 BB상단돌파 ∪ 4H BB상단음봉) — 강등
            "overheat_4h": bool(p.get("overheat_4h")),
            "vol_x": p.get("vol_x", 0),
            "cross_signal": p.get("cross_signal", ""),  # 5<10 데드+이격 (pullback/correction)
            "lead_strat": p["strategy"].split(".")[0].strip(),  # 대표전략 A/B/C/D
            "strategies": sorted(p["_strats"]),         # 매칭된 전략 전부(중복표기, 사용자 2026-06-05)
            "ai_summary": p.get("ai_summary", ""), "marcap_str": p.get("marcap_str", ""),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    if return_all:  # 전략 스크린용 — 종목당 1개(중복제거)·점수순 전체(사용자 2026-06-05)
        return ranked
    # 전략 다양성 — 같은 전략 최대 2개 (C 독점 방지 → 주도주+눌림목 균형)
    out: list[dict] = []
    strat_cnt: dict[str, int] = {}
    for r in ranked:
        st = r["lead_strat"]
        if strat_cnt.get(st, 0) >= 2:
            continue
        out.append(r)
        strat_cnt[st] = strat_cnt.get(st, 0) + 1
        if len(out) >= 3:
            break
    return out
