"""Top3 수급 연속일 가산 (사용자 2026-06-11) — 기관/외인 연속 순매수일이 길수록 점수↑."""
from __future__ import annotations

from src.market_report.top3 import select_top3


def _pick(tk: str) -> dict:
    return {"ticker": tk, "name": tk, "strategy": "A. 수렴 후 대세상승 시작",
            "price": 1000.0, "change_pct": 5.0}


def test_supply_streak_boosts_score() -> None:
    """동일 조건에서 기관 9일 연속 순매수 종목이 1일짜리보다 위로 랭크된다."""
    picks = [_pick("A"), _pick("B")]
    out = select_top3(
        picks, foreign_buy=set(), inst_buy={"A", "B"},
        supply_streaks={"A": {"orgn": 9, "frgn": 0}, "B": {"orgn": 1, "frgn": 0}},
        return_all=True,
    )
    assert out[0]["ticker"] == "A"        # 연속일 많은 쪽이 종합점수 상위
    assert out[0]["score"] > out[1]["score"]


def test_supply_streak_optional_backcompat() -> None:
    """supply_streaks 미전달 시 기존 동작(연속 가산 0, 에러 없음)."""
    out = select_top3([_pick("A")], inst_buy={"A"}, return_all=True)
    assert out and out[0]["ticker"] == "A"
