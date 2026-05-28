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
    """전체 파이프라인 — 데이터 수집 + AI 분석 + 추천 종목 차트 생성."""
    snap = await collect_snapshot(mode)
    snap = await analyze(snap)

    # 지수 스파크라인 (KOSPI/KOSDAQ — 양 모드 공통)
    await _render_index_sparks(snap)

    # 추천 종목별 차트 생성 (마감 전만 — 마감 후는 watchpoints만)
    if snap.mode == "pre_close" and snap.candidate_picks:
        await _render_pick_charts(snap)

    return snap


async def _render_index_sparks(snap: MarketSnapshot) -> None:
    """지수 미니 시계열 차트 생성."""
    from src.market_report.chart import index_spark_url_rel, render_index_sparkline

    date = snap.generated_at.strftime("%Y-%m-%d")

    def _safe(market: str) -> str:
        try:
            path = render_index_sparkline(market, date)
            return index_spark_url_rel(market, date) if path else ""
        except Exception as exc:
            logger.warning("spark_failed market=%s error=%s", market, exc)
            return ""

    kospi_url, kosdaq_url = await asyncio.gather(
        asyncio.to_thread(_safe, "KOSPI"),
        asyncio.to_thread(_safe, "KOSDAQ"),
    )
    snap.kospi_spark_url = kospi_url
    snap.kosdaq_spark_url = kosdaq_url


async def _render_pick_charts(snap: MarketSnapshot) -> None:
    """후보 종목별 차트 생성 — 동기 함수를 to_thread로 병렬 처리."""
    from src.market_report.chart import render_chart

    date = snap.generated_at.strftime("%Y-%m-%d")
    tasks = []
    for p in snap.candidate_picks:
        ticker = str(p.get("ticker", "")).strip()
        name = str(p.get("name", "")).strip()
        if not ticker or not name:
            continue
        tasks.append(asyncio.to_thread(_render_chart_safe, ticker, name, date))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 각 pick에 chart_url 추가 (성공한 것만)
    for p, result in zip(snap.candidate_picks, results):
        if isinstance(result, Exception) or result is None:
            p["chart_url"] = ""
            logger.warning("chart_skip ticker=%s reason=%s", p.get("ticker"), result)
        else:
            p["chart_url"] = result


def _render_chart_safe(ticker: str, name: str, date: str) -> str | None:
    """차트 생성 실패해도 예외 안 던지게 wrap. 성공 시 상대 URL 반환."""
    from src.market_report.chart import chart_url_rel, render_chart
    try:
        path = render_chart(ticker, name, date)
        if path is None:
            return None
        return chart_url_rel(ticker, date)
    except Exception as exc:
        logger.warning("chart_render_failed ticker=%s error=%s", ticker, exc)
        return None
