"""Daily market report — 데이터 모델.

전 스크래퍼가 공통으로 사용하는 dataclass 정의. 모두 immutable(frozen=False지만 mutate 금지)로 취급.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ReportMode = Literal["pre_close", "post_close", "us_morning", "midday", "us_premarket"]
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
    # 매크로 (지수 2x2 매트릭스 — 환율/유가)
    fx: dict | None = None    # {name, value, change_pct} USD/KRW
    wti: dict | None = None   # {name, value, change_pct} WTI 유가
    gold: dict | None = None  # {name, value, change_pct} 금(GC=F)
    candle_urls: dict = field(default_factory=dict)  # {key: 미니 캔들 이미지 URL} 지수·환율·유가·금·미국

    # 종목 순위
    top_volume: list[StockRank] = field(default_factory=list)
    top_gainers: list[StockRank] = field(default_factory=list)
    top_losers: list[StockRank] = field(default_factory=list)

    # 수급 (마감 후만 안정적, 마감 전은 누적)
    flows: list[InvestorFlow] = field(default_factory=list)
    market_flows: list[dict] = field(default_factory=list)  # 당일 수급: [{market,personal,foreign,institution,date}] 억
    market_flows_history: list[dict] = field(default_factory=list)  # 최근 3일 일자별: [{date,kospi,kosdaq}] 최신순

    # 테마
    top_themes: list[ThemeRank] = field(default_factory=list)
    leading_themes: list[str] = field(default_factory=list)  # 주도 테마(오늘 상위종목이 속한 테마, 랭킹순)

    # 뉴스
    market_news: list[NewsItem] = field(default_factory=list)

    # 미국 증시 (us_morning 모드 — USMarketSource가 채움)
    us_indices: list[dict] = field(default_factory=list)   # {symbol, name, price, change_pct}
    us_bigtech: list[dict] = field(default_factory=list)   # 빅테크/주요종목 등락
    us_sectors: list[dict] = field(default_factory=list)        # 강세 섹터 ETF (상승률순 top)
    us_weak_sectors: list[dict] = field(default_factory=list)   # 약세 섹터 ETF (하락률순 4)
    us_volume_sectors: list[dict] = field(default_factory=list)  # (deprecated — 강세/약세로 통합)
    us_news: list[dict] = field(default_factory=list)          # 미국 시장 뉴스 헤드라인 [{title, source}]
    # 미국 종목 스크리닝 (us_morning — A/B/C/D 미국 종목. 한국 top3/screen_picks와 분리)
    us_top3: list[dict] = field(default_factory=list)         # {symbol, name, price, change_pct, sector, reason, cross_signal}
    us_sector_leaders: list[dict] = field(default_factory=list)  # 강세/약세 섹터별 대장주(주요 종목)
    us_theme_leaders: list[dict] = field(default_factory=list)  # 관심 테마(양자·우주·AI 등) 대장 — 별도
    us_screen_groups: list[dict] = field(default_factory=list)  # [{label, initial, picks:[...]}] 전략별(C/B/A/D)
    # 서학개미(한국인) 미국주식 종목별 순매수 — SEIBro, 최근 5거래일 누적 (pre/post 둘 다 표시)
    kr_us_netbuy: list[dict] = field(default_factory=list)  # [{rank, ticker, name, net_buy_eok, net_buy_usd}]

    # AI 분석 결과 (analyzer가 채움)
    summary: str = ""           # 시장 한줄 요약
    why_moved: str = ""         # 왜 올랐나/내렸나
    theme_commentary: str = ""  # 강세 테마 해설
    candidate_picks: list[dict] = field(default_factory=list)  # 종가베팅 후보 (pre_close용)

    # Top3 종합 추천 (A/B/C/D + 주도주·거래량·수급 종합 → 딱 3종목, pipeline이 채움)
    top3: list[dict] = field(default_factory=list)           # {ticker, name, price, change_pct, score, reason, ...}
    # 전략 스크린 결과 (A/B/C — 오늘 포착, pipeline이 KIS로 채움)
    screen_picks: list[dict] = field(default_factory=list)   # {strategy, ticker, name, price, reason, endstage}
    # 보유종목 상태 (홀딩/손절/추가매수 — pipeline이 채움)
    holdings_status: list[dict] = field(default_factory=list)  # {name, ticker, state, reason, profit_rate, ...}
    holdings_summary: str = ""  # 보유종목 전체에 대한 AI 종합 코멘트 (analyzer.summarize_holdings)

    # 핫종목 — 거래대금 상위 + 시총 하한 필터 (거래대금 전일대비·순매수 연속일·소속테마)
    hot_stocks: list[dict] = field(default_factory=list)
    # {ticker, name, price, change_pct, marcap, theme, tv_change(거래대금전일대비%), streak:{orgn,frgn,prsn}}

    # 장중 리포트(midday) — 전날 추천 Top3의 현재 상태 (top3_status가 채움)
    prev_top3_date: str = ""    # 전날 top3 추천일(YYYY-MM-DD)
    prev_top3_status: list[dict] = field(default_factory=list)
    # {ticker, name, rec_price, cur_price, return_pct(추천가대비), today_pct(오늘등락)}

    # 차트 URL (renderer가 채움, 상대 경로 — docs/reports/ 기준)
    kospi_spark_url: str = ""
    kosdaq_spark_url: str = ""
