"""AI 수급 요약 — 연속 순매수/매도·전일/전주대비 결정론 통계 검증(사용자 #313/#316)."""
from __future__ import annotations

from datetime import datetime

from src.market_report.flows_history import compute_flow_stats
from src.market_report.models import MarketSnapshot
from src.market_report.telegram_notify import _format_flows_summary


def test_compute_flow_stats_streak_and_deltas() -> None:
    series = [  # 최신순
        {"date": "20260605", "kospi": {"foreign": 100, "institution": -50, "personal": -50}},
        {"date": "20260604", "kospi": {"foreign": 80, "institution": -30, "personal": -50}},
        {"date": "20260603", "kospi": {"foreign": 60, "institution": 20, "personal": -80}},
        {"date": "20260602", "kospi": {"foreign": -10, "institution": 10, "personal": 0}},
        {"date": "20260530", "kospi": {"foreign": 50, "institution": -5, "personal": -45}},
        {"date": "20260529", "kospi": {"foreign": 30, "institution": -5, "personal": -25}},
    ]
    st = compute_flow_stats(series)
    f = st["kospi_foreign"]
    assert f["today"] == 100
    assert f["streak"] == 3            # 100,80,60 연속 순매수(4일째 -10에서 끊김)
    assert f["prev"] == 80
    assert f["week_ago"] == 30         # 5거래일 전
    assert f["week_sum"] == 280        # 최근5일 합
    inst = st["kospi_institution"]
    assert inst["streak"] == -2        # -50,-30 연속 순매도(3일째 +20에서 끊김)


def test_format_flows_summary_empty() -> None:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 5, 16, 0))
    assert _format_flows_summary(snap) == []
    snap.flows_summary = "외국인 3일 연속 순매수"
    assert "수급 요약" in "\n".join(_format_flows_summary(snap))
