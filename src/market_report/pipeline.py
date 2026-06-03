"""파이프라인 — 스크래퍼 → 분석기 → (렌더러 → 퍼블리셔 → 텔레그램).

Phase 5에서 렌더러/퍼블리셔/텔레그램 연결.
지금은 collect_snapshot()만 단독으로 호출 가능.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from src.market_report.analyzer import analyze
from src.market_report.models import MarketSnapshot, ReportMode
from src.market_report.scrapers.naver import (
    fetch_index,
    fetch_market_investor_flows,
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
        fetch_market_investor_flows(),
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
    snap.market_flows = _safe(8, []) or []
    try:
        from src.market_report.flows_history import update_flows_history
        snap.market_flows_history = update_flows_history(snap.market_flows, keep_days=3)
    except Exception as exc:
        logger.warning("flows_history_failed error=%s", exc)
        snap.market_flows_history = []

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
        _inject_candidate_quotes(snap)  # 현재가·등락률 + 관련주 등락률 보정
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
    """지수·환율·유가·금 차트 생성 → snap.candle_urls.

    종가베팅 스타일(캔들+이평·볼밴·일목·MACD)을 최근 1주일 확대로 표시.
    지표는 ~10개월 데이터로 계산(render_index_chart 내부).
    """
    from src.market_report.chart import candle_url_rel, render_index_chart

    date = snap.generated_at.strftime("%Y-%m-%d")
    items = _CANDLE_ITEMS["us_morning"] if snap.mode == "us_morning" else _CANDLE_ITEMS["kr"]

    def _one(sym: str, key: str, src: str):
        try:
            p = render_index_chart(sym, key, date, source=src)
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
    """후보 차트 생성 실패해도 예외 안 던지게 wrap. 성공 시 상대 URL 반환.

    종가베팅 후보는 candidate 레이아웃(2달·전략마커·MACD·거래대금) 사용.
    """
    from src.market_report.chart import chart_url_rel, render_candidate_chart
    try:
        path = render_candidate_chart(ticker, name, date)
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


def _norm_name(name: str) -> str:
    """종목명 매칭용 정규화 — 공백 제거 + 소문자."""
    return str(name or "").replace(" ", "").strip().lower()


def _rank_leading_themes(movers: list, strong_picks: list, jmap: dict, is_nontheme) -> list[str]:
    """오늘 '주도 테마'를 강도순으로 랭킹 — 상승률/거래량 상위 종목 + 급등 전략픽이 속한 테마.

    급등 주도주가 이끄는 테마를 포착(평균등락률 기준의 한계 보완). 예: 로봇(두산로보틱스 등
    상위) · 광통신(성호전자 상위). 점수 = 기여 종목 등락률 합(+존재 가중). 원본 테마명 반환.
    """
    from collections import defaultdict
    score: dict[str, float] = defaultdict(float)
    for s in (movers or []):
        chg = getattr(s, "change_pct", 0) or 0
        if chg <= 0:  # 주도=상승 주도. 하락/보합 종목은 제외
            continue
        jv = jmap.get(str(getattr(s, "ticker", "")).strip())
        if jv and jv.get("theme") and not is_nontheme(jv["theme"]):
            score[jv["theme"]] += chg + 1.0  # +1=존재 가중
    for p in (strong_picks or []):
        if p.get("theme_kind") == "theme" and (p.get("change_pct", 0) or 0) >= 5.0 and p.get("theme"):
            score[p["theme"]] += float(p.get("change_pct", 0) or 0)
    return sorted(score, key=lambda t: score[t], reverse=True)


def _set_leading_theme(picks: list[dict], lead_theme_names: set[str]) -> None:
    """각 종목의 '주도테마 여부'(is_leading_theme) 설정.

    기준: 종목의 테마(judal)가 '오늘 상위종목이 속한 테마' 집합에 속하는가.
    'is_theme_leader'(종목 자신이 테마 top3 주도주)와는 다른 개념.
    """
    for p in (picks or []):
        th = _norm_name(p.get("theme", ""))
        # 업종(sector) 폴백은 테마가 아니므로 제외 — judal 테마만 주도테마 판정
        p["is_leading_theme"] = bool(th and p.get("theme_kind") == "theme" and th in lead_theme_names)


def _inject_candidate_quotes(snap: MarketSnapshot) -> None:
    """종가베팅 후보(candidate_picks)에 현재가·등락률 주입 + 관련주(theme_peers) 등락률 보정.

    AI는 종목명·종목코드를 서로 어긋나게 내는 경우가 잦다(예: name=아남전자인데
    ticker=003280=다른 종목). 이 경우 ticker로 차트를 그리면 엉뚱한 종목이 표시된다.
    14:50 스냅샷의 거래량·상승·하락 상위 종목은 실제 name↔ticker↔price를 보유하므로,
    **종목명 매칭을 우선**해 종목코드를 보정하고(=AI 코드 오매칭 교정) 시세를 덮어쓴다.
    (본 함수는 후보 차트 생성 전에 호출되므로 보정된 ticker가 차트에 반영됨)
    """
    try:
        # 권위있는 종목명→코드 맵 (FDR 전체 상장) — AI 코드 오매칭(엉뚱/ETF) 교정의 1차 기준
        try:
            from src.datasource.market_cap import get_name_ticker_map
            name_ticker = get_name_ticker_map()
        except Exception:
            name_ticker = {}

        by_ticker: dict[str, Any] = {}
        by_name: dict[str, Any] = {}
        for lst in (snap.top_volume, snap.top_gainers, snap.top_losers):
            for s in (lst or []):
                by_ticker.setdefault(str(s.ticker).strip(), s)
                by_name.setdefault(_norm_name(s.name), s)

        for p in (snap.candidate_picks or []):
            tk = str(p.get("ticker", "")).strip()
            nm_norm = _norm_name(p.get("name", ""))
            # 1) 종목코드 권위 해석: 종목명으로 전체 상장목록에서 코드 확정 (AI 코드 무시).
            #    이름이 목록에 없으면 스냅샷 이름매칭, 그것도 없으면 AI 코드 유지.
            real_tk = name_ticker.get(nm_norm)
            if not real_tk:
                snap_hit = by_name.get(nm_norm)
                real_tk = str(snap_hit.ticker).strip() if snap_hit is not None else ""
            if real_tk and real_tk != tk:
                logger.info("candidate_ticker_fixed name=%s ai_ticker=%s → %s",
                            p.get("name"), tk, real_tk)
                p["ticker"] = tk = real_tk

            # 2) 시세 주입: 보정된 코드 → 스냅샷 코드매칭, 없으면 이름매칭
            hit = by_ticker.get(tk) or by_name.get(nm_norm)
            if hit is not None:
                p["price"] = float(hit.price)
                p["change_pct"] = float(hit.change_pct)

            # 관련주: 스냅샷 있으면 실등락률+코드, 없으면 전체목록 종목명으로 코드만 (네이버 링크 정확)
            for peer in p.get("theme_peers", []) or []:
                pn = _norm_name(peer.get("name", ""))
                ph = by_name.get(pn)
                if ph is not None:
                    peer["change_pct"] = float(ph.change_pct)
                    peer["ticker"] = str(ph.ticker).strip()
                    peer["matched"] = True
                elif name_ticker.get(pn):
                    peer["ticker"] = name_ticker[pn]  # 등락률은 AI값 유지, 링크만 정확
    except Exception as exc:
        logger.warning("candidate_quote_inject_failed error=%s", exc)


async def _collect_us_screening(snap: MarketSnapshot) -> None:
    """미국 종목 A/B/C/D 스크리닝 → snap.us_top3 / snap.us_screen_groups.

    us_morning 리포트의 종목 정보를 한국이 아닌 '미국 종목'으로 채운다.
    기존 us_screening 모듈(run_us_screening, S&P500 A/B/C/D)을 그대로 재사용.
    실패 시 빈 채로 두어 리포트 자체는 발송되게 한다(best-effort).
    """
    from src.screener.us_pipeline import run_us_screening
    from src.screener.us_report import STRATEGY_ORDER, _turnover

    picks = await run_us_screening()
    if not picks:
        logger.info("us_screening_no_picks")
        return

    def _pick_reason(p, initial: str = "") -> str:
        """전략 매칭 reason 중 통화 무관한 것 우선 선택.

        engine 거래대금 reason은 '억'(원화) 포맷이라 미국 달러엔 부적합 → 회피.
        """
        cands: list[str] = []
        for m in p.matches:
            if initial and m.strategy_name[:1] != initial:
                continue
            cands.extend(m.reasons)
        if not cands and not initial:
            cands = p.all_reasons
        non_won = [r for r in cands if "억" not in r and "거래대금" not in r]
        pool = non_won or cands
        return pool[0] if pool else ""

    def _to_dict(p, initial: str = "") -> dict:
        return {
            "symbol": p.symbol, "name": p.name, "price": round(p.price, 2),
            "change_pct": round(p.change_pct, 2), "sector": p.sector or "",
            "industry": p.industry or "",
            "reason": _pick_reason(p, initial),
            "cross_signal": p.cross_signal,
        }

    # 전략별 그룹 (C·B·A·D 백테스트 우위순) — 각 그룹 거래대금 상위 5
    groups: list[dict] = []
    for initial, label in STRATEGY_ORDER:
        grp = [p for p in picks if any(m.strategy_name[:1] == initial for m in p.matches)]
        if not grp:
            continue
        grp.sort(key=_turnover, reverse=True)
        groups.append({"label": label, "initial": initial,
                       "picks": [_to_dict(p, initial) for p in grp[:5]]})
    snap.us_screen_groups = groups

    # 미국 Top3 — 전체 매칭 종목 중 거래대금 상위 3 (심볼 중복 제거)
    seen: set[str] = set()
    top: list = []
    for p in sorted(picks, key=_turnover, reverse=True):
        if p.symbol in seen:
            continue
        seen.add(p.symbol)
        top.append(p)
        if len(top) >= 3:
            break
    snap.us_top3 = [_to_dict(p) for p in top]
    logger.info("us_screening_collected picks=%d top3=%s",
                len(picks), [p["symbol"] for p in snap.us_top3])


async def run_full(
    mode: ReportMode, *, do_publish: bool = True, do_telegram: bool = True, force: bool = False
) -> MarketSnapshot:
    """End-to-end: 데이터 → 분석 → HTML 렌더 → git push → 텔레그램.

    각 단계 실패는 다음 단계를 막지 않는다.
    스케줄러·CLI 모두 이 함수를 단일 진입점으로 사용.

    force: 휴장일 스킵을 무시하고 강제 실행 (테스트·수동 발송용).
    """
    from src.market_report.publisher import publish
    from src.market_report.render import render_report
    from src.market_report.telegram_notify import send_report

    logger.info("pipeline_start mode=%s force=%s", mode, force)

    # 한국장 휴장일 스킵 (평일 공휴일·임시공휴일·선거일 등). pre/post만 — us_morning은
    # 미국 캘린더 기준이라 별도(자체 신선도 스킵 보유). 휴장이면 데이터·AI 호출 전에 중단.
    if mode in ("pre_close", "post_close") and not force:
        from src.market_report.market_calendar import is_kr_market_open_today
        if not await is_kr_market_open_today():
            logger.info("kr_market_closed_skip mode=%s — 휴장일 발송 생략", mode)
            return MarketSnapshot(mode=mode, generated_at=datetime.now())

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
        # 미국 종목 스크리닝 (A/B/C/D) — 종목 정보를 '미국 종목'으로 채움.
        # 한국 시초 Top3(구 동작)는 폐기: us_morning 리포트의 종목/Top3/강세테마는 미국만.
        # 한국장 연결성은 analyze()의 theme_commentary(한국장 시사점)로 유지.
        try:
            await _collect_us_screening(snap)
        except Exception as exc:
            logger.warning("us_morning_screening_failed error=%s", exc)

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

        # 주도 테마 — 오늘 상위종목(상승률/거래량 상위) + 급등 전략픽이 속한 테마(랭킹순)
        _ranked = _rank_leading_themes((snap.top_gainers or []) + (snap.top_volume or []),
                                       snap.screen_picks, jmap, _is_nontheme)
        snap.leading_themes = _ranked[:6]
        # O/X는 표시된 주도테마(상위 6)와 일치 — 선택적
        _set_leading_theme(snap.screen_picks, {_norm_name(t) for t in snap.leading_themes})

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

        # 자동매매 브리지: 보고서 Top3를 JSON으로 남겨 auto_trader가 동일 종목 매수
        if snap.mode == "pre_close" and snap.top3:
            try:
                from datetime import datetime as _dt
                from src.trading.top3_bridge import persist_top3
                persist_top3(snap.top3, snap.mode, _dt.now().strftime("%Y-%m-%d"))
            except Exception as exc:  # 리포트를 깨지 않도록 best-effort
                logger.warning("top3_persist_failed error=%s", exc)

        logger.info("pipeline_strategy_ready picks=%d holdings=%d top3=%d",
                    len(snap.screen_picks), len(snap.holdings_status), len(snap.top3))

        # 종목별 AI 요약 사전 생성 (정적 리포트 임베드용 — 클릭 시 모달 표시)
        try:
            from src.market_report.analyzer import summarize_stocks
            await summarize_stocks(snap)
        except Exception as exc:
            logger.warning("stock_summary_skip error=%s", exc)
        # 보유종목 전체 AI 종합 코멘트 (홀드/익절/손절 관점)
        try:
            from src.market_report.analyzer import summarize_holdings
            await summarize_holdings(snap)
        except Exception as exc:
            logger.warning("holdings_summary_skip error=%s", exc)
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
