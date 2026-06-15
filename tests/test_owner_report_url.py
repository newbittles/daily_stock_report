"""Regression — 오너 전용 리포트 URL은 오너판 파일이 생성될 때만 사용해야 한다.

버그(2026-06-15): render는 '보유종목 있을 때만' 오너판 HTML을 생성(render.py)하는데,
telegram_notify는 오너에게 '항상' 오너 URL을 보냄 → 보유종목이 비면 오너 파일이 없는데
링크는 오너 주소 → 클릭 시 GitHub Pages 404. (per-user 분리 17c765a 부작용)

계약: report_url(owner=True)는 오너판 파일이 실제 생성될 때(보유종목 있음)만 토큰 접미사를
붙이고, 아니면 공개 URL로 폴백한다.
"""
from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.publisher import report_url


def _snap(holdings: list[dict]) -> MarketSnapshot:
    return MarketSnapshot(
        mode="midday", generated_at=datetime(2026, 6, 15, 12, 0),
        holdings_status=holdings,
    )


def test_owner_url_falls_back_to_public_when_no_holdings():
    """보유종목 없음 → 오너판 파일 미생성 → 오너 URL == 공개 URL (404 방지)."""
    snap = _snap([])
    assert report_url(snap, owner=True) == report_url(snap, owner=False)


def test_owner_url_distinct_when_holdings_present():
    """보유종목 있음 → 오너판 파일 생성 → 오너 URL은 토큰 접미사로 구분."""
    snap = _snap([{"name": "삼성전자", "ticker": "005930", "state": "보유"}])
    assert report_url(snap, owner=True) != report_url(snap, owner=False)
    assert report_url(snap, owner=True).endswith(".html")
