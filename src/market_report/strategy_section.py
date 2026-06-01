"""리포트용 전략(A/B/C) 스크린 + 보유종목 상태 수집.

일일 리포트(마감 전/후)에 '오늘 A/B/C 포착 종목' + '보유종목 상태'를 포함시키기 위한
수집 헬퍼. KIS 어댑터로 유니버스를 평가하고, 보유종목은 KIS 잔고 → 비면 config/holdings.yaml.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import yaml

from src.alerts.holdings_report import diagnose_holdings
from src.indicators.core import average_true_range, moving_average
from src.screener.config import load_screener_config
from src.screener.engine import evaluate_strategy
from src.screener.pipeline import _is_etf, _is_pref

logger = logging.getLogger(__name__)

_HOLDINGS_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "holdings.yaml"

# 주도주 보강 (대시보드와 동일)
_LEADERS = {
    "000660": "SK하이닉스", "005930": "삼성전자", "009150": "삼성전기",
    "011070": "LG이노텍", "066570": "LG전자", "005380": "현대차",
    "307950": "현대오토에버", "018260": "삼성에스디에스",
}


def _watchlist_tickers() -> list[tuple[str, str]]:
    """관심종목(SQLite watchlist) → [(ticker, name)]. 실패/빈 경우 []."""
    try:
        from src.config.settings import get_settings
        from src.storage.db import get_connection
        from src.storage.repos import WatchlistRepo
        conn = get_connection(get_settings().db_path)
        items = WatchlistRepo(conn).get_all()
        conn.close()
        return [(w.ticker, w.name) for w in items]
    except Exception as exc:
        logger.warning("watchlist_load_failed error=%s", exc)
        return []


def load_manual_holdings() -> list[dict]:
    """config/holdings.yaml의 수동 보유종목 로드. 없으면 빈 리스트."""
    if not _HOLDINGS_CONFIG.exists():
        return []
    try:
        raw = yaml.safe_load(_HOLDINGS_CONFIG.read_text(encoding="utf-8")) or {}
        return [h for h in raw.get("holdings", []) if isinstance(h, dict) and h.get("ticker")]
    except Exception as exc:
        logger.warning("holdings_config_load_failed error=%s", exc)
        return []


async def collect_screen_picks(adapter, per_strategy: int = 8) -> list[dict]:
    """오늘 A/B/C 전략 포착 종목 (유니버스: 주도주 + 핫종목)."""
    cfg = load_screener_config()
    strategies = cfg.enabled_strategies()
    min_price = cfg.global_filters.get("min_price", 0)
    exclude_etf = cfg.global_filters.get("exclude_etf", False)

    universe = dict(_LEADERS)
    try:
        from src.datasource.base import RankingKind
        for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
            for r in await adapter.get_ranking(kind, top=cfg.hot_stocks_top):
                if r.ticker and r.ticker not in universe:
                    universe[r.ticker] = r.name
    except Exception as exc:
        logger.warning("screen_picks_ranking_failed error=%s", exc)

    # 관심종목(watchlist) 추가 — B(급등후 눌림)는 당일 핫종목보다 관심종목에서 잘 잡힘
    for tk, nm in _watchlist_tickers():
        if tk not in universe:
            universe[tk] = nm

    # 시총 상위 풀 (HTS식 디텍팅) — config market_cap: true
    if getattr(cfg, "universe_market_cap", False):
        try:
            from src.datasource.universe import get_market_cap_universe
            cap = await asyncio.to_thread(
                get_market_cap_universe, cfg.market_cap_kospi, cfg.market_cap_kosdaq,
                getattr(cfg, "market_cap_min_amount", 0))
            for tk, nm in cap:
                if tk not in universe:
                    universe[tk] = nm
        except Exception as exc:
            logger.warning("screen_picks_marketcap_failed error=%s", exc)

    counts: dict[str, int] = {}
    picks: list[dict] = []
    for tk, nm in universe.items():
        if _is_pref(nm) or (exclude_etf and _is_etf(nm)):  # 우선주 항상 제외
            continue
        await asyncio.sleep(random.uniform(0.2, 0.5))
        try:
            c = await adapter.get_ohlcv(tk, days=180)
        except Exception:
            continue
        if len(c) < 135 or c[-1].close < min_price:
            continue
        change_pct = (c[-1].close - c[-2].close) / c[-2].close * 100 if len(c) >= 2 and c[-2].close else 0.0
        # Top3 종합점수용 지표 (거래대금·20선이격·신고가근접)
        from math import log10
        _closes = [x.close for x in c]
        _ma20 = moving_average(_closes, 20)[-1]
        _liq = log10(max(c[-1].close * c[-1].volume, 1))
        _gap20 = (c[-1].close - _ma20) / _ma20 * 100 if _ma20 else 0.0
        _hi60 = max(x.high for x in c[-60:])
        _nh = (c[-1].close / _hi60 - 0.97) * 100 if _hi60 else 0.0
        # ATR(변동성) 기반 손절가 — 현재가 - 1.5×ATR. 급등주는 넓게, 안정주는 좁게 자동.
        # 배수 1.5: 한 달 백테스트상 종가베팅 다음날 손절 7.4%(2.0×는 0% 무의미, 1.0×는 18.5% 휩쏘 과다)
        _atr = average_true_range([x.high for x in c], [x.low for x in c], _closes, 14)
        _price = c[-1].close
        if _atr and _price:
            _stop_price = max(_price - 1.5 * _atr, 0.0)
            _stop_pct = (_stop_price - _price) / _price * 100  # 음수 = 하락 손절폭
        else:
            _stop_price, _stop_pct = 0.0, 0.0
        for s in strategies:
            if counts.get(s.name, 0) >= per_strategy:
                continue
            res = evaluate_strategy(s.name, s.opinion, s.conditions, c, change_pct)
            if res.matched:
                picks.append({
                    "strategy": s.name,
                    "ticker": tk, "name": nm,
                    "price": round(c[-1].close, 1),
                    "change_pct": round(change_pct, 2),
                    "reason": "; ".join(res.reasons),
                    "endstage": bool(res.metrics.get("endstage")),
                    "_liq": round(_liq, 2), "_gap20": round(_gap20, 1), "_nh": round(_nh, 2),
                    "stop_price": round(_stop_price, 1) if _stop_price else 0,
                    "stop_pct": round(_stop_pct, 1),
                    "theme": "",            # pipeline에서 judal 테마/업종 폴백으로 채움
                    "theme_kind": "",       # "theme"(judal 테마) | "sector"(네이버 세분업종)
                    "theme_idx": "",        # judal themeIdx (테마 링크용)
                    "is_theme_leader": False,
                })
                counts[s.name] = counts.get(s.name, 0) + 1
    return picks


async def collect_holdings_status(adapter) -> list[dict]:
    """보유종목 상태 — KIS 잔고 우선, 비면 config 수동 보유종목."""
    try:
        balance = await adapter.get_balance()
    except Exception as exc:
        logger.warning("holdings_balance_failed error=%s", exc)
        balance = []
    holdings = balance if balance else load_manual_holdings()
    if not holdings:
        return []
    return await diagnose_holdings(adapter, holdings=holdings)
