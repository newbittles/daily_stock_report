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
        fetch_us_bigtech, fetch_us_indices, fetch_us_news, fetch_us_sectors,
    )
    logger.info("us_snapshot_collect_start")
    idx, bt, sec, news = await asyncio.gather(
        fetch_us_indices(), fetch_us_bigtech(), fetch_us_sectors(), fetch_us_news(10),
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
    # 섹터 전체(상승률순) 보관 — 표시단에서 강세 top4 / 약세 bottom4 슬라이스 (사용자 2026-06-04)
    snap.us_sectors = [asdict(q) for q in _safe(sec)]
    snap.us_news = _safe(news)  # 미국 시장 뉴스 헤드라인 (장후 뉴스·이슈 AI 해설용)
    # 금/유가 (미국 지수 2x2 하단 — 금 좌, 유가 우)
    try:
        from src.market_report.scrapers.macro import fetch_macro
        macro = await fetch_macro()
        snap.gold = macro.get("gold")
        snap.wti = macro.get("wti")
    except Exception as exc:
        logger.warning("us_macro_failed error=%s", exc)
    try:
        from src.datasource.us.fear_greed import fetch_fear_greed
        snap.fear_greed = await fetch_fear_greed()  # 공포탐욕지수(바닥 보조, 사용자 #331)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_fear_greed_failed error=%s", exc)
    try:  # EWY(한국 MSCI ETF) — 미국 마감 리포트 고정 표시(#479)
        from src.datasource.us.fdr_source import fetch_ewy
        snap.ewy = await fetch_ewy()
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_ewy_failed error=%s", exc)
    try:  # 지수 이평선 이격도(고점 판단, 사용자 #357) + 시장 국면 신호등(#362)
        snap.ma_gaps = {"나스닥": await _index_ma_gaps("IXIC"), "S&P500": await _index_ma_gaps("US500")}
        _fill_market_phase(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_ma_gaps_failed error=%s", exc)
    logger.info("us_snapshot_collected indices=%d bigtech=%d sectors=%d fg=%s",
                len(snap.us_indices), len(snap.us_bigtech), len(snap.us_sectors),
                snap.fear_greed.get("score") if snap.fear_greed else None)
    return snap


async def warm_us_cache() -> None:
    """미국 ohlcv 캐시 선제 워밍 — 리포트 시각 전에 1회 다운로드(#499).

    us_morning/us_premarket의 14분 지연 대부분이 yfinance 일봉 다운로드(캐시 미스).
    리포트 전 워밍 잡이 동일 유니버스·days로 `data/us_ohlcv_cache.json`을 채워두면
    실제 리포트는 캐시 히트로 수십 초에 끝난다(같은 마감 데이터 재사용 → 정보 손실 0).
    발송·웹 없음. 실패해도 리포트는 자체 다운로드로 폴백(best-effort)."""
    snap = await collect_us_snapshot()
    await _collect_us_screening(snap, per_group=3)
    logger.info("us_cache_warmed top3=%d screen=%d",
                len(snap.us_top3 or []), len(snap.us_screen_ranked or []))


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
        await _inject_candidate_strategies(snap)  # 종가베팅 후보에 ABCD 전략 매칭 라벨(사용자 2026-06-05)
        await _render_pick_charts(snap)

    return snap


async def _inject_candidate_strategies(snap: MarketSnapshot) -> None:
    """종가베팅 후보(AI 선정)에 해당 ABCD 전략 라벨 부착 → snap.candidate_picks[i]['strategies'].

    AI가 고른 후보가 실제 A/B/C/D 기준에 부합하는지 투명화(사용자 2026-06-05). 각 후보 일봉으로
    스크리너 엔진 재평가 → 매칭 전략 리스트(빈 리스트=ABCD 미해당). best-effort(실패 시 미부착)."""
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter
    from src.screener.config import load_screener_config
    from src.screener.engine import evaluate_strategy

    try:
        s = get_settings()
        adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
        strategies = load_screener_config().enabled_strategies()
    except Exception as exc:  # noqa: BLE001
        logger.warning("candidate_strategies_setup_failed error=%s", exc)
        return
    for p in snap.candidate_picks:
        tk = p.get("ticker", "")
        if not tk:
            continue
        try:
            c = await adapter.get_ohlcv(tk, days=180)
        except Exception as exc:  # noqa: BLE001
            logger.debug("candidate_strat_ohlcv_failed ticker=%s error=%s", tk, exc)
            continue
        if len(c) < 60:
            continue
        chg = (c[-1].close - c[-2].close) / c[-2].close * 100 if len(c) >= 2 and c[-2].close else 0.0
        p["strategies"] = [st.name.split(".")[0].strip() for st in strategies
                           if evaluate_strategy(st.name, st.opinion, st.conditions, c, chg).matched]
    n_abcd = sum(1 for p in snap.candidate_picks if p.get("strategies"))
    logger.info("candidate_strategies_injected total=%d abcd_matched=%d",
                len(snap.candidate_picks), n_abcd)


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
    # us_intraday도 미국 차트(S&P·나스닥·금·유가) — 누락 시 금 차트 빠짐(#511)
    items = (_CANDLE_ITEMS["us_morning"]
             if snap.mode in ("us_morning", "us_premarket", "us_intraday") else _CANDLE_ITEMS["kr"])

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


async def collect_hot_stocks(
    snap: MarketSnapshot, adapter, top: int = 5, min_marcap_won: float = 5e11,
) -> list[dict]:
    """상승률>거래대금 순 + 시총 하한(5000억) 핫종목 → 거래대금 전일대비·순매수 연속일·소속테마.

    장중/마감 리포트 공용(사용자 2026-06-04). 후보=상승률상위∪거래량상위, 상승률 우선 정렬,
    시총 5000억 미만 잡주 제외. 종목당 KIS 일봉·투자자 조회(Top5라 가벼움).
    """
    from src.datasource.market_cap import get_market_cap_map

    # 후보 = 상승률 상위 ∪ 거래량 상위(중복 제거). 정렬 = 상승률 > 거래대금 순(사용자 2026-06-04).
    pool: dict[str, object] = {}
    for s in (snap.top_gainers or []) + (snap.top_volume or []):
        pool.setdefault(s.ticker, s)
    cands = sorted(pool.values(),
                   key=lambda s: (s.change_pct, getattr(s, "trade_value", 0) or 0), reverse=True)
    marcap: dict[str, int] = {}
    try:
        marcap = get_market_cap_map()
    except Exception as exc:  # noqa: BLE001
        logger.warning("hot_marcap_failed error=%s", exc)
    picked = [s for s in cands if marcap.get(s.ticker, 0) >= min_marcap_won][:top]
    if not picked:
        return []

    themes: dict[str, str] = {}
    try:
        from src.market_report.scrapers.sector import get_stock_sectors
        themes = await get_stock_sectors([s.ticker for s in picked])
    except Exception as exc:  # noqa: BLE001
        logger.warning("hot_sector_failed error=%s", exc)

    out: list[dict] = []
    for s in picked:
        d = {
            "ticker": s.ticker, "name": s.name, "price": s.price,
            "change_pct": round(s.change_pct, 2), "marcap": marcap.get(s.ticker, 0),
            "theme": themes.get(s.ticker, ""), "tv_today": None, "tv_change": None,
            "streak": {"orgn": 0, "frgn": 0, "prsn": 0},
        }
        try:  # 거래대금 금액(오늘) + 전일대비(%)
            candles = await adapter.get_ohlcv(s.ticker, days=3)
            if candles:
                tv0 = candles[-1].close * candles[-1].volume
                d["tv_today"] = tv0  # 오늘 거래대금(원)
                if len(candles) >= 2:
                    tv1 = candles[-2].close * candles[-2].volume
                    d["tv_change"] = round((tv0 - tv1) / tv1 * 100, 0) if tv1 else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("hot_tv_failed ticker=%s error=%s", s.ticker, exc)
        try:  # 순매수 연속일 (기관/외인/개인)
            rows = await adapter.get_stock_investor_daily(s.ticker, days=10)
            d["streak"] = {"orgn": _supply_streak(rows, "orgn"),
                           "frgn": _supply_streak(rows, "frgn"),
                           "prsn": _supply_streak(rows, "prsn")}
        except Exception as exc:  # noqa: BLE001
            logger.debug("hot_streak_failed ticker=%s error=%s", s.ticker, exc)
        out.append(d)
    logger.info("hot_stocks collected=%d", len(out))
    return out


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


def _supply_sell_streak(rows: list[dict], key: str) -> int:
    """최신순 일별에서 연속 순매도일 수 (음수 연속)."""
    n = 0
    for r in rows:
        if (r.get(key) or 0) < 0:
            n += 1
        else:
            break
    return n


async def collect_supply_streaks(adapter, top: int = 40, min_marcap_won: float = 1e12,
                                 min_streak: int = 2) -> tuple[list[dict], list[dict]]:
    """기관+외인 연속 순매수/순매도 Top — 시총 상위 종목(사용자 #393, 스마트머니 선행).

    시총 상위 top개(시총 하한) 각 일별 투자자 → 기관·외인 둘 다 min_streak일↑ 연속 순매수=매수후보,
    둘 다 연속 순매도=매도후보. (기관+외인이 개인보다 빠르다는 취지). 반환 (buy, sell) 각 점수순.
    """
    import asyncio
    import random

    def _univ() -> list[tuple]:
        import FinanceDataReader as fdr
        out: list[tuple] = []
        for mkt in ("KOSPI", "KOSDAQ"):
            df = fdr.StockListing(mkt).dropna(subset=["Marcap"]).sort_values(
                "Marcap", ascending=False).head(top)
            for _, r in df.iterrows():
                out.append((str(r["Code"]).zfill(6), str(r["Name"]), float(r["Marcap"])))
        out.sort(key=lambda x: -x[2])
        return out[:top]

    try:
        universe = await asyncio.to_thread(_univ)
    except Exception as exc:  # noqa: BLE001
        logger.warning("supply_universe_failed error=%s", exc)
        return [], []

    buys: list[dict] = []
    sells: list[dict] = []
    for tk, name, mc in universe:
        if mc < min_marcap_won:
            continue
        try:
            rows = await adapter.get_stock_investor_daily(tk, days=7)
        except Exception:  # noqa: BLE001
            continue
        ob, fb = _supply_streak(rows, "orgn"), _supply_streak(rows, "frgn")
        os_, fs = _supply_sell_streak(rows, "orgn"), _supply_sell_streak(rows, "frgn")
        if ob >= min_streak and fb >= min_streak:
            buys.append({"ticker": tk, "name": name, "orgn": ob, "frgn": fb, "score": ob + fb})
        elif os_ >= min_streak and fs >= min_streak:
            sells.append({"ticker": tk, "name": name, "orgn": os_, "frgn": fs, "score": os_ + fs})
        await asyncio.sleep(random.uniform(0.1, 0.25))  # §7 분산
    buys.sort(key=lambda x: -x["score"])
    sells.sort(key=lambda x: -x["score"])
    logger.info("supply_streaks buy=%d sell=%d (universe=%d)", len(buys), len(sells), len(universe))
    return buys[:7], sells[:7]


def _inject_marcap(snap: MarketSnapshot) -> None:
    """모든 종목(top3·screen_picks·candidate_picks·e_picks·surge_picks)에 시가총액(원) 주입 — 표기용.

    e_picks/surge_picks는 거래대금(trade_value→turnover_str)도 함께 포맷(사용자 2026-06-05).
    """
    try:
        from src.datasource.market_cap import format_marcap, get_market_cap_map
        mm = get_market_cap_map()
        if not mm:
            return
        for lst in (snap.top3, snap.screen_picks, snap.candidate_picks, snap.e_picks,
                    snap.surge_picks, snap.support_picks):
            for p in (lst or []):
                tk = str(p.get("ticker", "")).strip()
                if tk:
                    p["marcap"] = mm.get(tk, 0)
                    p["marcap_str"] = format_marcap(p["marcap"])
                # KR e/surge: 거래대금(원) 포맷 (US는 _collect_us_screening에서 이미 turnover_str)
                if p.get("trade_value") and not p.get("turnover_str"):
                    p["turnover_str"] = format_marcap(p["trade_value"])
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


def _is_etf_name(name_en: str) -> bool:
    """SEIBro 영문명으로 ETF/ETN 여부 판정(개별종목 칸과 분리용). 대부분 'ETF'/'ETN' 포함."""
    u = name_en.upper()
    return "ETF" in u or "ETN" in u


async def _collect_kr_us_netbuy(snap: MarketSnapshot) -> None:
    """서학개미(한국인) 미국주식 순매수 → snap.kr_us_netbuy (SEIBro, 최근 5거래일 누적).

    개별종목/ETF를 한 리스트에 is_etf 플래그로 담는다(표시단에서 칸 분리, 사용자 2026-06-05).
    금액은 억원(USD×환율/1e8), 종목명 옆 티커 표시. pre/post 둘 다.
    SEIBro/환율 실패 시 빈 채로 둠(best-effort — 섹션 생략, 리포트는 발송).
    """
    from src.datasource.us.fdr_source import fetch_usd_krw
    from src.datasource.us.names_ko import korean_name
    from src.datasource.us.seibro_source import fetch_us_net_buy
    from src.datasource.us.seibro_symbols import ticker_for

    rows = await fetch_us_net_buy(trading_days=5, top=50)  # 50개 받아 개별/ETF 각 TOP5 확보
    if not rows:
        return
    rate = await fetch_usd_krw()  # USD→KRW. 0이면 억 환산 불가 → USD만 보관

    def _clean_en(name_en: str) -> str:
        nm = name_en.title()
        for acro in ("Etf", "Adr", "Ads"):
            nm = nm.replace(f" {acro}", f" {acro.upper()}")
        return nm

    out: list[dict] = []
    for r in rows:
        ticker = ticker_for(r.isin)
        out.append({
            "ticker": ticker,
            "name": korean_name(ticker, "") if ticker else "",
            "net_buy_usd": r.net_buy_amt,
            "net_buy_eok": round(r.net_buy_amt * rate / 1e8) if rate else 0,
            "is_etf": _is_etf_name(r.name_en),
            "isin": r.isin, "_en": r.name_en,
        })
    snap.kr_us_netbuy = out
    n_stock = sum(1 for o in out if not o["is_etf"])
    logger.info("kr_us_netbuy_ready n=%d stocks=%d etfs=%d", len(out), n_stock, len(out) - n_stock)

    # 데이터 기준일 표기(사용자 2026-06-09) — SEIBro는 날짜필드가 없어 우리가 조회한 구간으로 표기.
    # fetch_us_net_buy(trading_days=5)와 동일한 lookback_range(5)를 재계산(결정론적)해 정확히 일치시킨다.
    try:
        import datetime as _dt
        from src.datasource.us.seibro_source import lookback_range
        sdt, edt = lookback_range(5)
        sd = _dt.datetime.strptime(sdt, "%Y%m%d").date()
        ed = _dt.datetime.strptime(edt, "%Y%m%d").date()
        today = _dt.date.today()

        def _md(d: _dt.date) -> str:
            return f"{d.month}/{d.day}"

        snap.kr_us_netbuy_dates = {
            "range": f"{_md(sd)}~{_md(ed)}", "latest": _md(ed), "today": _md(today),
            "delay_days": (today - ed).days, "trading_days": 5,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("kr_us_netbuy_dates_failed error=%s", exc)

    sell_out: list[dict] = []
    # 순매도(자금 유출) TOP3 — 매도결제금액 상위 중 순매수 음수(사용자 #318: 자금이 어디로 빠지는지)
    try:
        from src.datasource.us.seibro_source import fetch_us_net_sell
        srows = await fetch_us_net_sell(trading_days=5, top=3)
        for r in srows:
            ticker = ticker_for(r.isin)
            sell_out.append({
                "ticker": ticker, "name": korean_name(ticker, "") if ticker else "",
                "net_sell_usd": -r.net_buy_amt,  # 양수(유출 규모)
                "net_sell_eok": round(-r.net_buy_amt * rate / 1e8) if rate else 0,
                "is_etf": _is_etf_name(r.name_en),
                "isin": r.isin, "_en": r.name_en,
            })
        snap.kr_us_netsell = sell_out
        logger.info("kr_us_netsell_ready n=%d", len(sell_out))
    except Exception as exc:  # noqa: BLE001
        logger.warning("kr_us_netsell_failed error=%s", exc)

    # 미매핑 종목/ETF: AI로 티커·한국어명 보강(ISIN 캐시) → 빈 티커·영문명 최소화(사용자 #441/#442)
    try:
        from src.datasource.us.seibro_enrich import enrich as _seibro_enrich
        unmapped = [(d["isin"], d["_en"]) for d in (out + sell_out) if not d["ticker"]]
        if unmapped:
            emap = await _seibro_enrich(unmapped)
            for d in (out + sell_out):
                if not d["ticker"]:
                    e = emap.get(d["isin"]) or {}
                    if e.get("ticker"):
                        d["ticker"] = e["ticker"]
                    if e.get("ko"):
                        d["name"] = e["ko"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("seibro_enrich_apply_failed error=%s", exc)
    # 보강 후에도 빈 이름은 영문 정리본으로 폴백 + 내부 키 정리
    for d in (out + sell_out):
        if not d["name"]:
            d["name"] = _clean_en(d["_en"])
        d.pop("_en", None)
        d.pop("isin", None)

    # 한국인 자금흐름 총액(TOP50 순매수 합) — 이번주 일평균 vs 전주 일평균(사용자 #377)
    # 올해 초 코스피→나스닥(SOXL 등) 자금이동 추세를 총액으로 추산.
    try:
        import datetime as _dt
        from src.datasource.us.seibro_source import lookback_range
        this_total = sum(r.net_buy_amt for r in rows)  # 이번주 5거래일 TOP50 순매수 합(USD)
        ps, pe = lookback_range(5, end=_dt.date.today() - _dt.timedelta(days=8))  # 전주 구간
        prev_rows = await fetch_us_net_buy(top=50, start_dt=ps, end_dt=pe)
        prev_total = sum(r.net_buy_amt for r in prev_rows)

        def _eok(usd: float) -> int:
            return round(usd * rate / 1e8) if rate else 0

        tt, pt = _eok(this_total), _eok(prev_total)
        snap.kr_us_netbuy_total = {
            "total_eok": tt, "daily_avg_eok": round(tt / 5),
            "prev_daily_avg_eok": round(pt / 5),
            "change_pct": round((tt - pt) / pt * 100, 1) if pt else None,
        }
        logger.info("kr_us_netbuy_total this=%d억 prev=%d억", tt, pt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kr_us_netbuy_total_failed error=%s", exc)


async def _attach_kr_netbuy_to_picks(snap: MarketSnapshot) -> None:
    """미국 추천 Top3/ABCD/섹터·테마 대장 픽에 서학개미 순매수금액 부착(전일 + 최근5거래일).

    SEIBro TOP50(전일 단일일 + 5거래일 누적)에서 ISIN→티커로 매핑해, 각 픽의 symbol과
    교차되면 kr_netbuy_prev_eok / kr_netbuy_5d_eok(억원) 부착. 장전·장후 둘 다(사용자 2026-06-05).
    TOP50 권외 종목은 부착 안 함(배지 생략). SEIBro/환율 실패 시 조용히 건너뜀(best-effort)."""
    from src.datasource.us.fdr_source import fetch_usd_krw
    from src.datasource.us.seibro_source import fetch_us_net_buy, fetch_us_net_sell, prev_trading_day
    from src.datasource.us.seibro_symbols import ticker_for

    rate = await fetch_usd_krw()
    if not rate:
        return
    five = await fetch_us_net_buy(trading_days=5, top=50)
    pday = prev_trading_day()
    prev = await fetch_us_net_buy(top=50, start_dt=pday, end_dt=pday)
    sells = await fetch_us_net_sell(trading_days=5, top=50)  # 순매도 TOP50(픽이 매도상위면 표시, #431)

    def _ticker_eok(rows: list, sign: int = 1) -> dict[str, int]:
        m: dict[str, int] = {}
        for r in rows:
            tk = ticker_for(r.isin)
            if tk:
                m[tk] = round(sign * r.net_buy_amt * rate / 1e8)
        return m

    m5, m1 = _ticker_eok(five), _ticker_eok(prev)
    ms = _ticker_eok(sells, sign=-1)  # 양수 = 유출(순매도) 규모
    if not (m5 or m1 or ms):
        return
    dicts: list[dict] = list(snap.us_top3 or []) + list(snap.us_theme_leaders or []) \
        + list(snap.us_sector_leaders or []) + list(snap.e_picks or []) + list(snap.surge_picks or []) \
        + list(snap.us_screen_ranked or [])  # 종합점수순 픽도 서학개미 부착(#454)
    for g in (snap.us_screen_groups or []):
        dicts.extend(g.get("picks", []))
    hit = 0
    for d in dicts:
        sym = d.get("symbol", "")
        if sym in m5 or sym in m1:
            d["kr_netbuy_5d_eok"] = m5.get(sym)
            d["kr_netbuy_prev_eok"] = m1.get(sym)
            hit += 1
        elif sym in ms and ms[sym] > 0:  # 매수TOP50엔 없지만 매도TOP50에 있으면(마이크론 케이스 #431)
            d["kr_netsell_5d_eok"] = ms[sym]
            hit += 1
    logger.info("kr_netbuy_pick_attach hit=%d (m5=%d m1=%d sell=%d)", hit, len(m5), len(m1), len(ms))


async def _market_rsi(market: str) -> float | None:
    """시장 지수 일봉 RSI(14) — US=나스닥(IXIC), KR=코스피(KS11). E 2단계 등급용(사용자 #330/#339).

    미국 종목엔 나스닥, 한국 종목엔 코스피만 사용(절대 교차 안 함). 실패 시 None."""
    sym = "IXIC" if market == "US" else "KS11"

    def _work() -> float | None:
        import datetime as _dt

        import FinanceDataReader as fdr

        from src.indicators.core import rsi as _rsi
        start = (_dt.date.today() - _dt.timedelta(days=160)).isoformat()
        df = fdr.DataReader(sym, start)
        closes = [float(x) for x in df["Close"].dropna()]
        if len(closes) < 20:
            return None
        r = _rsi(closes, 14)
        return r[-1] if r and r[-1] is not None else None

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_rsi_failed market=%s error=%s", market, exc)
        return None


def _tag_market_bottom(picks: list[dict], market_rsi: float | None, threshold: float = 35.0,
                       fg_score: float | None = None, fg_max: float = 25.0) -> None:
    """E 픽에 시장 동반 바닥 등급 부착 — 지수 RSI<threshold OR 공포탐욕≤fg_max면 '강'(사용자 #330/#331).

    공포탐욕지수(F&G)≤25(extreme fear)도 시장 바닥 신호로 인정(백테스트: F&G≤25 매수 20일 +4~9%)."""
    fg_bottom = fg_score is not None and fg_score <= fg_max
    for p in (picks or []):
        p["market_rsi"] = round(market_rsi) if market_rsi is not None else None
        p["fg_score"] = round(fg_score) if fg_score is not None else None
        p["market_bottom"] = bool((market_rsi is not None and market_rsi < threshold) or fg_bottom)


_OVERHEAT_120 = {"나스닥": 12.0, "S&P500": 8.0, "코스피": 40.0, "코스닥": 40.0}
_OVERHEAT_60 = {"나스닥": 9.0, "S&P500": 7.0, "코스피": 25.0, "코스닥": 25.0}


def _market_phase(label: str, gaps: dict) -> tuple[str, str]:
    """지수 이격도 → 시장 국면 신호등(사용자 #360/#362). (이모지, 국면명).

    우선순위(비대칭): 바닥권(검증된 실전) > 과열(정보용) > 하락전환 > 조정 > 단기눌림 > 정상.
    바닥은 이격/RSI로 잘 잡히지만(백테스트 #371) 고점은 단일지표 신뢰 낮아(#363) 과열은 정보 라벨."""
    g5, g20, g60, g120 = (gaps.get(k) for k in (5, 20, 60, 120))
    rv = gaps.get("rsi")
    if g120 is None or g60 is None:
        return ("⚪", "판단불가")
    # 바닥 3단계 게이지(백테스트 #371/#419/#422/#424). 코스닥 주봉신호는 노이즈라 제외.
    rsi_w, rsi_m = gaps.get("rsi_w"), gaps.get("rsi_m")
    cci_d, cci_w = gaps.get("cci"), gaps.get("cci_w")
    is_kosdaq = label == "코스닥"
    # 🔵🔵🔵 역대급 대바닥 — 월봉 RSI≤31 (희귀, 6개월후 +14~60%·승80%↑, 2008급)
    if rsi_m is not None and rsi_m <= 31:
        return ("🔵🔵🔵", "역대급 대바닥")
    # 🔵🔵 강한 바닥(중기) — 주봉 RSI≤31 OR 주봉 CCI≤-200 (12주후 +6~10%·승60~88%, 코스닥 제외)
    if not is_kosdaq and ((rsi_w is not None and rsi_w <= 31)
                          or (cci_w is not None and cci_w <= -200)):
        return ("🔵🔵", "강한 바닥")
    # 🔵 바닥권(1차) — 일봉 RSI≤30 OR 60일이격≤-7% OR 일봉 CCI≤-200 (검증 #371/#424)
    if (rv is not None and rv <= 30) or g60 <= -7 or (cci_d is not None and cci_d <= -200):
        return ("🔵", "바닥권")
    # 🔴 과열(고점권 경계·정보용) = 이격 임계 AND RSI≥70. ⚠️타이밍 신뢰 낮음(#363) — 매도 트리거 아님.
    if (g120 >= _OVERHEAT_120.get(label, 12.0) or g60 >= _OVERHEAT_60.get(label, 9.0)) \
            and (rv is None or rv >= 70):
        return ("🔴", "과열")
    # 🔼 상승전환 — 5일선 음→양 회복 + 20일선 위(진짜 반등, 백테스트 #379: 미국 69% vs 가짜 46%)
    g5_prev = gaps.get("g5_prev")
    if g5_prev is not None and g5_prev < 0 and g5 is not None and g5 >= 0 \
            and g20 is not None and g20 >= 0:
        return ("🔼", "상승전환")
    if g60 < 0:
        return ("🔻", "하락전환")
    if g20 is not None and g20 < 0:
        return ("🟠", "조정")
    if g5 is not None and g5 < 0:
        return ("🟡", "단기눌림")
    return ("🟢", "정상")


def _fill_market_phase(snap: MarketSnapshot) -> None:
    """snap.ma_gaps 기반으로 지수별 시장 국면 신호등 채움 → snap.market_phase {라벨:{emoji,name}}."""
    out: dict[str, dict] = {}
    for label, gaps in (snap.ma_gaps or {}).items():
        if gaps:
            em, nm = _market_phase(label, gaps)
            out[label] = {"emoji": em, "name": nm}
    snap.market_phase = out


async def _index_ma_gaps(symbol: str) -> dict:
    """지수 이평선 이격도 — 5/10/20/60/120일선 대비 현재가 괴리%(사용자 #357, 고점 판단). 실패 시 {}."""
    def _work() -> dict:
        import datetime as _dt

        import FinanceDataReader as fdr

        from src.indicators.core import cci, moving_average, rsi
        # 800일 — 월봉 RSI(14, ~15개월 필요)·주봉 CCI까지 계산. 일봉 이격/RSI는 마지막값이라 불변.
        start = (_dt.date.today() - _dt.timedelta(days=800)).isoformat()
        df = fdr.DataReader(symbol, start)
        c = [float(x) for x in df["Close"].dropna()]
        if len(c) < 120:
            return {}
        out: dict = {}
        ma5_series = moving_average(c, 5)
        for k in (5, 10, 20, 60, 120):
            ma = moving_average(c, k)[-1]
            if ma:
                out[k] = round((c[-1] - ma) / ma * 100, 1)
        # 전일 5일선 이격(상승전환 전환 감지용, #379)
        if len(c) >= 2 and ma5_series[-2]:
            out["g5_prev"] = round((c[-2] - ma5_series[-2]) / ma5_series[-2] * 100, 1)
        rv = rsi(c, 14)[-1]
        if rv is not None:
            out["rsi"] = round(rv)
        # 바닥 3단계용(#419/#422/#424): 일봉 CCI + 주봉 RSI/CCI + 월봉 RSI
        try:
            hi = [float(x) for x in df["High"].dropna()]
            lo = [float(x) for x in df["Low"].dropna()]
            if len(hi) == len(c) and len(lo) == len(c):
                cd = cci(hi, lo, c, 20)[-1]
                if cd is not None:
                    out["cci"] = round(cd)
            wc = df["Close"].resample("W-FRI").last().dropna()
            wcl = [float(x) for x in wc]
            if len(wcl) >= 15:
                wr = rsi(wcl, 14)[-1]
                if wr is not None:
                    out["rsi_w"] = round(wr)
            wh = [float(x) for x in df["High"].resample("W-FRI").max().dropna()]
            wl = [float(x) for x in df["Low"].resample("W-FRI").min().dropna()]
            if len(wcl) >= 20 and len(wh) == len(wcl) and len(wl) == len(wcl):
                cw = cci(wh, wl, wcl, 20)[-1]
                if cw is not None:
                    out["cci_w"] = round(cw)
            mc = [float(x) for x in df["Close"].resample("ME").last().dropna()]
            if len(mc) >= 15:
                mr = rsi(mc, 14)[-1]
                if mr is not None:
                    out["rsi_m"] = round(mr)
        except Exception as exc:  # noqa: BLE001
            logger.warning("index_bottom_metrics_failed symbol=%s error=%s", symbol, exc)
        # 거래량 연속 증가(최근 2일) — 반등에 거래량 실리는지 정보 표식(사용자 #388/#392)
        try:
            vol = [float(x) for x in df["Volume"].dropna()]
            if len(vol) >= 3:
                out["vol_up"] = bool(vol[-1] > vol[-2] > vol[-3])
        except Exception:  # noqa: BLE001
            pass
        return out

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:  # noqa: BLE001
        logger.warning("index_ma_gaps_failed symbol=%s error=%s", symbol, exc)
        return {}


def _tag_bigtech_strategies(snap: MarketSnapshot, ohlcv: dict) -> None:
    """대장주(빅테크/주요ETF) 리스트에 전략(A/B/C/D/E/급등초입) + E바닥 태깅(사용자 #345).

    스크리닝 캐시 OHLCV로 일봉 패턴 평가(없으면 전략 빈칸). E 매칭이면 e_bottom=True(시장바닥 등급은 호출측).
    """
    from src.patterns.core import (
        gave_back_recent_gain, is_convergence_breakout, is_downtrend_reversal,
        is_ma20_pullback, is_surge_start, is_trend_follow, oversold_leader,
    )
    for b in (snap.us_bigtech or []):
        cs = ohlcv.get(b.get("symbol", ""))
        if not cs or len(cs) < 60:
            b["strategies"] = []
            continue
        st: list[str] = []
        if is_trend_follow(cs).matched:
            st.append("C")
        if is_ma20_pullback(cs).matched and not gave_back_recent_gain(cs):
            st.append("B")
        if is_convergence_breakout(cs).matched:
            st.append("A")
        if is_downtrend_reversal(cs).matched:
            st.append("D")
        if is_surge_start(cs).matched:
            st.append("급등초입")
        if oversold_leader(cs).matched:
            st.append("E")
            b["e_bottom"] = True
        b["strategies"] = st


async def _collect_us_screening(snap: MarketSnapshot, *, per_group: int = 5) -> None:
    """미국 종목 A/B/C/D 스크리닝 → snap.us_top3 / snap.us_screen_groups.

    us_morning 리포트의 종목 정보를 한국이 아닌 '미국 종목'으로 채운다.
    기존 us_screening 모듈(run_us_screening, S&P500 A/B/C/D)을 그대로 재사용.
    per_group: 전략(A/B/C/D)별 노출 종목 수(사용자 2026-06-05: 마감·장중 3개). 기본 5.
    실패 시 빈 채로 두어 리포트 자체는 발송되게 한다(best-effort).
    """
    from src.datasource.us.universe import get_hybrid_universe
    from src.screener.us_pipeline import run_us_screening
    from src.screener.us_report import STRATEGY_ORDER, _turnover

    _MARCAP_FLOOR_WON = 4e11   # 시총 하한 4천억 — 전 종목 고정(워치리스트 면제 폐지, 사용자 2026-06-04)
    _PRICE_CAP_WON = 5e6       # 주가 상한 500만원/주
    _PRICE_FLOOR_USD = 1.5     # 페니주 제외 — $1.5 미만 컷(전 종목, 사용자 2026-06-04)
    _MARCAP_TOPN = 50          # 시총 조회는 거래대금 상위 N개만(속도)

    try:
        # 하이브리드: S&P500 ∪ 나스닥 거래대금상위(캐시) ∪ 큐레이션 — 발견+보장
        universe = await get_hybrid_universe()
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_hybrid_universe_failed error=%s", exc)
        universe = None
    picks = await run_us_screening(universe=universe)
    if not picks:
        logger.info("us_screening_no_picks")
        return

    from src.datasource.us.fdr_source import fetch_us_market_caps, fetch_usd_krw

    from src.datasource.us.names_ko import us_theme as _us_theme_fn
    from src.datasource.us.universe import US_GROWTH_WATCHLIST

    rate = await fetch_usd_krw()  # USD→KRW (0이면 환산·필터 스킵, best-effort)
    # 페니주 제외($1 미만) — 워치리스트 포함 전체 적용(시총 면제와 무관, 사용자 2026-06-04).
    picks = [p for p in picks if p.price >= _PRICE_FLOOR_USD]
    if rate:  # 주가 상한 필터 (price는 이미 있음 — 무료, marcap 조회 전 선필터)
        picks = [p for p in picks if p.price * rate <= _PRICE_CAP_WON]
    picks.sort(key=_turnover, reverse=True)
    top50 = picks[:_MARCAP_TOPN]  # 추천Top3·전략그룹용(거래대금 상위)
    # 테마 대장 후보 = 전체 매칭에서 테마별 거래대금 1등 (top50 컷 전 — 양자 등 소형테마 보존)
    _watch_themes = {w.sector for w in US_GROWTH_WATCHLIST}
    _by_theme: dict[str, list] = {}
    for p in picks:
        _by_theme.setdefault(_us_theme_fn(p.sector, p.industry), []).append(p)
    theme_cands = [max(m, key=_turnover) for m in _by_theme.values()]
    # 시총 조회 = top50 ∪ 테마대장 후보 (양자 대장도 시총 조회 보장)
    marcaps = await fetch_us_market_caps(
        list({p.symbol for p in top50} | {p.symbol for p in theme_cands}))
    if rate:  # 시총 하한 필터 — 전 종목 4천억 고정(워치리스트도 동일, 사용자 2026-06-04)
        picks = [p for p in top50 if marcaps.get(p.symbol, 0) * rate >= _MARCAP_FLOOR_WON]
        theme_cands = [p for p in theme_cands if marcaps.get(p.symbol, 0) * rate >= _MARCAP_FLOOR_WON]
    else:
        picks = top50
    if not picks:
        logger.info("us_screening_all_filtered")
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

    from src.datasource.market_cap import format_marcap
    from src.datasource.us.names_ko import korean_name, us_theme
    from src.datasource.us.symbols import to_yf_symbol

    def _won(usd: float) -> str:
        """USD 금액 → 원화 조/억 표기 (환율 0이면 빈 문자열)."""
        return format_marcap(usd * rate) if (usd and rate) else ""

    def _gap20(p) -> float:
        """현재가의 20일 이동평균 대비 괴리(%) — B전략 정렬·표시용."""
        cs = [c.close for c in p.candles[-20:]]
        if len(cs) < 20:
            return 0.0
        ma = sum(cs) / len(cs)
        return (p.price - ma) / ma * 100 if ma else 0.0

    def _overheat_volx(p) -> tuple[bool, float]:
        """🔥 과열(일봉 BB 상단 종가돌파) + 거래량 배수 — KR strategy_section과 동일 공식(#414 통일)."""
        from statistics import pstdev
        cs = [c.close for c in p.candles]
        if len(cs) < 20:
            return False, 0.0
        ma = sum(cs[-20:]) / 20
        bbup = ma + 2 * pstdev(cs[-20:]) if ma else 0.0
        vols = [c.volume for c in p.candles]
        va = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0.0
        volx = p.candles[-1].volume / va if va else 0.0
        return bool(bbup and p.candles[-1].close > bbup), round(volx, 1)

    def _endstage(p) -> bool:
        return any((getattr(m, "metrics", {}) or {}).get("endstage") for m in p.matches)

    def _week_pct(p) -> float | None:
        """최근 1주일(5거래일) 상승률 — 섹터/테마 대장 표시용(#433)."""
        if len(p.candles) >= 6 and p.candles[-6].close:
            return round((p.price / p.candles[-6].close - 1) * 100, 1)
        return None

    def _strategies(p) -> list[str]:
        return sorted({m.strategy_name[:1] for m in p.matches})

    def _eff_cross(cs, strats) -> str | None:
        # ⚠️조정시작은 추세추종(C)에만 — 수렴후상승(A) 등엔 부적합(사용자 2026-06-04).
        if cs == "CORRECTION" and "C" not in strats:
            return None
        return cs

    def _to_dict(p, initial: str = "") -> dict:
        strats = _strategies(p)
        ctx = {initial} if initial else set(strats)  # 그룹이면 그 전략, top3면 전체
        _hi60 = max((x.high for x in p.candles[-60:]), default=p.price) if p.candles else p.price
        _hdd = round((p.price / _hi60 - 1) * 100, 1) if _hi60 else 0.0  # 60일 고점 대비 낙폭
        _reason = _pick_reason(p, initial)
        if "B" in ctx:  # B 설명란에 고점대비 낙폭 표시(사용자 2026-06-05)
            _reason += f" · 고점대비 {_hdd:+.1f}%"
        _ov, _vx = _overheat_volx(p)
        return {
            "symbol": p.symbol,
            # 한국어(티커) 표기 — 아는 종목은 한국어, 모르는 건 영문명 폴백(사용자 합의).
            "name": korean_name(p.symbol, p.name), "price": round(p.price, 2),
            # 야후/구글 링크용 정규화 심볼(BRKB→BRK-B). 표시는 symbol, 링크는 yf_symbol.
            "yf_symbol": to_yf_symbol(p.symbol),
            "change_pct": round(p.change_pct, 2),
            # 표시 테마 = GICS Industry 한국어 세분(반도체/반도체장비/클라우드 등). 섹터(IT)는 거침.
            "sector": us_theme(p.sector, p.industry),
            "industry": p.industry or "",
            "reason": _reason,
            "cross_signal": _eff_cross(p.cross_signal, ctx),
            "strategies": strats,                       # #4 A/B/C/D 표시
            "marcap_str": _won(marcaps.get(p.symbol, 0)),  # 시총(원화 조/억)
            "turnover_str": _won(_turnover(p)),         # 거래대금(원화 조/억)
            "gap20": round(_gap20(p), 1),               # #11 20MA 괴리(B 표시·정렬)
            "high_dd": _hdd,
            # KR과 표시 통일(#414): 과열(BB돌파)·거래량배수·끝물 — 픽/순위 불변, 표시용만
            "overheat": _ov, "vol_x": _vx, "endstage": _endstage(p),
            "week_pct": _week_pct(p),  # 최근 1주일 상승률(#433)
        }

    # 한국어 종목명 DB 채우기(미캐시 종목 네이버 best-effort) — _to_dict 전에 (사용자 154)
    try:
        from src.datasource.us.names_db import ensure_names
        _allp = list(picks) + list(theme_cands)
        # name_map(티커→영문명) 전달 → 네이버 실패 종목은 AI 음역으로 캐시(사용자 2026-06-05)
        await ensure_names([p.symbol for p in _allp], {p.symbol: p.name for p in _allp})
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_names_ensure_failed error=%s", exc)

    # 전략별 그룹 (A·B·C·D 순) — 기본 거래대금 상위 5, B는 20MA 괴리 작은 순(#11)
    groups: list[dict] = []
    for initial, label in STRATEGY_ORDER:
        grp = [p for p in picks if any(m.strategy_name[:1] == initial for m in p.matches)]
        if not grp:
            continue
        if initial == "B":
            # 시총>거래대금 순(사용자 2026-06-04). 이격도(gap20)는 참고 표시만.
            grp.sort(key=lambda p: (marcaps.get(p.symbol, 0), _turnover(p)), reverse=True)
        else:
            grp.sort(key=_turnover, reverse=True)
        groups.append({"label": label, "initial": initial,
                       "picks": [_to_dict(p, initial) for p in grp[:per_group]]})
    snap.us_screen_groups = groups

    # 종합 랭킹(종목당 1개·거래대금순·매칭전략 다 표기) — KR screen_ranked과 동일 컨셉(사용자 #454)
    seen: set[str] = set()
    ranked: list[dict] = []
    for p in sorted(picks, key=_turnover, reverse=True):
        if p.symbol in seen:
            continue
        seen.add(p.symbol)
        ranked.append(_to_dict(p))  # initial 없음 → 매칭 전략 전체 표기
    snap.us_screen_ranked = ranked[:12]

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

    # 관심 테마 대장 = 큐레이션 테마(양자·우주·AI·원자력 등)만, 별도 노출(사용자 162).
    # (섹터 대장은 별도 — us_sector_leaders. 양자는 ETF 섹터에 없어 여기 둠.)
    _watch = [p for p in theme_cands if _us_theme_fn(p.sector, p.industry) in _watch_themes]
    _watch.sort(key=lambda p: p.change_pct, reverse=True)
    snap.us_theme_leaders = [_to_dict(p) for p in _watch[:10]]

    logger.info("us_screening_collected picks=%d top3=%s theme_leaders=%d",
                len(picks), [p["symbol"] for p in snap.us_top3], len(snap.us_theme_leaders))

    # E전략(과매도 주도주) — 유니버스 전체 캐시 OHLCV로 일봉 판정 → 4H RSI 게이트(사용자 2026-06-05)
    try:
        from src.datasource.us.fdr_source import fetch_us_ohlcv_batch
        from src.patterns.core import oversold_leader
        uni_syms = [u.symbol for u in (universe or [])]
        if uni_syms:
            ohlcv = await fetch_us_ohlcv_batch(uni_syms, days=120)  # 캐시 히트(스크리닝과 동일)
            meta2 = {u.symbol: u for u in (universe or [])}
            from src.patterns.core import is_surge_start
            e_cand: list[dict] = []
            surge: list[dict] = []
            for sym, cs in ohlcv.items():
                if len(cs) < 60 or cs[-1].close < _PRICE_FLOOR_USD:
                    continue
                ch = ((cs[-1].close - cs[-2].close) / cs[-2].close * 100
                      if len(cs) >= 2 and cs[-2].close else 0.0)
                u = meta2.get(sym)
                # 공통 표기필드(시총·거래량·거래대금·테마) — 사용자 2026-06-05
                _extra = {
                    "marcap_str": _won(marcaps.get(sym, 0)),
                    "turnover_str": _won(cs[-1].close * cs[-1].volume),
                    "volume": cs[-1].volume,
                    "theme": us_theme(u.sector, u.industry) if u else "",
                    "theme_kind": "sector",
                }
                er = oversold_leader(cs)
                if er.matched:
                    e_cand.append({"symbol": sym, "name": korean_name(sym, u.name if u else sym),
                                   "price": round(cs[-1].close, 2), "change_pct": round(ch, 2),
                                   "rsi": round(float(er.metrics.get("rsi", 0)), 0), "reason": er.reason,
                                   **_extra})
                sr = is_surge_start(cs)
                if sr.matched:
                    surge.append({"symbol": sym, "name": korean_name(sym, u.name if u else sym),
                                  "price": round(cs[-1].close, 2), "change_pct": round(ch, 2),
                                  "reason": sr.reason, **_extra})
            e_cand.sort(key=lambda x: x["rsi"])  # 가장 과매도부터 4H 확인
            e_cand = e_cand[:12]
            from src.datasource.kr_4h import fetch_4h_rsi_oversold
            _ok = await fetch_4h_rsi_oversold([p["symbol"] for p in e_cand], market="US")
            snap.e_picks = [p for p in e_cand if p["symbol"] in _ok][:7]
            _fg_us = snap.fear_greed.get("score") if snap.fear_greed else None
            _us_mr = await _market_rsi("US")
            _tag_market_bottom(snap.e_picks, _us_mr, fg_score=_fg_us)  # 나스닥/F&G 동반바닥(#330/#331/#339)
            _tag_bigtech_strategies(snap, ohlcv)  # 대장주 전략·E바닥 태깅(#345)
            _tag_market_bottom([b for b in (snap.us_bigtech or []) if b.get("e_bottom")],
                               _us_mr, fg_score=_fg_us)
            snap.surge_picks = sorted(surge, key=lambda x: x["change_pct"], reverse=True)[:7]
            logger.info("us_e_picks_ready daily=%d final=%d surge=%d",
                        len(e_cand), len(snap.e_picks), len(snap.surge_picks))
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_e_picks_failed error=%s", exc)


async def _overlay_live_quote(snap: MarketSnapshot, fetch_fn, flag: str,
                              price_key: str, log_label: str) -> None:
    """장전/장중 공용 시세 오버레이 — 픽·주요종목·섹터 change_pct를 실시간 기준으로 덮어씀.

    프리장(flag='premkt')·장중(flag='intraday')이 동일 로직(fetch_fn·키만 다름) → 통합.
    change_pct=실시간 등락률, close_pct=직전 마감 등락률 보존, flag=True, price_key=실시간가.
    미체결은 마감값 유지(flag=False). 주요종목·섹터·테마대장은 실시간 등락률순 재정렬.
    """
    pick_dicts: list[dict] = list(snap.us_top3 or []) + list(snap.us_theme_leaders or [])
    for g in (snap.us_screen_groups or []):
        pick_dicts.extend(g.get("picks", []))
    other_dicts: list[dict] = list(snap.us_bigtech or []) + list(snap.us_sectors or [])
    all_dicts = pick_dicts + other_dicts
    syms = list({d["symbol"] for d in all_dicts if d.get("symbol")})
    if not syms:
        return
    q_map = await fetch_fn(syms)
    for d in all_dicts:
        q = q_map.get(d.get("symbol", ""))
        if q:
            d[flag] = True
            d["close_pct"] = d.get("change_pct", 0)   # 직전 마감 등락률 보존
            d["change_pct"] = q["change_pct"]          # 표시 등락률 = 실시간(프리장/장중)
            # 가격은 전일마감가 유지(사용자), 실시간가는 참고용으로만 보관
            d[price_key] = round(q["price"], 2)
        else:
            d.setdefault(flag, False)
    # 주요종목·섹터·테마대장은 실시간 등락률순 재정렬 (섹터 전체 → 표시단에서 강세/약세 슬라이스)
    for coll in (snap.us_bigtech, snap.us_sectors, snap.us_theme_leaders):
        if coll:
            coll.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
    logger.info("%s targets=%d matched=%d", log_label, len(all_dicts), len(q_map))


async def _overlay_premarket(snap: MarketSnapshot) -> None:
    """장전 리포트 — change_pct를 프리장 기준으로 오버레이(close_pct=마감 보존). 공용 로직 사용."""
    from src.datasource.us.fdr_source import fetch_us_premarket

    await _overlay_live_quote(snap, fetch_us_premarket, "premkt", "premkt_price", "us_premarket_overlay")


async def _overlay_postmarket(snap: MarketSnapshot) -> None:
    """us_morning(아침 마감 리포트) — 추천종목·스크린·주요종목에 애프터장(시간외) 등락률 부착.

    change_pct(장마감 등락률)는 보존하고, after_pct/after_price만 추가한다(표시: 장마감
    종가(등락률)(애프터장등락률), 사용자 2026-06-05). 미체결 종목은 부착 안 함(best-effort)."""
    from src.datasource.us.fdr_source import fetch_us_postmarket

    dicts: list[dict] = list(snap.us_top3 or []) + list(snap.us_theme_leaders or []) \
        + list(snap.us_sector_leaders or [])
    for g in (snap.us_screen_groups or []):
        dicts.extend(g.get("picks", []))
    syms = list({d["symbol"] for d in dicts if d.get("symbol")})
    if not syms:
        return
    pm = await fetch_us_postmarket(syms)
    matched = 0
    for d in dicts:
        q = pm.get(d.get("symbol", ""))
        if q:
            d["after_pct"] = q["change_pct"]
            d["after_price"] = round(q["price"], 2)
            matched += 1
    logger.info("us_postmarket_overlay targets=%d matched=%d", len(dicts), matched)


async def _overlay_intraday(snap: MarketSnapshot) -> None:
    """us_intraday(장중, 23:50/개장직후) — change_pct를 '현재 장중' 기준으로 오버레이. 공용 로직 사용.

    ⚠️ 개장 직후라 값이 흔들림 → 표시단에서 '장중 잠정' 라벨(사용자 합의 2026-06-05)."""
    from src.datasource.us.fdr_source import fetch_us_intraday

    await _overlay_live_quote(snap, fetch_us_intraday, "intraday", "intraday_price", "us_intraday_overlay")


async def _collect_sector_leaders(snap: MarketSnapshot) -> None:
    """표시될 강세4 + 약세4 섹터의 대장주(시총1등) → snap.us_sector_leaders (주요 종목).

    장전이면 대장주 등락률도 프리장 기준으로 오버레이(가격은 마감가 유지)."""
    secs = snap.us_sectors or []
    if not secs:
        return
    strong = [s.get("name", "") for s in secs[:4]]
    weak = [s.get("name", "") for s in sorted(secs, key=lambda x: x.get("change_pct", 0))[:4]]
    try:
        from src.datasource.us.fdr_source import fetch_sector_leaders
        leaders = await fetch_sector_leaders(strong + weak)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_sector_leaders_failed error=%s", exc)
        return
    # SOXL(반도체 3X) 고정 병기 — 섹터 대장 리스트 끝에 추가(사용자 2026-06-09). 실패해도 본 리스트는 유지.
    try:
        from src.datasource.us.fdr_source import fetch_soxl_leader
        soxl = await fetch_soxl_leader()
        if soxl and not any(d.get("symbol") == "SOXL" for d in leaders):
            leaders.append(soxl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_sector_leaders_soxl_failed error=%s", exc)
    if snap.mode == "us_premarket" and leaders:
        try:
            from src.datasource.us.fdr_source import fetch_us_premarket
            pm = await fetch_us_premarket([d["symbol"] for d in leaders])
            for d in leaders:
                q = pm.get(d["symbol"])
                if q:
                    d["close_pct"] = d.get("change_pct", 0)
                    d["change_pct"] = q["change_pct"]
                    d["premkt"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("us_sector_leaders_premarket_failed error=%s", exc)
    snap.us_sector_leaders = leaders
    logger.info("us_sector_leaders=%d", len(leaders))


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
            await _collect_us_screening(snap, per_group=3)  # 마감 리포트 ABCD 3개(사용자 2026-06-05)
        except Exception as exc:
            logger.warning("us_morning_screening_failed error=%s", exc)
        try:
            await _collect_sector_leaders(snap)  # 주요종목 = 섹터 대장
        except Exception as exc:
            logger.warning("us_morning_sector_leaders_failed error=%s", exc)
        try:
            await _overlay_postmarket(snap)  # 애프터장(시간외) 등락률 부착 (장마감 종가 옆 병기)
        except Exception as exc:
            logger.warning("us_morning_postmarket_failed error=%s", exc)
        try:
            await _attach_kr_netbuy_to_picks(snap)  # 픽별 서학개미 순매수금액(전일+5일)
        except Exception as exc:
            logger.warning("us_morning_kr_netbuy_failed error=%s", exc)
        try:
            await _collect_kr_us_netbuy(snap)  # 한국인 자금흐름 매수TOP5+매도TOP3(#318)
        except Exception as exc:
            logger.warning("us_morning_kr_netflow_failed error=%s", exc)
        try:
            from src.market_report.analyzer import summarize_us_stocks
            await summarize_us_stocks(snap)  # 종목별 AI 요약(🤖 버튼, 사용자 #309)
        except Exception as exc:
            logger.warning("us_morning_summary_failed error=%s", exc)
        try:
            from src.market_report.analyzer import translate_us_news
            await translate_us_news(snap)  # 뉴스 헤드라인 한국어 번역(사용자 #394)
        except Exception as exc:
            logger.warning("us_morning_news_translate_failed error=%s", exc)

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
        _e_cand: list[dict] = []
        _surge: list[dict] = []
        _support: list[dict] = []  # F. 60일선 지지(참고용) — 가중치 0, Top3 미반영
        snap.screen_picks = await collect_screen_picks(
            adapter, e_out=_e_cand, surge_out=_surge, support_out=_support)
        snap.surge_picks = sorted(_surge, key=lambda p: p.get("change_pct", 0), reverse=True)[:7]
        snap.support_picks = sorted(_support, key=lambda p: p.get("change_pct", 0), reverse=True)[:10]
        snap.holdings_status = await collect_holdings_status(adapter)
        # E전략 4시간봉 게이트 — 일봉 과매도 주도주 후보 중 4H RSI(14)≤30도 충족하는 종목만(사용자 2026-06-05)
        if _e_cand:
            try:
                from src.datasource.kr_4h import fetch_4h_rsi_oversold
                _ok = await fetch_4h_rsi_oversold([p["ticker"] for p in _e_cand], market="KR")
                snap.e_picks = [p for p in _e_cand if p["ticker"] in _ok][:7]
                try:
                    from src.datasource.us.fear_greed import fetch_fear_greed
                    snap.fear_greed = await fetch_fear_greed()  # 글로벌 공포탐욕(KR 바닥 보조, #331)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("kr_fear_greed_failed error=%s", exc)
                _fg_kr = snap.fear_greed.get("score") if snap.fear_greed else None
                _tag_market_bottom(snap.e_picks, await _market_rsi("KR"), fg_score=_fg_kr)  # 코스피/F&G 동반바닥(#330/#331/#339)
                logger.info("e_picks_ready daily=%d final=%d", len(_e_cand), len(snap.e_picks))
            except Exception as exc:
                logger.warning("e_picks_4h_failed error=%s", exc)
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

        # E/급등초입 픽에도 테마 부착(judal → 네이버 세분업종 폴백, 사용자 2026-06-05)
        try:
            _sec_picks = (snap.e_picks or []) + (snap.surge_picks or []) + (snap.support_picks or [])
            for p in _sec_picks:
                jv = jmap.get(p.get("ticker", ""))
                if jv and jv.get("theme") and not _is_nontheme(jv["theme"]):
                    p["theme"] = jv["theme"]
                    p["theme_kind"] = "theme"
                    p["theme_idx"] = jv.get("idx", "")
            _need2 = [p["ticker"] for p in _sec_picks if p.get("ticker") and not p.get("theme")]
            if _need2:
                from src.market_report.scrapers.sector import get_stock_sectors
                _sec2 = await get_stock_sectors(_need2)
                for p in _sec_picks:
                    if not p.get("theme") and _sec2.get(p.get("ticker", "")):
                        p["theme"] = _sec2[p["ticker"]]
                        p["theme_kind"] = "sector"
        except Exception as exc:  # noqa: BLE001
            logger.warning("sec_picks_theme_failed error=%s", exc)

        # 주도 테마 — 오늘 상위종목(상승률/거래량 상위) + 급등 전략픽이 속한 테마(랭킹순)
        _ranked = _rank_leading_themes((snap.top_gainers or []) + (snap.top_volume or []),
                                       snap.screen_picks, jmap, _is_nontheme)
        snap.leading_themes = _ranked[:6]
        # O/X는 표시된 주도테마(상위 6)와 일치 — 선택적
        _set_leading_theme(snap.screen_picks, {_norm_name(t) for t in snap.leading_themes})

        # 4시간봉 과열(BB상단 음봉) 부착 — Top3 후보(거래대금 상위 ~12)만 yfinance 조회(사용자 2026-06-05).
        # best-effort: 실패해도 일봉 과열만으로 진행. 전체 유니버스 조회는 부하 커서 상위만.
        try:
            from src.datasource.kr_4h import fetch_4h_overheat
            _cand = sorted(snap.screen_picks, key=lambda p: p.get("_liq", 0), reverse=True)[:12]
            _o4 = await fetch_4h_overheat([p["ticker"] for p in _cand])
            for p in snap.screen_picks:
                if _o4.get(p["ticker"]):
                    p["overheat_4h"] = True
        except Exception as exc:
            logger.warning("kr_4h_overheat_failed error=%s", exc)

        # Top3 종합추천 — 수급(외인/기관 순매수) 수집 후 P4 점수로 3종목 선정
        _kr_fb, _kr_ib = set(), set()
        try:
            from src.market_report.top3 import select_top3
            fb = {x["ticker"] for x in await adapter.get_investor_net_buy("foreign", "buy")}
            ib = {x["ticker"] for x in await adapter.get_investor_net_buy("inst", "buy")}
            _kr_fb, _kr_ib = fb, ib
            snap.top3 = select_top3(snap.screen_picks, foreign_buy=fb, inst_buy=ib)
            await _inject_supply_streak(snap, adapter)  # 연속 순매수일
            logger.info("pipeline_top3_ready top3=%s", [t["name"] for t in snap.top3])
        except Exception as exc:
            logger.warning("top3_failed error=%s", exc)

        # 🌙 시간외(NXT) 상위 상승률 — 마감 후(post_close)만. 정규장 종가 대비 NXT 변동(사용자 2026-06-05).
        if snap.mode == "post_close":
            try:
                snap.overtime_gainers = await adapter.get_nxt_overtime_gainers(top=7)
                logger.info("overtime_gainers_ready n=%d", len(snap.overtime_gainers))
            except Exception as exc:
                logger.warning("overtime_gainers_failed error=%s", exc)

        # 자동매매 브리지: 보고서 Top3를 JSON으로 남겨 auto_trader가 동일 종목 매수
        if snap.mode == "pre_close" and snap.top3:
            try:
                from datetime import datetime as _dt
                from src.trading.top3_bridge import persist_candidates, persist_top3
                _d = _dt.now().strftime("%Y-%m-%d")
                persist_top3(snap.top3, snap.mode, _d)
                if snap.candidate_picks:  # 종가베팅 후보 영속화(다음날 프리/장초 시초등락용, #404)
                    persist_candidates(snap.candidate_picks, _d)
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
        # AI 수급 요약 — 최근 일주일 개인/기관/외인 흐름·연속·전일/전주대비 (사용자 #313/#316)
        try:
            from src.market_report.analyzer import summarize_flows
            await summarize_flows(snap)
        except Exception as exc:
            logger.warning("flows_summary_skip error=%s", exc)
        # 강세 테마별 '왜 올랐나' 1~2줄 (뉴스·정책 기대감 연계 → 각 테마 description)
        try:
            from src.market_report.analyzer import summarize_themes
            await summarize_themes(snap)
        except Exception as exc:
            logger.warning("theme_summary_skip error=%s", exc)
    except Exception as exc:
        logger.error("pipeline_strategy_failed error=%s", exc)

    _inject_marcap(snap)

    if mode in ("pre_close", "post_close"):  # 코스피/코스닥 이평선 이격도(고점 판단, #357) + 신호등(#362)
        try:
            snap.ma_gaps = {"코스피": await _index_ma_gaps("KS11"), "코스닥": await _index_ma_gaps("KQ11")}
            _fill_market_phase(snap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kr_ma_gaps_failed error=%s", exc)

    if mode == "post_close":  # 기관+외인 연속 순매수/매도 Top (시총상위, 마감후 확정데이터, #393)
        try:
            snap.supply_buy_streaks, snap.supply_sell_streaks = await collect_supply_streaks(adapter)
        except Exception as exc:  # noqa: BLE001
            logger.warning("supply_streaks_failed error=%s", exc)

    # 전략 스크린 표시용 — 종목당 1개로 중복제거 + 종합점수순 + 매칭전략 다 표기(사용자 2026-06-05).
    # marcap/ai 주입 후 빌드(screen_picks가 enrich된 상태). select_top3 재사용(return_all).
    try:
        from src.market_report.top3 import select_top3 as _sel
        snap.screen_ranked = _sel(snap.screen_picks, foreign_buy=_kr_fb, inst_buy=_kr_ib, return_all=True)
    except Exception as exc:
        logger.warning("screen_ranked_failed error=%s", exc)

    # (서학개미 미국주식 순매수 TOP5는 한국장 리포트에서 제외 — 미국 데이터라 부적절, 사용자 2026-06-05.
    #  미국 리포트 종목 카드에는 서학개미 순매수 배지가 그대로 표시됨.)

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
