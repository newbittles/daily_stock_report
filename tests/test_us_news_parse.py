"""미국 시장 뉴스 파서 — yfinance 신/구 포맷에서 title·source·url 추출 (링크 끊김 #972)."""
from __future__ import annotations

from src.datasource.us.fdr_source import _parse_us_news_items


def test_new_format_clickthrough_url():
    raw = [{"content": {
        "title": "Fed holds rates",
        "provider": {"displayName": "Reuters"},
        "clickThroughUrl": {"url": "https://reuters.com/a"},
    }}]
    out = _parse_us_news_items(raw, 10)
    assert out == [
        {"title": "Fed holds rates", "source": "Reuters", "url": "https://reuters.com/a"},
    ]


def test_canonical_url_fallback():
    raw = [{"content": {"title": "T", "canonicalUrl": {"url": "https://x.com/c"}}}]
    assert _parse_us_news_items(raw, 10)[0]["url"] == "https://x.com/c"


def test_old_format_link():
    raw = [{"title": "Old", "publisher": "AP", "link": "https://ap.com/b"}]
    out = _parse_us_news_items(raw, 10)
    assert out[0]["url"] == "https://ap.com/b"
    assert out[0]["source"] == "AP"


def test_missing_url_is_empty_string():
    raw = [{"title": "No link"}]
    assert _parse_us_news_items(raw, 10)[0]["url"] == ""


def test_skips_non_dict_and_titleless():
    raw = ["junk", {"content": {"provider": {"displayName": "X"}}}, {"title": "A"}, {"title": "B"}]
    out = _parse_us_news_items(raw, 10)
    assert out == [{"title": "A", "source": "", "url": ""}, {"title": "B", "source": "", "url": ""}]


def test_respects_top_slice():
    raw = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    assert [n["title"] for n in _parse_us_news_items(raw, 2)] == ["A", "B"]
