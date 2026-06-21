"""Top3·US Top3 추천 카드에 종가베팅과 동일한 차트(chart_url) 부착·렌더 검증 (사용자 2026-06-18).

- pipeline._render_picks_charts: KR='ticker'/US='symbol' 키로 chart_url 세팅(차트 렌더는 모킹).
- 템플릿: top3 카드(KR)·us_top3 카드(US)가 chart_url 있을 때만 .pick-chart <img> 렌더.
"""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.render import render_report


def _idx(market: str, value: float, pct: float) -> IndexQuote:
    return IndexQuote(market=market, value=value, change=0.0, change_pct=pct,
                      volume=0, trade_value=0.0, timestamp=datetime.now())


async def test_render_picks_charts_uses_ticker_key(monkeypatch) -> None:
    """KR은 ticker, US는 symbol 키로 차트 생성 + chart_url 세팅. 키 없으면 chart_url=''."""
    from src.market_report import pipeline as P

    calls: list[tuple[str, str]] = []

    def fake_safe(ticker: str, name: str, date: str) -> str:
        calls.append((ticker, name))
        return f"charts/{date}-{ticker}.png"

    monkeypatch.setattr(P, "_render_chart_safe", fake_safe)

    kr = [{"ticker": "005930", "name": "삼성전자"}]
    us = [{"symbol": "NVDA", "name": "엔비디아"}, {"symbol": "", "name": "결측"}]
    await P._render_picks_charts(kr, "2026-06-18", "ticker")
    await P._render_picks_charts(us, "2026-06-18", "symbol")

    assert kr[0]["chart_url"] == "charts/2026-06-18-005930.png"
    assert us[0]["chart_url"] == "charts/2026-06-18-NVDA.png"
    assert us[1]["chart_url"] == ""                       # symbol 없으면 빈 값
    assert ("005930", "삼성전자") in calls and ("NVDA", "엔비디아") in calls
    assert ("", "결측") not in calls                       # 결측은 렌더 시도 안 함


def test_kr_top3_renders_chart_when_url_set() -> None:
    s = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 18, 14, 50))
    s.kospi = _idx("KOSPI", 7800.0, 0.5)
    s.kosdaq = _idx("KOSDAQ", 1029.0, 0.3)
    s.top3 = [{"ticker": "005930", "name": "삼성전자", "price": 70000, "change_pct": 1.5,
               "reason": "주도주 눌림목", "chart_url": "charts/2026-06-18-005930.png"}]
    html = render_report(s).read_text(encoding="utf-8")
    assert "charts/2026-06-18-005930.png" in html
    assert 'class="pick-chart"' in html


def test_kr_top3_no_chart_when_url_absent() -> None:
    s = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 18, 14, 50))
    s.kospi = _idx("KOSPI", 7800.0, 0.5)
    s.kosdaq = _idx("KOSDAQ", 1029.0, 0.3)
    s.top3 = [{"ticker": "005930", "name": "삼성전자", "price": 70000, "change_pct": 1.5,
               "reason": "주도주 눌림목"}]
    html = render_report(s).read_text(encoding="utf-8")
    assert "삼성전자" in html                              # 카드는 그대로
    assert "2026-06-18-005930.png" not in html             # 차트 파일은 없음


def test_us_top3_renders_chart_when_url_set() -> None:
    s = MarketSnapshot(mode="us_morning", generated_at=datetime(2026, 6, 18, 7, 30))
    s.us_indices = [{"name": "나스닥", "price": 17000.0, "change_pct": 1.0}]
    s.us_top3 = [{"symbol": "NVDA", "name": "엔비디아", "price": 1200.0, "change_pct": 3.0,
                  "sector": "IT", "reason": "신고가", "chart_url": "charts/2026-06-18-NVDA.png"}]
    html = render_report(s).read_text(encoding="utf-8")
    assert "charts/2026-06-18-NVDA.png" in html
    assert 'class="pick-chart"' in html
