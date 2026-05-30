"""조건 검색 파이프라인 — 유니버스 수집 → 일봉 → 스크리닝 → 매수 의견.

흐름:
  1. 유니버스 = 관심종목 ∪ 핫종목(거래량·등락률 상위)  (CLAUDE.md §5 — 전체스캔 금지)
  2. 각 종목 일봉(100봉) 조회
  3. YAML 전략으로 스크리닝
  4. 매칭된 종목 → 매수 의견 + 근거 수치 + 면책

이 모듈은 어댑터(MarketDataSource)·repo를 주입받아 사용 (포트 의존).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from src.datasource.base import MarketDataSource, RankingKind
from src.screener.config import ScreenerConfig, load_screener_config
from src.screener.engine import ScreenMatch, screen_stock

logger = logging.getLogger(__name__)


@dataclass
class StockPick:
    """조건 검색 결과 — 한 종목의 매칭 전략 모음."""
    ticker: str
    name: str
    price: float
    change_pct: float
    matches: list[ScreenMatch] = field(default_factory=list)
    candles: list = field(default_factory=list)  # 차트 렌더용 (KIS 일봉 재사용)
    sector: str = ""              # 섹터/테마명
    is_leading_sector: bool = False  # 주도(강세) 섹터 소속 여부
    news_title: str = ""          # 최근 주요 뉴스 헤드라인
    news_url: str = ""            # 뉴스 링크

    @property
    def opinions(self) -> list[str]:
        return [m.opinion for m in self.matches if m.opinion]

    @property
    def all_reasons(self) -> list[str]:
        out: list[str] = []
        for m in self.matches:
            out.extend(m.reasons)
        return out


def _is_etf(name: str) -> bool:
    keywords = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF",
                "SOL ", "ACE ", "PLUS ", "RISE ", "TIMEFOLIO", "레버리지", "인버스", "선물")
    upper = name.upper()
    return any(k.upper() in upper for k in keywords)


async def collect_universe(
    datasource: MarketDataSource,
    watchlist_tickers: list[tuple[str, str]],
    cfg: ScreenerConfig,
) -> dict[str, str]:
    """검색 대상 유니버스 수집 → {ticker: name}.

    watchlist_tickers: [(ticker, name), ...] — repo에서 추출해 전달.
    """
    universe: dict[str, str] = {}

    if cfg.universe_watchlist:
        for ticker, name in watchlist_tickers:
            universe[ticker] = name

    if cfg.universe_hot:
        try:
            # 거래량 + 등락률 상위 합집합
            vol_rank = await datasource.get_ranking(RankingKind.VOLUME, top=cfg.hot_stocks_top)
            chg_rank = await datasource.get_ranking(RankingKind.CHANGE_PCT, top=cfg.hot_stocks_top)
            for rs in [*vol_rank, *chg_rank]:
                if rs.ticker and rs.ticker not in universe:
                    universe[rs.ticker] = rs.name
        except Exception as exc:
            logger.warning("hot_stocks_failed error=%s", exc)

    logger.info("universe_collected size=%d", len(universe))
    return universe


async def run_screening(
    datasource: MarketDataSource,
    watchlist_tickers: list[tuple[str, str]],
    cfg: ScreenerConfig | None = None,
) -> list[StockPick]:
    """전체 조건 검색 실행. 매칭된 종목만 반환."""
    cfg = cfg or load_screener_config()
    strategies = cfg.enabled_strategies()
    if not strategies:
        logger.warning("no_enabled_strategies")
        return []

    universe = await collect_universe(datasource, watchlist_tickers, cfg)
    min_price = cfg.global_filters.get("min_price", 0)
    exclude_etf = cfg.global_filters.get("exclude_etf", False)

    picks: list[StockPick] = []
    for ticker, name in universe.items():
        if exclude_etf and _is_etf(name):
            continue
        try:
            candles = await datasource.get_ohlcv(ticker, days=120)
            if len(candles) < 60:
                continue
            price = candles[-1].close
            if price < min_price:
                continue

            # 당일 등락률 (전일 종가 대비)
            change_pct = 0.0
            if len(candles) >= 2 and candles[-2].close > 0:
                change_pct = (price - candles[-2].close) / candles[-2].close * 100

            matches = screen_stock(strategies, candles, change_pct)
            if matches:
                picks.append(StockPick(
                    ticker=ticker, name=name, price=price,
                    change_pct=change_pct, matches=matches, candles=candles,
                ))
        except Exception as exc:
            logger.debug("screen_skip ticker=%s error=%s", ticker, exc)
            continue

    logger.info("screening_done universe=%d picks=%d", len(universe), len(picks))

    # 섹터·뉴스 보강 (포착 종목만 — 적은 수라 부담 작음)
    if picks:
        await _enrich_picks(picks)

    return picks


async def _enrich_picks(picks: list[StockPick]) -> None:
    """포착 종목에 섹터(주도 여부) + 최근 뉴스 1건 보강.

    - 섹터: 네이버 강세 테마의 주도주 목록과 종목명 매칭
    - 뉴스: 구글 뉴스 RSS 종목 검색 (광고 제외)
    실패해도 picks 자체는 유지 (보강 정보만 비움).
    """
    from src.market_report.scrapers.stock_news import fetch_stock_news
    from src.market_report.scrapers.theme import fetch_top_themes

    # 1. 강세 테마 + 주도주 매핑
    leading_names: dict[str, str] = {}  # 종목명 → 테마명 (강세 테마 소속)
    try:
        themes = await fetch_top_themes(top=15)
        for t in themes:
            if t.change_pct <= 0:  # 강세(상승) 테마만 주도섹터로
                continue
            for stock_name in t.leading_stocks:
                leading_names[stock_name] = t.name
    except Exception as exc:
        logger.warning("theme_enrich_failed error=%s", exc)

    # 2. 종목별 보강
    for p in picks:
        # 섹터 매칭 (종목명 부분일치)
        for lead_name, theme_name in leading_names.items():
            if lead_name in p.name or p.name in lead_name:
                p.sector = theme_name
                p.is_leading_sector = True
                break

        # 뉴스 1건
        try:
            news = await fetch_stock_news(p.name, top=1)
            if news:
                p.news_title = news[0].title
                p.news_url = news[0].url
        except Exception as exc:
            logger.debug("news_enrich_failed ticker=%s error=%s", p.ticker, exc)
