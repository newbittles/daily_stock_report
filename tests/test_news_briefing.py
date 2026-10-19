"""텔레그램 주요뉴스 브리핑(사용자 2026-10-20, CLAUDE.md §5-9) — 4종 모드는 뉴스 꼬리표만."""
from datetime import datetime

import pytest

from src.market_report.analyzer import (
    _fallback_key_news,
    _news_candidates,
    _parse_key_news,
)
from src.market_report.models import IndexQuote, MarketSnapshot, NewsItem
from src.market_report.telegram_notify import BRIEFING_MODES, _build_report_text


def _snap(mode: str) -> MarketSnapshot:
    s = MarketSnapshot(mode=mode, generated_at=datetime(2026, 10, 20, 11, 40))  # type: ignore[arg-type]
    s.market_news = [NewsItem(title="SK하이닉스 3분기 영업익 시장 예상 크게 밑돌아", url="https://n.news.naver.com/a/1",
                              source="한경", published_at="")]
    s.us_news = [{"title": "Tesla unveils Roadster", "title_ko": "테슬라, 로드스터 공개",
                  "url": "https://finance.yahoo.com/x"}]
    return s


def test_candidates_by_market() -> None:
    assert _news_candidates(_snap("midday"))[0]["title"].startswith("SK하이닉스")
    us = _news_candidates(_snap("us_morning"))
    assert us == [{"title": "테슬라, 로드스터 공개", "url": "https://finance.yahoo.com/x"}]


def test_parse_keeps_source_url_and_drops_bad_items() -> None:
    cands = [{"title": "a", "url": "u0"}, {"title": "b", "url": "u1"}]
    data = {"items": [{"i": 1, "tag": "하이닉스 실적쇼크"}, {"i": 9, "tag": "지어낸 뉴스"},
                      {"i": 1, "tag": "중복"}, {"i": 0, "tag": ""}, {"i": "x", "tag": "y"}]}
    assert _parse_key_news(data, cands) == [{"tag": "하이닉스 실적쇼크", "url": "u1"}]


def test_fallback_clips_headlines() -> None:
    out = _fallback_key_news([{"title": "가" * 50, "url": "u"}])
    assert len(out[0]["tag"]) == 30 and out[0]["tag"].endswith("…")


@pytest.mark.parametrize("mode", BRIEFING_MODES)
def test_briefing_has_news_only_no_indices(mode: str) -> None:
    s = _snap(mode)
    s.kospi = IndexQuote(market="KOSPI", value=3000.0, change=10.0, change_pct=0.3,
                         volume=0, trade_value=0.0, timestamp=datetime(2026, 10, 20))
    s.key_news = [{"tag": "하이닉스 [실적]쇼크", "url": "https://n.news.naver.com/a/1"}]
    msg = _build_report_text(s, private=True)
    assert "📰 *주요뉴스*" in msg
    assert "[하이닉스 (실적)쇼크](https://n.news.naver.com/a/1)" in msg
    assert "코스피" not in msg and "투자자 수급" not in msg and "보유종목" not in msg
    assert "전체 리포트 보기" in msg


def test_non_briefing_modes_unchanged() -> None:
    s = _snap("pre_close")
    s.key_news = [{"tag": "x", "url": "https://a"}]
    assert "주요뉴스" not in _build_report_text(s, private=False)


def test_fallback_cleans_and_dedupes_same_story() -> None:
    cands = [
        {"title": "'다우키움·CJ 컨소시엄' 중앙일보 품는다", "url": "u0"},
        {"title": "'중앙일보 인수' 우선협상대상자에 다우키움·CJ 컨소시엄(종합)", "url": "u1"},
        {"title": "코스피 바닥인가 했더니…투매 경고 [분석+]", "url": "u2"},
    ]
    out = _fallback_key_news(cands)
    assert [o["url"] for o in out] == ["u0", "u2"]
    assert out[0]["tag"] == "다우키움·CJ 컨소시엄 중앙일보 품는다"
    assert "[분석+]" not in out[1]["tag"]
