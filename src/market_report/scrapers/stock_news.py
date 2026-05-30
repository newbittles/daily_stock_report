"""종목별 뉴스 — 구글 뉴스 RSS 기반.

종목명으로 검색해 최근 뉴스 헤드라인 + 링크 추출.
광고성 매체/제목 필터링.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from src.market_report.http import fetch
from src.market_report.models import NewsItem

logger = logging.getLogger(__name__)

# 광고성/저품질 키워드 (제목 또는 매체) — 헤드라인에 있으면 제외
_AD_KEYWORDS = (
    "광고", "분양", "이벤트", "쿠폰", "할인", "프로모션", "협찬",
    "카지노", "토토", "베팅", "대출", "보험료",
)
# 신뢰 매체 우선 (있으면 먼저 선택)
_TRUSTED = (
    "한국경제", "매일경제", "서울경제", "조선비즈", "이데일리", "연합뉴스",
    "파이낸셜뉴스", "헤럴드경제", "전자신문", "머니투데이", "뉴스1", "아시아경제",
)


def _is_ad(title: str, source: str) -> bool:
    text = f"{title} {source}"
    return any(kw in text for kw in _AD_KEYWORDS)


def _parse_source(title: str) -> tuple[str, str]:
    """구글 뉴스 제목은 '제목 - 매체명' 형식. 분리."""
    if " - " in title:
        head, src = title.rsplit(" - ", 1)
        return head.strip(), src.strip()
    return title.strip(), ""


async def fetch_stock_news(name: str, top: int = 1) -> list[NewsItem]:
    """종목명으로 구글 뉴스 검색 → 광고 제외 최신 뉴스 top개.

    신뢰 매체를 우선하되, 없으면 최신 비광고 뉴스.
    """
    query = f"{name} 주가"
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        xml = await fetch(url, encoding="utf-8")
    except Exception as exc:
        logger.warning("stock_news_failed name=%s error=%s", name, exc)
        return []

    soup = BeautifulSoup(xml, "xml")
    items = soup.find_all("item")

    candidates: list[NewsItem] = []
    for it in items:
        raw_title = it.title.get_text() if it.title else ""
        link = it.link.get_text() if it.link else ""
        pub = it.pubDate.get_text() if it.pubDate else ""
        if not raw_title or not link:
            continue
        headline, source = _parse_source(raw_title)
        if _is_ad(headline, source):
            continue
        candidates.append(NewsItem(
            title=headline, url=link, source=source, published_at=pub,
        ))

    if not candidates:
        return []

    # 신뢰 매체 우선 정렬 (구글 RSS는 관련도순 → 상위가 최신·핵심)
    trusted = [n for n in candidates if any(t in n.source for t in _TRUSTED)]
    pool = trusted if trusted else candidates
    return pool[:top]
