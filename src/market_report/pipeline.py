"""파이프라인 — 스크래퍼 → 분석기 → (렌더러 → 퍼블리셔 → 텔레그램).

Phase 5에서 렌더러/퍼블리셔/텔레그램 연결.
지금은 collect_snapshot()만 단독으로 호출 가능.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from src.market_report.analyzer import analyze
from src.market_report.models import MarketSnapshot, ReportMode
from src.market_report.scrapers.naver import (
    fetch_index,
    fetch_top_gainers,
    fetch_top_losers,
    fetch_top_volume,
)
from src.market_report.scrapers.news import fetch_market_news
from src.market_report.scrapers.theme import fetch_top_themes

logger = logging.getLogger(__name__)


async def collect_snapshot(mode: ReportMode) -> MarketSnapshot:
    """모든 스크래퍼를 병렬 호출해 시장 스냅샷 구성."""
    logger.info("snapshot_collect_start mode=%s", mode)

    results = await asyncio.gather(
        fetch_index("KOSPI"),
        fetch_index("KOSDAQ"),
        fetch_top_volume("KOSPI", top=30),
        fetch_top_gainers("KOSPI", top=15),
        fetch_top_losers("KOSPI", top=15),
        fetch_top_themes(top=10),
        fetch_market_news(top=15),
        return_exceptions=True,
    )

    def _safe(idx: int, default):
        r = results[idx]
        if isinstance(r, Exception):
            logger.warning("scraper_failed idx=%d error=%s", idx, r)
            return default
        return r

    snap = MarketSnapshot(
        mode=mode,
        generated_at=datetime.now(),
        kospi=_safe(0, None),
        kosdaq=_safe(1, None),
        top_volume=_safe(2, []),
        top_gainers=_safe(3, []),
        top_losers=_safe(4, []),
        top_themes=_safe(5, []),
        market_news=_safe(6, []),
    )

    logger.info(
        "snapshot_collected mode=%s kospi=%s themes=%d news=%d",
        mode,
        snap.kospi.value if snap.kospi else "fail",
        len(snap.top_themes),
        len(snap.market_news),
    )
    return snap


async def generate_report(mode: ReportMode) -> MarketSnapshot:
    """전체 파이프라인 — 데이터 수집 + AI 분석."""
    snap = await collect_snapshot(mode)
    snap = await analyze(snap)
    return snap
