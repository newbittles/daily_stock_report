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
from src.market_report.scrapers.macro import fetch_macro
from src.market_report.scrapers.news import fetch_market_news
from src.market_report.scrapers.theme import fetch_top_themes

logger = logging.getLogger(__name__)


async def collect_snapshot(mode: ReportMode) -> MarketSnapshot:
    """모든 스크래퍼를 병렬 호출해 시장 스냅샷 구성."""
    logger.info("snapshot_collect_start mode=%s", mode)

    # 종목순위는 ETF/ETN/우선주가 상위를 다수 차지 → 넉넉히 받아 필터 후 자름
    results = await asyncio.gather(
        fetch_index("KOSPI"),
        fetch_index("KOSDAQ"),
        fetch_top_volume("KOSPI", top=60),
        fetch_top_gainers("KOSPI", top=40),
        fetch_top_losers("KOSPI", top=40),
        fetch_top_themes(top=10),
        fetch_market_news(top=15),
        fetch_macro(),
        return_exceptions=True,
    )

    def _safe(idx: int, default):
        r = results[idx]
        if isinstance(r, Exception):
            logger.warning("scraper_failed idx=%d error=%s", idx, r)
            return default
        return r

    # 종목 스크린과 동일 기준 — ETF/ETN/우선주 제외 (위험/거래정지는 naver 데이터 한계, 별도 TODO)
    from src.screener.pipeline import _is_etf, _is_pref

    def _clean_rank(stocks, limit):
        out = [s for s in stocks
               if not _is_etf(getattr(s, "name", "")) and not _is_pref(getattr(s, "name", ""))]
        return out[:limit]

    snap = MarketSnapshot(
        mode=mode,
        generated_at=datetime.now(),
        kospi=_safe(0, None),
        kosdaq=_safe(1, None),
        top_volume=_clean_rank(_safe(2, []), 20),
        top_gainers=_clean_rank(_safe(3, []), 15),
        top_losers=_clean_rank(_safe(4, []), 15),
        top_themes=_safe(5, []),
        market_news=_safe(6, []),
    )
    _macro = _safe(7, {}) or {}
    snap.fx = _macro.get("fx")
    snap.wti = _macro.get("wti")

    logger.info(
        "snapshot_collected mode=%s kospi=%s themes=%d news=%d",
        mode,
        snap.kospi.value if snap.kospi else "fail",
        len(snap.top_themes),
        len(snap.market_news),
    )
    return snap


async def collect_us_snapshot() -> MarketSnapshot:
    """미국 증시 스냅샷 (us_morning) — FDR 지수/빅테크/섹터."""
    from dataclasses import asdict

    from src.datasource.us.fdr_source import (
        fetch_us_bigtech, fetch_us_indices, fetch_us_sectors,
    )
    logger.info("us_snapshot_collect_start")
    idx, bt, sec = await asyncio.gather(
        fetch_us_indices(), fetch_us_bigtech(), fetch_us_sectors(),
        return_exceptions=True,
    )

    def _safe(r):
        if isinstance(r, Exception):
            logger.warning("us_fetch_failed error=%s", r)
            return []
        return r

    snap = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    snap.us_indices = [asdict(q) for q in _safe(idx)]
    snap.us_bigtech = [asdict(q) for q in _safe(bt)]
    snap.us_sectors = [asdict(q) for q in _safe(sec)]
    # 금/유가 (미국 지수 2x2 하단 — 금 좌, 유가 우)
    try:
        from src.market_report.scrapers.macro import fetch_macro
        macro = await fetch_macro()
        snap.gold = macro.get("gold")
        snap.wti = macro.get("wti")
    except Exception as exc:
        logger.warning("us_macro_failed error=%s", exc)
    logger.info("us_snapshot_collected indices=%d bigtech=%d sectors=%d",
                len(snap.us_indices), len(snap.us_bigtech), len(snap.us_sectors))
    return snap


async def generate_report(mode: ReportMode) -> MarketSnapshot:
    """전체 파이프라인 — 데이터 수집 + AI 분석 + 추천 종목 차트 생성."""
    if mode == "us_morning":
        snap = await collect_us_snapshot()
        snap = await analyze(snap)
        await _render_candles(snap)
        return snap

    snap = await collect_snapshot(mode)
    snap = await analyze(snap)

    # 지수·환율·유가 미니 캔들차트 (지수 2x2 각 항목)
    await _render_candles(snap)

    # 추천 종목별 차트 생성 (마감 전만 — 마감 후는 watchpoints만)
    if snap.mode == "pre_close" and snap.candidate_picks:
        await _render_pick_charts(snap)

    return snap


# 지수 2x2 각 항목 캔들 대상 — (심볼, 키, 소스)
_CANDLE_ITEMS = {
    "us_morning": [("US500", "us_sp500", "fdr"), ("IXIC", "us_nasdaq", "fdr"),
                   ("GC=F", "gold", "yf"), ("CL=F", "wti", "yf")],
    "kr": [("KS11", "KOSPI", "fdr"), ("KQ11", "KOSDAQ", "fdr"),
           ("KRW=X", "fx", "yf"), ("CL=F", "wti", "yf")],
}


async def _render_candles(snap: MarketSnapshot) -> None:
    """지수·환율·유가·금 미니 캔들차트 생성 → snap.candle_urls."""
    from src.market_report.chart import candle_url_rel, render_mini_candle

    date = snap.generated_at.strftime("%Y-%m-%d")
    items = _CANDLE_ITEMS["us_morning"] if snap.mode == "us_morning" else _CANDLE_ITEMS["kr"]

    def _one(sym: str, key: str, src: str):
        try:
            p = render_mini_candle(sym, key, date, source=src)
            return key, (candle_url_rel(key, date) if p else "")
        except Exception as exc:
            logger.warning("candle_failed key=%s error=%s", key, exc)
            return key, ""

    results = await asyncio.gather(*[asyncio.to_thread(_one, s, k, sr) for s, k, sr in items])
    snap.candle_urls = {k: u for k, u in results if u}


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


def _supply_streak(rows: list[dict], key: str) -> int:
    """최신순 일별 순매수에서 연속 순매수일 수 (양수 연속)."""
    n = 0
    for r in rows:
        if (r.get(key) or 0) > 0:
            n += 1
        else:
            break
    return n


async def _inject_supply_streak(snap: MarketSnapshot, adapter) -> None:
    """Top3 종목별 기관/외국인 연속 순매수일 → supply_str (예 '기관 순매수(3일) · 외국인 순매수(2일)')."""
    for t in (snap.top3 or []):
        try:
            rows = await adapter.get_stock_investor_daily(t["ticker"], days=10)
            od, fd = _supply_streak(rows, "orgn"), _supply_streak(rows, "frgn")
            parts = []
            if od > 0:
                parts.append(f"기관 순매수({od}일)")
            if fd > 0:
                parts.append(f"외국인 순매수({fd}일)")
            t["supply_str"] = " · ".join(parts)
        except Exception as exc:
            logger.warning("supply_streak_failed ticker=%s error=%s", t.get("ticker"), exc)
            t["supply_str"] = ""


def _inject_marcap(snap: MarketSnapshot) -> None:
    """모든 종목(top3·screen_picks·candidate_picks)에 시가총액(원) 주입 — 리포트 표기용."""
    try:
        from src.datasource.market_cap import format_marcap, get_market_cap_map
        mm = get_market_cap_map()
        if not mm:
            return
        for lst in (snap.top3, snap.screen_picks, snap.candidate_picks):
            for p in (lst or []):
                tk = str(p.get("ticker", "")).strip()
                if tk:
                    p["marcap"] = mm.get(tk, 0)
                    p["marcap_str"] = format_marcap(p["marcap"])
        # 전략 스크린은 시총 내림차순 정렬 (전략별 그룹 내 순서 유지됨)
        if snap.screen_picks:
            snap.screen_picks.sort(key=lambda p: -(p.get("marcap") or 0))
    except Exception as exc:
        logger.warning("marcap_inject_failed error=%s", exc)


async def run_full(mode: ReportMode, *, do_publish: bool = True, do_telegram: bool = True) -> MarketSnapshot:
    """End-to-end: 데이터 → 분석 → HTML 렌더 → git push → 텔레그램.

    각 단계 실패는 다음 단계를 막지 않는다.
    스케줄러·CLI 모두 이 함수를 단일 진입점으로 사용.
    """
    from src.market_report.publisher import publish
    from src.market_report.render import render_report
    from src.market_report.telegram_notify import send_report

    logger.info("pipeline_start mode=%s", mode)

    # 오래된 차트 정리 (7일 이전 PNG 삭제 — git 용량 누적 방지)
    try:
        from src.market_report.chart import cleanup_old_charts
        cleanup_old_charts(7)
    except Exception as exc:
        logger.warning("chart_cleanup_failed error=%s", exc)

    snap = await generate_report(mode)
    logger.info("pipeline_data_ready mode=%s picks=%d themes=%d",
                mode, len(snap.candidate_picks), len(snap.top_themes))

    # 미국장(us_morning) P2: 미국 강세테마 연동 한국 시초 매수 Top3
    if mode == "us_morning":
        # Q4: 미국 휴장 스킵 — 지수 최신 거래일이 3일 이상 지났으면(주말+휴장) 발송 안 함
        try:
            from datetime import date as _date
            _last = (snap.us_indices[0].get("date", "") if snap.us_indices else "")
            if _last and (_date.today() - _date.fromisoformat(_last)).days >= 3:
                logger.info("us_market_holiday_skip last=%s — 발송 생략", _last)
                return snap
        except Exception as exc:
            logger.warning("us_freshness_check_failed error=%s", exc)
        try:
            from src.config.settings import get_settings
            from src.datasource.kis.adapter import KisAdapter
            from src.market_report.strategy_section import collect_screen_picks
            from src.market_report.theme_bridge import strong_kr_keywords
            from src.market_report.top3 import select_top3
            s = get_settings()
            adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
            snap.screen_picks = await collect_screen_picks(adapter, drop_today=True)  # 장전 미완성봉 제외
            # 종목 테마 채우기 (judal → 세분업종 폴백) — 미국 강세테마 매칭에 필요
            try:
                from src.market_report.scrapers.judal import _is_nontheme, build_judal_theme_map
                jmap = await build_judal_theme_map(max_themes=200)
            except Exception:
                jmap, _is_nontheme = {}, (lambda _n: False)
            for p in snap.screen_picks:
                jv = jmap.get(p["ticker"])
                if jv and jv.get("theme") and not _is_nontheme(jv["theme"]):
                    p["theme"], p["theme_kind"], p["theme_idx"] = jv["theme"], "theme", jv.get("idx", "")
            try:
                from src.market_report.scrapers.sector import get_stock_sectors
                need = [p["ticker"] for p in snap.screen_picks if not p.get("theme")]
                if need:
                    secs = await get_stock_sectors(need)
                    for p in snap.screen_picks:
                        if not p.get("theme") and secs.get(p["ticker"]):
                            p["theme"], p["theme_kind"] = secs[p["ticker"]], "sector"
            except Exception as exc:
                logger.warning("us_sector_fallback_failed error=%s", exc)
            # 시초 Top3 — P4 + 미국 강세테마 연동 가중(w_us) + ATR 손절
            strong_kw = strong_kr_keywords(snap.us_sectors)
            fb = {x["ticker"] for x in await adapter.get_investor_net_buy("foreign", "buy")}
            ib = {x["ticker"] for x in await adapter.get_investor_net_buy("inst", "buy")}
            snap.top3 = select_top3(snap.screen_picks, foreign_buy=fb, inst_buy=ib,
                                    us_keywords=strong_kw, w_us=2.0, us_sectors=snap.us_sectors)
            await _inject_supply_streak(snap, adapter)  # 연속 순매수일
            from src.market_report.analyzer import summarize_stocks
            await summarize_stocks(snap)
            logger.info("us_morning_top3 top3=%s us_kw=%d",
                        [t["name"] for t in snap.top3], len(strong_kw))
        except Exception as exc:
            logger.warning("us_morning_strategy_failed error=%s", exc)

        _inject_marcap(snap)
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
        # 테마 — judal(주달) 종목→테마 역인덱스 (네이버보다 트렌드 반영·정확). 일1회 캐시.
        jmap: dict[str, dict] = {}
        try:
            from src.market_report.scrapers.judal import _is_nontheme, build_judal_theme_map
            jmap = await build_judal_theme_map(max_themes=200)
        except Exception as exc:
            logger.warning("judal_theme_failed error=%s", exc)

            def _is_nontheme(_n):  # judal 실패 시 폴백 정의
                return False
        leaders = {lead.strip() for t in snap.top_themes for lead in t.leading_stocks}
        for p in snap.screen_picks:
            jv = jmap.get(p["ticker"])
            if jv and jv.get("theme") and not _is_nontheme(jv["theme"]):
                p["theme"] = jv["theme"]
                p["theme_kind"] = "theme"
                p["theme_idx"] = jv.get("idx", "")
            p["is_theme_leader"] = p["name"].strip() in leaders
        # judal 테마 없는 종목 → 네이버 세분업종 폴백 (누락 0)
        try:
            from src.market_report.scrapers.sector import get_stock_sectors
            need = [p["ticker"] for p in snap.screen_picks if not p.get("theme")]
            if need:
                sectors = await get_stock_sectors(need)
                for p in snap.screen_picks:
                    if not p.get("theme") and sectors.get(p["ticker"]):
                        p["theme"] = sectors[p["ticker"]]
                        p["theme_kind"] = "sector"
        except Exception as exc:
            logger.warning("sector_fallback_failed error=%s", exc)

        # Top3 종합추천 — 수급(외인/기관 순매수) 수집 후 P4 점수로 3종목 선정
        try:
            from src.market_report.top3 import select_top3
            fb = {x["ticker"] for x in await adapter.get_investor_net_buy("foreign", "buy")}
            ib = {x["ticker"] for x in await adapter.get_investor_net_buy("inst", "buy")}
            snap.top3 = select_top3(snap.screen_picks, foreign_buy=fb, inst_buy=ib)
            await _inject_supply_streak(snap, adapter)  # 연속 순매수일
            logger.info("pipeline_top3_ready top3=%s", [t["name"] for t in snap.top3])
        except Exception as exc:
            logger.warning("top3_failed error=%s", exc)

        logger.info("pipeline_strategy_ready picks=%d holdings=%d top3=%d",
                    len(snap.screen_picks), len(snap.holdings_status), len(snap.top3))

        # 종목별 AI 요약 사전 생성 (정적 리포트 임베드용 — 클릭 시 모달 표시)
        try:
            from src.market_report.analyzer import summarize_stocks
            await summarize_stocks(snap)
        except Exception as exc:
            logger.warning("stock_summary_skip error=%s", exc)
    except Exception as exc:
        logger.error("pipeline_strategy_failed error=%s", exc)

    _inject_marcap(snap)
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
