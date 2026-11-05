"""네이버 금융 스크래퍼 — 지수, 거래량/등락률 순위, 수급.

엔드포인트 (2026-11-05 모바일 JSON 으로 전환 — 데스크톱 sise 페이지는 Next.js 라 표가 없다):
- 거래량/상승/하락: https://m.stock.naver.com/api/stocks/{quantTop|up|down}/{KOSPI|KOSDAQ}
- 지수:            https://m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/basic
- 시장 수급:        https://m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/trend
- 외인/기관(옛):    https://finance.naver.com/sise/investorDealTrendDay.naver

sosok: 0=코스피, 1=코스닥
인코딩: euc-kr
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from io import StringIO

import pandas as pd

from src.market_report.http import fetch
from src.market_report.models import (
    IndexQuote,
    InvestorFlow,
    MarketCode,
    StockRank,
)

logger = logging.getLogger(__name__)

BASE = "https://finance.naver.com"
SOSOK = {"KOSPI": "0", "KOSDAQ": "1"}

# 종목코드 추출용 (a href="/item/main.naver?code=005930")
_CODE_PATTERN = re.compile(r"code=(\d{6})")


def _to_float(value) -> float:
    """콤마·부호 제거 후 float."""
    if pd.isna(value):
        return 0.0
    s = str(value).replace(",", "").replace("+", "").strip()
    # "상승 70" 같은 형식 처리
    s = re.sub(r"[가-힣\s]+", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_int(value) -> int:
    return int(_to_float(value))


def _parse_pct(value) -> float:
    """등락률 문자열 → float (예: '+3.21%' → 3.21, '-1.34%' → -1.34)."""
    if pd.isna(value):
        return 0.0
    s = str(value).replace("%", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


# 2026-10 네이버 금융 데스크톱 sise 페이지가 Next.js 로 바뀌어 표·id 가 사라짐(지수 0.0·순위 0건 사고,
# 2026-11-05 실측) → 모바일 JSON API 로 전환. 단위는 옛 표와 같다(거래대금=백만원, 시총=억원).
MOBILE_API = "https://m.stock.naver.com/api"
_RANK_PATH = {"volume": "quantTop", "rise": "up", "fall": "down"}


def parse_mobile_stocks(data: dict, top: int, *, stocks_only: bool = False) -> list[StockRank]:
    """모바일 순위 JSON → StockRank (순수·테스트가능). stocks_only=True 면 ETF·ETN 제외."""
    out: list[StockRank] = []
    for s in data.get("stocks") or []:
        if stocks_only and s.get("stockEndType") != "stock":
            continue
        code = str(s.get("itemCode") or "")
        if not _CODE_PATTERN.fullmatch(f"code={code}"):
            continue
        out.append(StockRank(
            rank=len(out) + 1,
            ticker=code,
            name=str(s.get("stockName") or "").strip(),
            price=_to_float(s.get("closePrice")),
            change=_to_float(s.get("compareToPreviousClosePrice")),
            change_pct=_parse_pct(s.get("fluctuationsRatio")),
            volume=_to_int(s.get("accumulatedTradingVolume")),
            trade_value=_to_float(s.get("accumulatedTradingValue")),
            market_cap=_to_float(s.get("marketValue")),
        ))
        if len(out) >= top:
            break
    return out


async def _fetch_mobile_rank(kind: str, market: MarketCode, top: int, *, stocks_only: bool) -> list[StockRank]:
    import json
    # ETF·ETN·영문 섞인 신규 코드가 걸러질 몫까지 넉넉히(한 번에 100개까지 줌)
    url = f"{MOBILE_API}/stocks/{_RANK_PATH[kind]}/{market}?page=1&pageSize=100"
    raw = await fetch(url, encoding="utf-8")
    return parse_mobile_stocks(json.loads(raw), top, stocks_only=stocks_only)


async def fetch_top_volume(market: MarketCode = "KOSPI", top: int = 30) -> list[StockRank]:
    """거래량 상위 종목 N개 (ETF 포함 — 옛 sise_quant 와 동일)."""
    return await _fetch_mobile_rank("volume", market, top, stocks_only=False)


async def fetch_top_gainers(market: MarketCode = "KOSPI", top: int = 30) -> list[StockRank]:
    """상승률 상위 종목 (ETF·ETN 제외)."""
    return await _fetch_mobile_rank("rise", market, top, stocks_only=True)


async def fetch_top_losers(market: MarketCode = "KOSPI", top: int = 30) -> list[StockRank]:
    """하락률 상위 종목 (ETF·ETN 제외)."""
    return await _fetch_mobile_rank("fall", market, top, stocks_only=True)


def _parse_eok(value: str) -> int:
    """'+63,537' / '-546' → 정수(억). 부호·콤마 처리."""
    s = str(value or "").replace(",", "").replace("+", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return 0


async def fetch_market_investor_flows() -> list[dict]:
    """시장(코스피·코스닥) 투자자별 순매수 — 개인/외국인/기관 (억 단위).

    소스: naver 모바일 API m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/trend (JSON, live).
    데스크탑 investorDealTrendDay는 JS동적이라 정적 파싱 불가 → 모바일 API 사용.
    반환: [{market, personal, foreign, institution, date}] (순매수 억, +매수/-매도). 실패 시 빈 리스트.
    """
    import json
    out: list[dict] = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            raw = await fetch(f"https://m.stock.naver.com/api/index/{market}/trend", encoding="utf-8")
            d = json.loads(raw)
            out.append({
                "market": market,
                "personal": _parse_eok(d.get("personalValue")),
                "foreign": _parse_eok(d.get("foreignValue")),
                "institution": _parse_eok(d.get("institutionalValue")),
                "date": str(d.get("bizdate", "")).strip(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_investor_flows_failed market=%s error=%s", market, exc)
    return out


def parse_mobile_index(market: MarketCode, data: dict) -> IndexQuote | None:
    """모바일 지수 JSON(/api/index/{m}/basic) → IndexQuote. 값이 없으면 None(0.0 지수를 내보내지 않는다)."""
    value = _to_float(data.get("closePrice"))
    if not value:
        return None
    return IndexQuote(
        market=market,
        value=value,
        change=_to_float(data.get("compareToPreviousClosePrice")),
        change_pct=_parse_pct(data.get("fluctuationsRatio")),
        volume=0,
        trade_value=0.0,
        timestamp=datetime.now(),
    )


async def fetch_index(market: MarketCode = "KOSPI") -> IndexQuote | None:
    """코스피/코스닥 지수 현재값 — 모바일 API(2026-11-05, 데스크톱 페이지 Next.js 전환으로 이전)."""
    import json
    try:
        raw = await fetch(f"{MOBILE_API}/index/{market}/basic", encoding="utf-8")
        q = parse_mobile_index(market, json.loads(raw))
    except Exception as exc:  # noqa: BLE001
        logger.warning("index_fetch_failed market=%s error=%s", market, exc)
        return None
    if q is None:
        logger.warning("index_parse_empty market=%s", market)
    return q


async def fetch_investor_flow(market: MarketCode = "KOSPI") -> InvestorFlow | None:
    """투자자별 매매 동향 (외국인/기관/개인). 일별 누적.

    네이버 sise/investorDealTrendDay 페이지 파싱.
    마감 후 안정적, 마감 전엔 누적값 (실시간성 제한).
    """
    url = f"{BASE}/sise/investorDealTrendDay.naver"
    html = await fetch(url, encoding="euc-kr")

    try:
        tables = pd.read_html(StringIO(html))
        # 첫 번째 테이블이 보통 일자별 수급
        df = tables[0].dropna(how="all")
        if df.empty:
            return None

        # 가장 최근 행 = 첫 행
        row = df.iloc[0]
        return InvestorFlow(
            market=market,
            foreign_net=_to_float(row.get("외국인", row.iloc[2] if len(row) > 2 else 0)),
            institution_net=_to_float(row.get("기관계", row.iloc[3] if len(row) > 3 else 0)),
            individual_net=_to_float(row.get("개인", row.iloc[1] if len(row) > 1 else 0)),
            date=str(row.iloc[0]).strip(),
        )
    except Exception as exc:
        logger.warning("investor_flow_parse_failed error=%s", exc)
        return None
