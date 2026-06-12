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
           "supply_streak": 0.4, "supply_sell": 0.8, "lead_theme": 1.5,
           "end": 6.0, "overheat": 5.0}
# supply_streak(연속 순매수일 가산, 사용자 2026-06-11): 기관/외인이 며칠 연속 순매수인지를 추가 가산
# (기존 supply는 '당일 순매수 여부' 0/1만 봐서 9일연속이나 1일이나 동일했음). 외인·기관 각 최대 7일 반영.
# supply_sell(연속 순매도 패널티, 사용자 2026-06-11): 기관/외인 연속 순매도면 강등(스마트머니 이탈).
#   삼성전기가 이틀 연속 매도인데도 Top3에 들던 문제 — 각 최대 7일, 가산(0.4)보다 강하게(0.8) 강등.
# lead_theme(주도테마 가산, 사용자 2026-06-11): 오늘 주도테마 소속 종목 가산. 기존 KR Top3엔 주도테마
#   직접 가산이 없었음(US만 테마 연동) — 주도주 우대.
# ⚠️ supply_streak/sell·lead_theme 백테스트 미검증 — 사용자 요청 반영. 추후 backtest_top3로 튜닝 권장.
# B(주도주 눌림목) 가중치 상향 2.0→2.8 (사용자 2026-06-05: 3일 조정 후 반등 눌림목 선호, 삼성전기 사례).
# ※ 원래 P4 백테스트 최적은 B=2.0 — 사용자 선호 반영(추후 백테스트로 재튜닝 가능).
# A(수렴 후 대세상승 시작) 가중치 상향 1.5→2.0 (사용자 2026-06-10: 후성 사례 — A 급등 초입 종목이
# Top3에 더 들어오도록. 단 A는 백테스트 신뢰도가 낮아 D(2.5)보다는 낮게 유지. 추후 백테스트 재튜닝).
_STRAT_W = {"D. 추세 반전": 2.5, "C. 대세 정배열 추세추종": 3.0,
            "B. 주도주 20일선 눌림목": 2.8, "A. 수렴 후 대세상승 시작": 2.0}

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
                us_sectors: list[dict] | None = None, return_all: bool = False,
                supply_streaks: dict[str, dict] | None = None,
                b_pullback_bonus: float = 0.0, overheat_4h_penalty: float = 0.0,
                exclude_tickers: set[str] | None = None, limit: int = 3,
                diversity_max: int = 2, min_b: int = 0) -> list[dict]:
    """A/B/C/D 스크린 결과 → 종합점수 상위 N종목(기본 3). 추천 이유 동반.

    foreign_buy/inst_buy: 외국인/기관 순매수 상위 종목코드 집합(수급 가산).
    us_keywords/w_us: 미국 강세테마 연동 가중(us_morning 시초 Top3 전용. 국장은 w_us=0).
    us_sectors: 추천이유에 연결된 미국 섹터명 표기용.
    b_pullback_bonus: B(주도주 20일선 눌림목) 종목 추가 가산 — 종가베팅 5선 전용(기본 0=Top3 무변경).
    overheat_4h_penalty: 4시간봉 볼밴 상단(overheat_4h) 종목 추가 강등 — 종가베팅 5선 전용(기본 0).
    exclude_tickers: 최종 선정에서 제외할 종목코드(예: Top3와 중복 제거). return_all에는 미적용.
    limit/diversity_max: 최종 선정 개수·동일전략 최대 개수(기본 3·2 = 기존 Top3 동작).
    min_b: B(주도주 20일선 눌림목) 매칭 종목 의무 포함 최소 개수(가능한 만큼) — 종가베팅 5선 전용(기본 0).
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
    _streaks = supply_streaks or {}
    for tk, p in by_ticker.items():
        supply = (1 if tk in fb else 0) + (1 if tk in ib else 0)  # 0~2
        # 연속 순매수일 가산(사용자 2026-06-11): 기관/외인 각 최대 7일까지 반영(0~14).
        _ss = _streaks.get(tk) or {}
        _streak_pts = min(int(_ss.get("orgn", 0) or 0), 7) + min(int(_ss.get("frgn", 0) or 0), 7)
        # 연속 순매도일 패널티(사용자 2026-06-11): 기관/외인 연속 순매도 = 스마트머니 이탈 → 강등.
        _sell_pts = min(int(_ss.get("orgn_sell", 0) or 0), 7) + min(int(_ss.get("frgn_sell", 0) or 0), 7)
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
            + w.get("supply_streak", 0.0) * _streak_pts  # 연속 순매수일 가산(사용자 2026-06-11)
            - w.get("supply_sell", 0.0) * _sell_pts      # 연속 순매도 패널티(사용자 2026-06-11)
            + w.get("lead_theme", 0.0) * (1 if p.get("is_leading_theme") else 0)  # 주도테마 가산(2026-06-11)
            + w_us * (1 if us_hit else 0)
            - w["end"] * (1 if p.get("endstage") else 0)
            - w["overheat"] * (1 if is_overheat else 0)  # 과열 강등
            # 종가베팅 5선 전용 가감(사용자 2026-06-12) — Top3는 두 값 0이라 무영향
            + b_pullback_bonus * (1 if "B" in p["_strats"] else 0)        # B 눌림목 가산
            - overheat_4h_penalty * (1 if p.get("overheat_4h") else 0)    # 4H 볼밴상단 추가 강등
        )
        # 추천 이유 구성
        why = []
        strats = "·".join(sorted(p["_strats"]))
        why.append(f"{strats} 시그널")
        if "B" in p["_strats"] and p.get("high_dd") is not None:  # B 설명에 고점대비 낙폭(사용자 2026-06-05)
            why.append(f"고점대비 {p.get('high_dd', 0):+.1f}%")
        if b_pullback_bonus and "B" in p["_strats"]:  # 종가베팅 5선 B 눌림목 우대(사용자 2026-06-12)
            why.append("🅱️눌림목 우대(+가산)")
        if overheat_4h_penalty and p.get("overheat_4h"):  # 4H 볼밴상단 추가 강등(사용자 2026-06-12)
            why.append("🔻4시간봉 볼밴상단(−패널티)")
        if us_hit:
            sec = matched_us_sector(p.get("theme", ""), us_sectors or [])
            why.append(f"미국 {sec} 강세 연동" if sec else "미국 강세테마 연동")
        if p.get("is_leading_theme"):
            why.append("🚀주도테마")  # 주도테마 가산(사용자 2026-06-11)
        if _sell_pts >= 2:  # 기관/외인 연속 순매도 경고(점수 강등됨, 사용자 2026-06-11)
            why.append("⚠️수급 이탈(기관·외인 연속 순매도)")
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
    _excl = exclude_tickers or set()
    # 전략 다양성 — 같은 전략 최대 diversity_max개 (C 독점 방지 → 주도주+눌림목 균형)
    out: list[dict] = []
    chosen: set[str] = set()
    strat_cnt: dict[str, int] = {}

    def _try_add(r: dict) -> bool:
        if r["ticker"] in _excl or r["ticker"] in chosen or len(out) >= limit:
            return False
        st = r["lead_strat"]
        if strat_cnt.get(st, 0) >= diversity_max:
            return False
        out.append(r)
        chosen.add(r["ticker"])
        strat_cnt[st] = strat_cnt.get(st, 0) + 1
        return True

    # 1) B 눌림목 의무 포함 — 점수순 B(매칭전략에 B 포함)를 min_b개까지 먼저(사용자 2026-06-12)
    if min_b > 0:
        b_added = 0
        for r in ranked:
            if b_added >= min_b or len(out) >= limit:
                break
            if "B" in (r.get("strategies") or []) and _try_add(r):
                b_added += 1
    # 2) 나머지 점수순 채우기
    for r in ranked:
        if len(out) >= limit:
            break
        _try_add(r)
    return out


# ── 종가베팅 5선 (사용자 2026-06-12) ──────────────────────────────────────────
# Top3와 별개로, '마감 직전 매수' 관점에 맞춰 B(주도주 20일선 눌림목)를 추가 가산하고
# 4시간봉 볼밴 상단(overheat_4h, 단기 고점)을 추가 강등해 과열 추격을 피한다.
# ⚠️ 두 값은 백테스트 미검증(사용자 요청 반영) — 추후 backtest_top3로 튜닝 권장.
CB_B_PULLBACK_BONUS = 3.0      # B 눌림목 가산(점수). 참고: 기존 B strat 점수 = 2.8×3.0 = 8.4
CB_OVERHEAT_4H_PENALTY = 4.0   # 4H 볼밴상단 추가 강등(점수). 기존 통합 overheat 강등(5.0)에 더해짐


CB_MIN_B = 2  # 종가베팅 5선에 B 눌림목 의무 포함 최소 개수(사용자 2026-06-12). 풀에 B가 부족하면 가능한 만큼.


def select_closing_bets(screen_picks: list[dict], foreign_buy: set[str] | None = None,
                        inst_buy: set[str] | None = None,
                        supply_streaks: dict[str, dict] | None = None,
                        exclude_tickers: set[str] | None = None,
                        limit: int = 5, min_b: int = CB_MIN_B) -> list[dict]:
    """종가베팅 5선 — Top3와 동일 점수 체계 + B 눌림목 가산 + 4H 볼밴상단 패널티.

    B(주도주 20일선 눌림목)는 최소 min_b종목 의무 포함(가능한 만큼). Top3와 중복을
    피하려면 exclude_tickers에 Top3 종목코드를 넘긴다. 결과 dict는 select_top3와 동일 스키마.
    diversity_max=3 — '2 이상' 요구에 맞춰 B가 최대 3까지 들어올 수 있게 허용(사용자 2026-06-12)."""
    return select_top3(
        screen_picks, foreign_buy=foreign_buy, inst_buy=inst_buy,
        supply_streaks=supply_streaks,
        b_pullback_bonus=CB_B_PULLBACK_BONUS,
        overheat_4h_penalty=CB_OVERHEAT_4H_PENALTY,
        exclude_tickers=exclude_tickers, limit=limit, diversity_max=3, min_b=min_b,
    )
