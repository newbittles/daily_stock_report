"""us_morning 리포트 — 종목 정보가 미국 종목만(한국 종목 아님)인지 검증."""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.telegram_notify import _format_us_morning_summary


def _us_snap() -> MarketSnapshot:
    snap = MarketSnapshot(mode="us_morning", generated_at=datetime(2026, 6, 4, 7, 30))
    snap.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2},
                       {"name": "나스닥", "price": 17000.0, "change_pct": 1.5}]
    snap.us_sectors = [{"name": "반도체", "change_pct": 2.1}]
    snap.theme_commentary = "미국 반도체 강세 → 한국 반도체 주목."
    snap.us_top3 = [
        {"symbol": "NVDA", "name": "NVIDIA", "price": 1200.5, "change_pct": 3.2,
         "sector": "Information Technology", "reason": "60일 신고가", "cross_signal": "PULLBACK"},
    ]
    snap.us_screen_groups = [
        {"label": "📈 C 추세추종", "initial": "C", "picks": [
            {"symbol": "AVGO", "name": "Broadcom", "price": 1500.0, "change_pct": 2.1,
             "sector": "IT", "reason": "추세", "cross_signal": None}]},
    ]
    return snap


def test_us_morning_shows_us_stocks() -> None:
    msg = _format_us_morning_summary(_us_snap())
    assert "미국 추천 Top 3" in msg
    assert "NVDA" in msg and "NVIDIA" in msg
    assert "$1,200.5" in msg  # 달러 표기
    assert "미국 종목 스크리닝" in msg
    assert "AVGO" in msg
    # 한국장 연결성 코멘트는 유지
    assert "한국장 시사점" in msg


def test_us_morning_no_korean_stock_links() -> None:
    """미국 종목만 — 한국 네이버 종목 링크가 없어야 함."""
    msg = _format_us_morning_summary(_us_snap())
    assert "finance.naver.com/item" not in msg
    assert "시초 매수" not in msg  # 구 한국 시초 Top3 문구 제거됨
