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


async def run_full(mode: ReportMode, *, do_publish: bool = True, do_telegram: bool = True) -> MarketSnapshot:
    """End-to-end: 데이터 → 분석 → HTML 렌더 → git push → 텔레그램.

    각 단계 실패는 다음 단계를 막지 않는다.
    스케줄러·CLI 모두 이 함수를 단일 진입점으로 사용.
    """
    from src.market_report.publisher import publish
    from src.market_report.render import render_report
    from src.market_report.telegram_notify import send_report

    logger.info("pipeline_start mode=%s", mode)

    snap = await generate_report(mode)
    logger.info("pipeline_data_ready mode=%s picks=%d themes=%d",
                mode, len(snap.candidate_picks), len(snap.top_themes))

    # A/B/C 전략 스크린 + 보유종목 상태 (KIS) — 리포트에 필수 포함
    try:
        from src.config.settings import get_settings
        from src.datasource.kis.adapter import KisAdapter
        from src.market_report.strategy_section import (
            collect_holdings_status,
            collect_screen_picks,
        )
        s = get_settings()
        adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
        snap.screen_picks = await collect_screen_picks(adapter)
        snap.holdings_status = await collect_holdings_status(adapter)
        # 테마 역매핑 — 강세/약세 테마의 주도주면 테마명·주도여부 부착 (추가 크롤링 없음)
        theme_map: dict[str, str] = {}
        for t in snap.top_themes:
            for lead in t.leading_stocks:
                theme_map.setdefault(lead.strip(), t.name)
        for p in snap.screen_picks:
            tname = theme_map.get(p["name"].strip())
            if tname:
                p["theme"] = tname
                p["is_theme_leader"] = True
        logger.info("pipeline_strategy_ready picks=%d holdings=%d",
                    len(snap.screen_picks), len(snap.holdings_status))
    except Exception as exc:
        logger.error("pipeline_strategy_failed error=%s", exc)

    try:
        render_report(snap)
    except Exception as exc:
        logger.error("pipeline_render_failed error=%s", exc)

    if do_publish:
        try:
            publish(snap)
        except Exception as exc:
            logger.error("pipeline_publish_failed error=%s", exc)

    if do_telegram:
        try:
            await send_report(snap)
        except Exception as exc:
            logger.error("pipeline_telegram_failed error=%s", exc)

    logger.info("pipeline_done mode=%s", mode)
    return snap
