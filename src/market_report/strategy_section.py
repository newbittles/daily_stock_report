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
from src.screener.config import load_screener_config
from src.screener.engine import evaluate_strategy
from src.screener.pipeline import _is_etf

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
        if exclude_etf and _is_etf(nm):
            continue
        await asyncio.sleep(random.uniform(0.2, 0.5))
        try:
            c = await adapter.get_ohlcv(tk, days=180)
        except Exception:
            continue
        if len(c) < 135 or c[-1].close < min_price:
            continue
        change_pct = (c[-1].close - c[-2].close) / c[-2].close * 100 if len(c) >= 2 and c[-2].close else 0.0
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
                    "theme": "",            # pipeline에서 테마 역매핑/업종 폴백으로 채움
                    "theme_kind": "",       # "theme"(테마) | "sector"(세분업종)
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
