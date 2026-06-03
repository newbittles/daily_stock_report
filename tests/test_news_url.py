"""네이버 뉴스 URL 변환 — 구형 news_read.naver → 최신 기사 URL (클릭 시 기사로 연결).

배경: 구형 finance.naver.com/news/news_read.naver?article_id=X&office_id=Y 는
뉴스 홈/프레임으로 리디렉션돼 기사 본문으로 안 간다(2026-06 실측). 변환 회귀 방지.
"""
from __future__ import annotations

from src.market_report.scrapers.news import _to_article_url


def test_converts_legacy_news_read_url():
    href = "/news/news_read.naver?article_id=0002651738&office_id=016&mode=mainnews&type=&date=2026-06-04&page=1"
    assert _to_article_url(href) == "https://n.news.naver.com/article/016/0002651738"


def test_converts_absolute_legacy_url():
    href = "https://finance.naver.com/news/news_read.naver?office_id=119&article_id=0003097536&mode=mainnews"
    assert _to_article_url(href) == "https://n.news.naver.com/article/119/0003097536"


def test_passthrough_already_modern_or_other():
    # 이미 기사 URL이면 그대로
    modern = "https://n.news.naver.com/article/016/0002651738"
    assert _to_article_url(modern) == modern
    # article_id/office_id 없는 상대경로는 base 정규화 폴백
    assert _to_article_url("/news/mainnews.naver").startswith("https://finance.naver.com")
