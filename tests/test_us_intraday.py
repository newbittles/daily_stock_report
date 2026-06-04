"""미국장 장중 리포트(us_intraday) — 가격 포맷·뉴스 위치·텔레그램 헤더 검증(오프라인).

us_px 매크로 모드 분기(장중=실시간만)·뉴스 최하단·텔레그램 뉴스 제외·장중 잠정 라벨.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.market_report import render as render_mod
from src.market_report.models import MarketSnapshot
from src.market_report.render import render_report
from src.market_report.telegram_notify import _format_us_morning_summary


@pytest.fixture(autouse=True)
def _isolate_render(tmp_path, monkeypatch):
    """render 출력(리포트 HTML·_history·index)을 tmp로 격리 — 레포 docs/ 오염 방지."""
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(render_mod, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(render_mod, "REPORTS_DIR", reports)
    monkeypatch.setattr(render_mod, "INDEX_FILE", tmp_path / "index.html")
    monkeypatch.setattr(render_mod, "HISTORY_FILE", reports / "_history.json")


def _snap(mode: str) -> MarketSnapshot:
    s = MarketSnapshot(mode=mode, generated_at=datetime(2026, 6, 5, 23, 50))
    s.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2}]
    s.us_sectors = [{"name": "반도체", "change_pct": 2.1}]
    s.summary = "테스트 요약"
    s.us_top3 = [{"symbol": "MU", "name": "마이크론", "price": 120.5, "change_pct": 2.3,
                  "intraday": True, "intraday_price": 122.0, "close_pct": 2.3,
                  "sector": "반도체", "reason": "x"}]
    s.us_news = [{"title": "테스트 뉴스", "source": "Reuters"}]
    return s


def test_us_intraday_price_is_realtime_only() -> None:
    """장중 리포트 가격 = '장중 $현재가 (장중%)' — 전일/종가 표기 없음(사용자 2026-06-05)."""
    html = render_report(_snap("us_intraday")).read_text(encoding="utf-8")
    assert "장중 $122.00" in html          # 장중 현재가
    assert html.count("종가 $") == 0       # 마감 포맷 아님
    assert html.count("(전일 ") == 0       # 전일 등락률 없음


def test_us_news_moved_to_bottom() -> None:
    """미국 뉴스 = 웹 리포트 최하단(면책 직전)."""
    html = render_report(_snap("us_intraday")).read_text(encoding="utf-8")
    inews = html.find("미국 시장 뉴스")
    idis = html.find("공개 데이터 기반")
    assert 0 < inews < idis


def test_us_intraday_telegram_header_and_no_news() -> None:
    """텔레그램: 장중 잠정 라벨 + 미국 뉴스 제외(웹으로)."""
    tg = _format_us_morning_summary(_snap("us_intraday"))
    assert "미국장 장중 리포트" in tg
    assert "장중 잠정" in tg
    assert "미국 시장 뉴스" not in tg


def test_us_morning_telegram_excludes_news() -> None:
    """마감(us_morning) 텔레그램도 뉴스 제외(사용자 2026-06-05 추가요청)."""
    tg = _format_us_morning_summary(_snap("us_morning"))
    assert "미국 시장 뉴스" not in tg
