"""종가베팅 5선 — 점수기반 선정(B 눌림목 가산 + 4H 볼밴상단 패널티). 사용자 2026-06-12.

select_top3와 동일 점수 체계를 재사용하되, 종가베팅 전용으로:
- B(주도주 20일선 눌림목) 추가 가산 → 마감 직전 눌림목 매수 우대
- 4시간봉 볼밴 상단(overheat_4h) 추가 강등 → 단기 고점 추격 방지
Top3(기본 호출)는 두 가산/패널티가 0이라 기존 동작이 그대로 유지되는지도 함께 검증.
"""
from __future__ import annotations

from src.market_report.top3 import (
    CB_B_PULLBACK_BONUS,
    select_closing_bets,
    select_top3,
)


def _pick(ticker: str, name: str, strategy: str, **extra) -> dict:
    return {"ticker": ticker, "name": name, "price": 1000.0, "strategy": strategy,
            "change_pct": 1.0, "gap20": 5.0, "_liq": 5.0, "_nh": 0.0, **extra}


_B = "B. 주도주 20일선 눌림목"
_C = "C. 대세 정배열 추세추종"
_D = "D. 추세 반전"


def test_b_pullback_bonus_lifts_b_above_c() -> None:
    """B 가산으로 B 종목이 동급 C 종목보다 위로(기본 strat점수는 C가 더 높음에도)."""
    picks = [_pick("CCC", "씨", _C), _pick("BBB", "비", _B)]
    # 기본 Top3(가산 없음)에선 C(3.0)가 B(2.8)보다 위
    base = select_top3(picks)
    assert base[0]["ticker"] == "CCC"
    # 종가베팅: B 가산(+3.0)으로 B가 위로 역전
    out = select_closing_bets(picks)
    assert out[0]["ticker"] == "BBB"
    bbb = next(o for o in out if o["ticker"] == "BBB")
    assert "🅱️눌림목 우대(+가산)" in bbb["reason"]


def test_overheat_4h_extra_penalty_demotes() -> None:
    """4시간봉 볼밴상단(overheat_4h) 종목은 종가베팅에서 추가 강등 + 패널티 표기."""
    picks = [_pick("AAA", "정상", _C), _pick("HOT", "4H상단", _C, overheat_4h=True)]
    out = select_closing_bets(picks)
    assert out[0]["ticker"] == "AAA"
    hot = next(o for o in out if o["ticker"] == "HOT")
    assert hot["overheat_4h"] is True
    assert "🔻4시간봉 볼밴상단(−패널티)" in hot["reason"]


def test_exclude_tickers_removes_top3_overlap() -> None:
    """exclude_tickers(=Top3 종목)는 5선에서 제외 → 탑3와 중복 없음."""
    picks = [_pick("T1", "탑1", _C), _pick("B1", "비1", _B), _pick("B2", "비2", _B)]
    out = select_closing_bets(picks, exclude_tickers={"T1"})
    assert "T1" not in {o["ticker"] for o in out}
    assert {"B1", "B2"} <= {o["ticker"] for o in out}


def test_limit_caps_count() -> None:
    """limit=5 — 6종목 풀이어도 5개만."""
    picks = [_pick(f"S{i}", f"종목{i}", _C if i % 2 else _B, _liq=5.0 + i) for i in range(6)]
    out = select_closing_bets(picks, limit=5)
    assert len(out) <= 5


def test_top3_default_has_no_closing_bet_markers() -> None:
    """기본 select_top3(Top3)는 B 가산/4H 패널티 마커가 붙지 않음(동작 불변)."""
    picks = [_pick("BBB", "비", _B, overheat_4h=True)]
    out = select_top3(picks)
    assert "🅱️눌림목 우대(+가산)" not in out[0]["reason"]
    assert "🔻4시간봉 볼밴상단(−패널티)" not in out[0]["reason"]


def test_min_b_mandatory_inclusion() -> None:
    """B 눌림목이 점수 낮아 top5 밖이어도 min_b=2면 의무 포함(사용자 2026-06-12)."""
    high = [_pick("C0", "씨0", _C, _liq=9.0), _pick("C1", "씨1", _C, _liq=9.0),
            _pick("C2", "씨2", _C, _liq=9.0), _pick("D0", "디0", _D, _liq=9.0),
            _pick("D1", "디1", _D, _liq=9.0)]
    lowb = [_pick("B1", "비1", _B, _liq=1.0), _pick("B2", "비2", _B, _liq=1.0)]
    picks = high + lowb
    # min_b=0 → 고득점 비B가 5칸 모두 차지(B 0개)
    none_forced = select_closing_bets(picks, limit=5, min_b=0)
    assert sum(1 for o in none_forced if "B" in o["strategies"]) == 0
    # min_b=2 → 저득점 B라도 최소 2종목 의무 포함
    forced = select_closing_bets(picks, limit=5, min_b=2)
    assert sum(1 for o in forced if "B" in o["strategies"]) >= 2
    assert len(forced) == 5


def test_bonus_constant_sane() -> None:
    """가산 상수는 양수(튜닝 가능). 0이면 의미 없음."""
    assert CB_B_PULLBACK_BONUS > 0
