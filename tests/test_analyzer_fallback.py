"""AI 실패 시 결정론적 폴백 — 시장 요약 / 보유종목 종합 코멘트."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.market_report.analyzer import (
    _fallback_summary,
    _holdings_fallback,
    _move_label,
    _summary_target_line,
)
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


# ─── 종목 AI요약 방향 인식 (왜 올랐나 / 왜 하락했나) ──────────────────────────
@pytest.mark.parametrize("chg,cross,expected", [
    (3.5, None, "▲상승"),
    (-4.2, None, "▼하락"),
    (0.0, None, "보합"),
    (1.0, "CORRECTION", "▼조정(상승 후 하락 전환)"),   # 잘 오르다 꺾인 종목
    (2.0, "PULLBACK", "▲상승"),                        # 단기눌림은 상승 추세 유지
])
def test_move_label(chg, cross, expected) -> None:
    assert _move_label(chg, cross) == expected


def test_summary_target_line_down_stock_marks_decline() -> None:
    """하락 종목 줄에 ▼하락이 들어가 AI가 하락 사유를 쓰도록 유도."""
    line = _summary_target_line("005930", {"name": "삼성전자", "change_pct": -3.1,
                                           "theme": "반도체"})
    assert "▼하락" in line
    assert "-3.1%" in line and "삼성전자" in line


def test_summary_target_line_correction_stock() -> None:
    """'상승 후 하락 전환'(CORRECTION) 종목은 양수 등락이어도 조정으로 표기."""
    line = _summary_target_line("000660", {"name": "SK하이닉스", "change_pct": 0.5,
                                           "cross_signal": "CORRECTION"})
    assert "조정(상승 후 하락 전환)" in line


def test_summary_target_line_today_pct_fallback() -> None:
    """전날Top3·종가베팅은 change_pct가 없고 today_pct가 오늘 등락 → 폴백 사용(장중 #2026-06-10)."""
    line = _summary_target_line("093370", {"name": "후성", "today_pct": -2.7})
    assert "-2.7%" in line and "▼하락" in line and "후성" in line


def test_summary_target_line_no_pct_defaults_zero() -> None:
    """등락 키가 전혀 없으면(보유종목 등) 0% 보합으로 안전 처리(크래시 방지)."""
    line = _summary_target_line("005930", {"name": "삼성전자"})
    assert "+0.0%" in line and "보합" in line
