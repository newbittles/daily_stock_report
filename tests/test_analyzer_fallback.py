"""AI 실패 시 결정론적 폴백 — 시장 요약 / 보유종목 종합 코멘트."""
from __future__ import annotations

from datetime import datetime

from src.market_report.analyzer import _fallback_summary, _holdings_fallback
from src.market_report.models import IndexQuote, MarketSnapshot


def _idx(market: str, value: float, pct: float) -> IndexQuote:
    return IndexQuote(market=market, value=value, change=0.0, change_pct=pct,
                      volume=0, trade_value=0.0, timestamp=datetime.now())


def test_fallback_summary_kr_has_index_and_theme() -> None:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime.now())
    snap.kospi = _idx("KOSPI", 2700.5, -0.5)
    snap.kosdaq = _idx("KOSDAQ", 850.2, 0.3)
    snap.leading_themes = ["로봇", "반도체", "2차전지"]
    out = _fallback_summary(snap)
    assert "코스피" in out and "2,700.5" in out
    assert "로봇" in out
    # 'AI 분석 불가' 단순 메시지가 아니어야 함
    assert "사용할 수 없습니다" not in out


def test_fallback_summary_us() -> None:
    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2}]
    snap.us_sectors = [{"name": "반도체", "change_pct": 2.0}]
    out = _fallback_summary(snap)
    assert "S&P500" in out and "반도체" in out


def test_holdings_fallback_counts_states() -> None:
    rows = [
        {"state": "HOLD", "name": "A"},
        {"state": "HOLD", "name": "B"},
        {"state": "STOP20", "name": "C"},
    ]
    out = _holdings_fallback(rows)
    assert "보유 3종목" in out
    assert "추세양호(홀딩) 2종목" in out
    assert "분할 대응" in out  # 손절선 이탈 있으면 경고 문구


def test_holdings_fallback_no_risk() -> None:
    rows = [{"state": "HOLD", "name": "A"}, {"state": "ADD", "name": "B"}]
    out = _holdings_fallback(rows)
    assert "추세 이탈 종목은 없습니다" in out
