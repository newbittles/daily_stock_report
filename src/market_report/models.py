"""Daily market report — 데이터 모델.

전 스크래퍼가 공통으로 사용하는 dataclass 정의. 모두 immutable(frozen=False지만 mutate 금지)로 취급.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ReportMode = Literal["pre_close", "post_close"]
MarketCode = Literal["KOSPI", "KOSDAQ"]


@dataclass
class IndexQuote:
    """지수 시세 스냅샷."""
    market: MarketCode        # KOSPI | KOSDAQ
    value: float              # 지수 값 (예: 2700.45)
    change: float             # 전일비 (절댓값)
    change_pct: float         # 등락률 % (+/-)
    volume: int               # 거래량 (주)
    trade_value: float        # 거래대금 (백만원 단위로 통일)
    timestamp: datetime


@dataclass
class StockRank:
    """순위 종목 (거래량/상승률/하락률 공통)."""
    rank: int
    ticker: str               # 6자리 종목코드 — 네이버 페이지에서 추출
    name: str
    price: float
    change: float             # 전일비
    change_pct: float
    volume: int
    trade_value: float = 0.0  # 거래대금 (있으면)
    market_cap: float = 0.0   # 시가총액 (백만원, 있으면)


@dataclass
class InvestorFlow:
    """투자자별 수급 (외국인/기관/개인)."""
    market: MarketCode
    foreign_net: float        # 외국인 순매수 (백만원, +매수 / -매도)
    institution_net: float    # 기관 순매수
    individual_net: float     # 개인 순매수
    date: str                 # YYYY-MM-DD


@dataclass
class ThemeRank:
    """테마/업종 강세 순위."""
    rank: int
    name: str                 # 테마명
    change_pct: float         # 테마 평균 등락률
    leading_stocks: list[str] = field(default_factory=list)  # 주도주 종목명 (Top 3)
    description: str = ""     # 테마 설명 (있으면)
    reason: str = ""          # AI가 채움 — 왜 강한지/약한지 (뉴스·매크로 근거)


@dataclass
class NewsItem:
    """관련 뉴스."""
    title: str
    url: str
    source: str               # 매체명 (한경, 매경 등)
    published_at: str         # 게시 시각 (raw string)
    related_tickers: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class MarketSnapshot:
    """리포트 생성 시점의 시장 전체 스냅샷 — 분석기·렌더러 입력."""
    mode: ReportMode
    generated_at: datetime

    # 지수
    kospi: IndexQuote | None = None
    kosdaq: IndexQuote | None = None

    # 종목 순위
    top_volume: list[StockRank] = field(default_factory=list)
    top_gainers: list[StockRank] = field(default_factory=list)
    top_losers: list[StockRank] = field(default_factory=list)

    # 수급 (마감 후만 안정적, 마감 전은 누적)
    flows: list[InvestorFlow] = field(default_factory=list)

    # 테마
    top_themes: list[ThemeRank] = field(default_factory=list)

    # 뉴스
    market_news: list[NewsItem] = field(default_factory=list)

    # AI 분석 결과 (analyzer가 채움)
    summary: str = ""           # 시장 한줄 요약
    why_moved: str = ""         # 왜 올랐나/내렸나
    theme_commentary: str = ""  # 강세 테마 해설
    candidate_picks: list[dict] = field(default_factory=list)  # 종가베팅 후보 (pre_close용)

    # 전략 스크린 결과 (A/B/C — 오늘 포착, pipeline이 KIS로 채움)
    screen_picks: list[dict] = field(default_factory=list)   # {strategy, ticker, name, price, reason, endstage}
    # 보유종목 상태 (홀딩/손절/추가매수 — pipeline이 채움)
    holdings_status: list[dict] = field(default_factory=list)  # {name, ticker, state, reason, profit_rate, ...}

    # 차트 URL (renderer가 채움, 상대 경로 — docs/reports/ 기준)
    kospi_spark_url: str = ""
    kosdaq_spark_url: str = ""
