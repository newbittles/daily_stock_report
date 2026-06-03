"""네이버 금융 — 시장 뉴스 수집.

엔드포인트:
- 주요 뉴스:     https://finance.naver.com/news/mainnews.naver
- 시황·전망:    https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258
- 종목 관련:     https://finance.naver.com/item/news_news.naver?code=NNNNNN

selector: dd.articleSubject (제목 + 링크)
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from src.market_report.http import fetch
from src.market_report.models import NewsItem

logger = logging.getLogger(__name__)

NEWS_URL_BASE = "https://finance.naver.com"
MAIN_NEWS_URL = f"{NEWS_URL_BASE}/news/mainnews.naver"
MARKET_OUTLOOK_URL = f"{NEWS_URL_BASE}/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"


def _normalize_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return NEWS_URL_BASE + href
    return f"{NEWS_URL_BASE}/news/{href}"


def _to_article_url(href: str) -> str:
    """네이버 구형 news_read.naver?article_id=X&office_id=Y → 최신 기사 URL.

    구형 news_read.naver는 기사 본문이 아닌 뉴스 홈/프레임으로 리디렉션돼(2026-06 실측)
    클릭해도 기사로 안 간다. n.news.naver.com/article/{office_id}/{article_id}로 변환.
    파싱 실패 시 기존 정규화로 폴백.
    """
    if "article_id" in href and "office_id" in href:
        try:
            q = parse_qs(urlparse(href).query)
            aid = (q.get("article_id") or [""])[0]
            oid = (q.get("office_id") or [""])[0]
            if aid and oid:
                return f"https://n.news.naver.com/article/{oid}/{aid}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("news_url_convert_failed href=%s error=%s", href, exc)
    return _normalize_url(href)


async def fetch_market_news(top: int = 15) -> list[NewsItem]:
    """주요 시장 뉴스 N개 (네이버 금융 메인뉴스)."""
    html = await fetch(MAIN_NEWS_URL, encoding="euc-kr")
    soup = BeautifulSoup(html, "lxml")

    items: list[NewsItem] = []
    seen_titles: set[str] = set()

    for dd in soup.select("dd.articleSubject"):
        a = dd.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        href = a.get("href", "")
        url = _to_article_url(href)

        # 매체·시각 정보 (같은 dl 안의 articleSummary 등에서 추출)
        parent_dl = dd.find_parent("dl")
        source = ""
        published_at = ""
        if parent_dl:
            summary = parent_dl.find("dd", class_="articleSummary")
            if summary:
                # 보통 "한국경제 | 2분 전" 형식
                press = summary.find("span", class_="press")
                wdate = summary.find("span", class_="wdate")
                if press:
                    source = press.get_text(strip=True)
                if wdate:
                    published_at = wdate.get_text(strip=True)

        items.append(
            NewsItem(
                title=title,
                url=url,
                source=source,
                published_at=published_at,
            )
        )

        if len(items) >= top:
            break

    return items


async def fetch_market_outlook(top: int = 10) -> list[NewsItem]:
    """시황·전망 카테고리 뉴스."""
    html = await fetch(MARKET_OUTLOOK_URL, encoding="euc-kr")
    soup = BeautifulSoup(html, "lxml")

    items: list[NewsItem] = []
    seen_titles: set[str] = set()

    # 시황 페이지는 dl.newsList 또는 ul.realtimeNewsList 사용
    for a in soup.select("dl.newsList dd.articleSubject a, ul.realtimeNewsList a"):
        title = a.get_text(strip=True)
        if not title or len(title) < 8 or title in seen_titles:
            continue
        seen_titles.add(title)
        items.append(
            NewsItem(
                title=title,
                url=_to_article_url(a.get("href", "")),
                source="네이버금융",
                published_at="",
            )
        )
        if len(items) >= top:
            break

    return items
