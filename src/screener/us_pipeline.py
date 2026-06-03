"""미국 종목 스크리닝 파이프라인 — S&P500 유니버스에 A/B/C/D 전략 적용.

흐름 (CLAUDE.md §5 — 전체스캔 금지: S&P500 명시 풀):
  1. 유니버스 = get_sp500_universe()  (심볼 + GICS 섹터/산업)
  2. yfinance 배치로 일봉(120봉) 일괄 수집 → {sym: [Candle]}
  3. min_price·거래대금·ETF 1차 필터 → screen_stock(engine 재사용)으로 전략 매칭
  4. 매칭 종목 → USStockPick (섹터 + 근거 수치 + 면책)

engine.py·indicators·patterns 는 순수 재사용(수정 0). 한국 pipeline.py 와 독립.
design: docs/02-design/features/us-screening.design.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.datasource.base import Candle
from src.datasource.us.fdr_source import fetch_us_ohlcv_batch
from src.datasource.us.universe import USStock, get_sp500_universe
from src.patterns.core import cross_signal
from src.screener.config import ScreenerConfig, load_screener_config
from src.screener.engine import ScreenMatch, screen_stock

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCREENER_US_PATH = _PROJECT_ROOT / "config" / "screener_us.yaml"

# 미국 ETF/ETN 명칭 키워드 (S&P500엔 거의 없으나 안전장치)
_US_ETF_KEYWORDS = (" ETF", " ETN", " FUND", " TRUST", "ISHARES", "SPDR", "INVESCO QQQ")

DISCLAIMER = "※ 참고용 시그널 · 매수 추천 아님 · 투자 판단과 책임은 본인."


@dataclass
class USStockPick:
    """미국 조건 검색 결과 — 한 종목의 매칭 전략 모음."""
    symbol: str
    name: str
    price: float
    change_pct: float
    sector: str = ""
    industry: str = ""
    matches: list[ScreenMatch] = field(default_factory=list)
    candles: list[Candle] = field(default_factory=list)
    cross_signal: str = ""   # ""|"pullback"(🟢단기눌림)|"correction"(⚠️조정시작) — 대세상승주 보조신호

    @property
    def opinions(self) -> list[str]:
        return [m.opinion for m in self.matches if m.opinion]

    @property
    def all_reasons(self) -> list[str]:
        out: list[str] = []
        for m in self.matches:
            out.extend(m.reasons)
        return out


def _is_us_etf(name: str) -> bool:
    upper = (name or "").upper()
    return any(k in upper for k in _US_ETF_KEYWORDS)


async def run_us_screening(
    cfg: ScreenerConfig | None = None,
    universe: list[USStock] | None = None,
    days: int = 120,
) -> list[USStockPick]:
    """미국 S&P500 조건 검색 실행. 매칭된 종목만 반환.

    cfg: None이면 config/screener_us.yaml 로드.
    universe: None이면 get_sp500_universe() (테스트 시 주입 가능).
    """
    cfg = cfg or load_screener_config(SCREENER_US_PATH)
    strategies = cfg.enabled_strategies()
    if not strategies:
        logger.warning("us_no_enabled_strategies")
        return []

    uni = universe if universe is not None else get_sp500_universe()
    if not uni:
        logger.warning("us_universe_empty")
        return []

    min_price = cfg.global_filters.get("min_price", 0)
    exclude_etf = cfg.global_filters.get("exclude_etf", False)

    symbols = [u.symbol for u in uni if not (exclude_etf and _is_us_etf(u.name))]
    ohlcv = await fetch_us_ohlcv_batch(symbols, days=days)
    meta = {u.symbol: u for u in uni}

    picks: list[USStockPick] = []
    for sym in symbols:
        candles = ohlcv.get(sym, [])
        if len(candles) < 60:
            continue
        price = candles[-1].close
        if price < min_price:
            continue

        change_pct = 0.0
        if len(candles) >= 2 and candles[-2].close > 0:
            change_pct = (price - candles[-2].close) / candles[-2].close * 100

        matches = screen_stock(strategies, candles, change_pct)
        if matches:
            u = meta.get(sym)
            picks.append(USStockPick(
                symbol=sym,
                name=u.name if u else sym,
                price=price,
                change_pct=change_pct,
                sector=u.sector if u else "",
                industry=u.industry if u else "",
                matches=matches,
                candles=candles,
                cross_signal=cross_signal(candles),
            ))

    logger.info("us_screening_done universe=%d picks=%d", len(symbols), len(picks))
    return picks
