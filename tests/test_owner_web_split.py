"""웹리포트 유저별 분리 — 보유종목은 오너판(audience=owner)에만, 공개판엔 제외(사용자 2026-06-14)."""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.render import _env, report_path, report_url_rel


def _snap() -> MarketSnapshot:
    s = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 14, 16, 0))
    s.kospi = IndexQuote(market="KOSPI", value=7800.0, change=0, change_pct=0.5,
                         volume=0, trade_value=0.0, timestamp=datetime.now())
    s.holdings_status = [{"ticker": "042660", "name": "한화오션", "price": 112000,
                          "profit_rate": 8.5, "reason": "20MA 위 홀드"}]
    return s


def test_web_holdings_owner_only() -> None:
    tmpl = _env().get_template("report.html")
    owner = tmpl.render(title="t", snap=_snap(), audience="owner")
    public = tmpl.render(title="t", snap=_snap(), audience="public")
    assert "보유종목 상태" in owner and "한화오션" in owner
    assert "보유종목 상태" not in public and "한화오션" not in public


def test_web_default_audience_hides_holdings() -> None:
    """audience 미지정(기본 public) 시 보유종목 비노출(안전 기본값)."""
    html = _env().get_template("report.html").render(title="t", snap=_snap())
    assert "보유종목 상태" not in html


def test_owner_report_path_suffix() -> None:
    s = _snap()
    assert report_path(s, owner=False).name == "2026-06-14-post.html"
    assert report_path(s, owner=True).name == "2026-06-14-post-owner.html"
    assert report_url_rel(s, owner=True).endswith("-post-owner.html")
