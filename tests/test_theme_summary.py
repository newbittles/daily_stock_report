"""강세 테마별 '왜 올랐나' 해설 — 그레이스풀 동작 + 리포트 렌더."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.market_report.analyzer import summarize_themes
from src.market_report.models import IndexQuote, MarketSnapshot, ThemeRank
from src.market_report.telegram_notify import _format_post_summary


def _snap_with_theme(desc: str = "") -> MarketSnapshot:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 4, 16, 30))
    snap.kospi = IndexQuote(market="KOSPI", value=8801.5, change=0, change_pct=0.5,
                            volume=0, trade_value=0, timestamp=datetime.now())
    snap.top_themes = [
        ThemeRank(rank=1, name="보험", change_pct=4.2,
                  leading_stocks=["삼성생명", "한화생명"], description=desc),
        ThemeRank(rank=2, name="반도체", change_pct=2.1, leading_stocks=["삼성전자"]),
    ]
    return snap


async def test_summarize_themes_no_key_is_graceful(monkeypatch):
    """Gemini 키 없으면 description을 건드리지 않고 조용히 반환(예외 없음)."""
    import src.market_report.analyzer as A

    class _S:
        gemini_api_key = ""
    monkeypatch.setattr(A, "get_settings", lambda: _S())
    snap = _snap_with_theme(desc="기존값")
    await summarize_themes(snap)               # 예외 없이 통과
    assert snap.top_themes[0].description == "기존값"  # 변경 없음


def test_post_summary_renders_theme_why():
    """테마 해설(description)이 텔레그램 강세테마에 💡로 표시된다."""
    snap = _snap_with_theme(desc="정책 기대감에 보험 종목 일제 강세")
    msg = _format_post_summary(snap)
    assert "보험" in msg
    assert "💡 정책 기대감에 보험 종목 일제 강세" in msg


def test_post_summary_no_why_when_empty():
    """해설이 없으면 💡 줄을 만들지 않는다(테마명만)."""
    snap = _snap_with_theme(desc="")
    msg = _format_post_summary(snap)
    # 보험 테마 줄은 있되 그 아래 💡 해설 줄은 없음
    assert "  · 보험 +4.20%" in msg
    lines = msg.split("\n")
    bo_idx = next(i for i, ln in enumerate(lines) if "· 보험" in ln)
    assert "💡" not in lines[bo_idx + 1]
