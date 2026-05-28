from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Quote:
    ticker: str
    name: str
    price: float
    change_pct: float   # 등락률 (%)
    volume: int
    timestamp: str      # YYYYMMDD or ISO-8601


@dataclass(frozen=True)
class Candle:
    date: str           # YYYYMMDD
    open: float
    high: float
    low: float
    close: float
    volume: int


class RankingKind(Enum):
    CHANGE_PCT = "change_pct"    # 등락률 순위
    VOLUME = "volume"            # 거래량 순위
    TRADE_VALUE = "trade_value"  # 거래대금 순위


@dataclass(frozen=True)
class RankedStock:
    rank: int
    ticker: str
    name: str
    price: float
    change_pct: float
    volume: int


@runtime_checkable
class MarketDataSource(Protocol):
    async def get_quote(self, ticker: str) -> Quote: ...
    async def get_ohlcv(self, ticker: str, days: int) -> list[Candle]: ...
    async def get_ranking(self, kind: RankingKind, top: int = 20) -> list[RankedStock]: ...
