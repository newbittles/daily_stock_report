"""한국장 프리/장초 리포트 — 종가베팅 후보 영속화·로드 (#404)."""
from __future__ import annotations

from src.market_report.top3_status import find_prev_candidates
from src.trading.top3_bridge import persist_candidates


def test_candidate_persist_and_find_prev(tmp_path) -> None:
    picks = [
        {"ticker": "055550", "name": "신한지주", "theme": "금융", "rationale": "외인매수", "risk": "금리"},
        {"ticker": "005930", "name": "삼성전자", "rationale": "반등"},
    ]
    persist_candidates(picks, "2026-06-05", base_dir=tmp_path)
    # 이전 거래일분 로드 (today=06-08 → 06-05 선택)
    res = find_prev_candidates("2026-06-08", base_dir=tmp_path)
    assert res is not None
    date, loaded = res
    assert date == "2026-06-05"
    assert loaded[0]["ticker"] == "055550"
    assert loaded[0]["name"] == "신한지주"


def test_find_prev_candidates_excludes_today_and_future(tmp_path) -> None:
    persist_candidates([{"ticker": "000660", "name": "SK하이닉스"}], "2026-06-08", base_dir=tmp_path)
    # today 이전만 → 06-08 자신은 제외 → None
    assert find_prev_candidates("2026-06-08", base_dir=tmp_path) is None


def test_find_prev_candidates_none_when_empty(tmp_path) -> None:
    assert find_prev_candidates("2026-06-08", base_dir=tmp_path) is None
