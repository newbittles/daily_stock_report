"""네이버 금융 스크래퍼 — 지수, 거래량/등락률 순위, 수급.

엔드포인트 (모두 정적 HTML, 정상 fetch 가능):
- 거래량 상위:  https://finance.naver.com/sise/sise_quant.naver?sosok={0|1}
- 상승률 상위:  https://finance.naver.com/sise/sise_rise.naver?sosok={0|1}
- 하락률 상위:  https://finance.naver.com/sise/sise_fall.naver?sosok={0|1}
- 시장 요약:    https://finance.naver.com/sise/
- 외인/기관:    https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=YYYYMMDD
- 지수:        https://finance.naver.com/sise/sise_index.naver?code=KOSPI|KOSDAQ

sosok: 0=코스피, 1=코스닥
인코딩: euc-kr
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup

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


async def _fetch_rank_page(url: str) -> tuple[pd.DataFrame, str]:
    """순위 페이지 fetch → 메인 테이블 DataFrame + raw html 반환.

    raw html은 종목코드 추출을 위해 함께 반환 (pandas는 href 못 읽음).
    """
    html = await fetch(url, encoding="euc-kr")
    tables = pd.read_html(StringIO(html))
    # 보통 가장 큰 테이블 (rows 많은 것)이 메인
    main = max(tables, key=lambda t: len(t))
    return main, html


def _extract_tickers(html: str) -> list[str]:
    """HTML 내 모든 종목코드 추출 (출현 순서대로)."""
    return _CODE_PATTERN.findall(html)


# 종목명 앵커: <a href="/item/main.naver?code=XXXXXX">종목명</a> — 코드↔이름이 같은 앵커라 정확히 정렬
_NAME_LINK_PATTERN = re.compile(r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>')


def _norm_stock_name(name: str) -> str:
    """종목명 정규화(공백 제거) — 표(pd.read_html)와 앵커 텍스트의 공백 차이 흡수."""
    return str(name or "").replace(" ", "").strip()


def _name_code_map(html: str) -> dict[str, str]:
    """정규화 종목명 → 종목코드 (앵커 기준, 첫 등장 우선).

    기존 위치매칭(tickers[i])은 거래량 페이지에서 코드 출현순이 표 행과 어긋나 ticker↔name이
    뒤섞였다(예: 069500↔LG디스플레이). 같은 앵커에서 코드·이름을 함께 뽑아 이름으로 매칭한다.
    """
    m: dict[str, str] = {}
    for code, name in _NAME_LINK_PATTERN.findall(html):
        key = _norm_stock_name(name)
        if key and key not in m:
            m[key] = code
    return m


async def fetch_top_volume(market: MarketCode = "KOSPI", top: int = 30) -> list[StockRank]:
    """거래량 상위 종목 N개."""
    url = f"{BASE}/sise/sise_quant.naver?sosok={SOSOK[market]}"
    df, html = await _fetch_rank_page(url)
    df = df.dropna(how="all")

    # 컬럼 정규화 (네이버 컬럼: N, 종목명, 현재가, 전일비, 등락률, 거래량, 거래대금, 매수호가, 매도호가, 시가총액, PER, ROE)
    df = df[df.iloc[:, 0].notna()]   # N 열이 있는 행만
    # 종목명이 텍스트인 행만 (광고 행 제거)
    name_col = df.columns[1]
    df = df[df[name_col].astype(str).str.len() > 0]

    # 종목명 → 코드 (앵커 기준, ticker↔name 정합 보장)
    name_code = _name_code_map(html)

    results: list[StockRank] = []
    for i, (_, row) in enumerate(df.head(top).iterrows()):
        name = str(row.iloc[1]).strip()
        ticker = name_code.get(_norm_stock_name(name), "")
        if not ticker:  # 코드 못 찾으면 skip (오정렬 종목 방지)
            continue
        try:
            results.append(
                StockRank(
                    rank=i + 1,
                    ticker=ticker,
                    name=name,
                    price=_to_float(row.iloc[2]),
                    change=_to_float(row.iloc[3]),
                    change_pct=_parse_pct(row.iloc[4]),
                    volume=_to_int(row.iloc[5]),
                    trade_value=_to_float(row.iloc[6]) if len(row) > 6 else 0.0,
                    market_cap=_to_float(row.iloc[9]) if len(row) > 9 else 0.0,
                )
            )
        except Exception as exc:
            logger.debug("rank_parse_skip row=%d error=%s", i, exc)
            continue
    return results


async def fetch_top_gainers(market: MarketCode = "KOSPI", top: int = 30) -> list[StockRank]:
    """상승률 상위 종목."""
    url = f"{BASE}/sise/sise_rise.naver?sosok={SOSOK[market]}"
    df, html = await _fetch_rank_page(url)
    df = df.dropna(how="all")
    df = df[df.iloc[:, 0].notna()]
    name_col = df.columns[1]
    df = df[df[name_col].astype(str).str.len() > 0]

    name_code = _name_code_map(html)
    results: list[StockRank] = []
    for i, (_, row) in enumerate(df.head(top).iterrows()):
        name = str(row.iloc[1]).strip()
        ticker = name_code.get(_norm_stock_name(name), "")
        if not ticker:
            continue
        try:
            results.append(
                StockRank(
                    rank=i + 1,
                    ticker=ticker,
                    name=name,
                    price=_to_float(row.iloc[2]),
                    change=_to_float(row.iloc[3]),
                    change_pct=_parse_pct(row.iloc[4]),
                    volume=_to_int(row.iloc[5]) if len(row) > 5 else 0,
                )
            )
        except Exception as exc:
            logger.debug("rise_parse_skip row=%d error=%s", i, exc)
            continue
    return results


async def fetch_top_losers(market: MarketCode = "KOSPI", top: int = 30) -> list[StockRank]:
    """하락률 상위 종목."""
    url = f"{BASE}/sise/sise_fall.naver?sosok={SOSOK[market]}"
    df, html = await _fetch_rank_page(url)
    df = df.dropna(how="all")
    df = df[df.iloc[:, 0].notna()]
    name_col = df.columns[1]
    df = df[df[name_col].astype(str).str.len() > 0]

    name_code = _name_code_map(html)
    results: list[StockRank] = []
    for i, (_, row) in enumerate(df.head(top).iterrows()):
        name = str(row.iloc[1]).strip()
        ticker = name_code.get(_norm_stock_name(name), "")
        if not ticker:
            continue
        try:
            results.append(
                StockRank(
                    rank=i + 1,
                    ticker=ticker,
                    name=name,
                    price=_to_float(row.iloc[2]),
                    change=_to_float(row.iloc[3]),
                    change_pct=_parse_pct(row.iloc[4]),
                    volume=_to_int(row.iloc[5]) if len(row) > 5 else 0,
                )
            )
        except Exception as exc:
            logger.debug("fall_parse_skip row=%d error=%s", i, exc)
            continue
    return results


async def fetch_index(market: MarketCode = "KOSPI") -> IndexQuote | None:
    """코스피/코스닥 지수 현재값.

    네이버 시세 메인 페이지에서 추출. (간단한 파싱 — 단일 값만)
    """
    url = f"{BASE}/sise/sise_index.naver?code={market}"
    html = await fetch(url, encoding="euc-kr")
    soup = BeautifulSoup(html, "lxml")

    try:
        # 지수 값: id="now_value" 또는 .num
        now_value_el = soup.find(id="now_value")
        if not now_value_el:
            now_value_el = soup.select_one("em#now_value")
        value = _to_float(now_value_el.get_text(strip=True)) if now_value_el else 0.0

        # 등락
        change_el = soup.find(id="change_value_and_rate")
        change_text = change_el.get_text(" ", strip=True) if change_el else ""
        # 예: "5.32 +0.20%" 또는 "▼ 3.14 -0.12%"
        parts = change_text.replace("▲", "").replace("▼", "").split()
        change = _to_float(parts[0]) if parts else 0.0
        change_pct = _parse_pct(parts[1]) if len(parts) > 1 else 0.0

        # 거래량/거래대금 (페이지 우측 표)
        tables = soup.find_all("table")
        volume, trade_value = 0, 0.0
        for table in tables:
            text = table.get_text(" ", strip=True)
            if "거래량" in text and "거래대금" in text:
                # 가장 단순한 파싱: 숫자만 추출
                nums = re.findall(r"[\d,]+", text)
                if len(nums) >= 2:
                    volume = _to_int(nums[0])
                    trade_value = _to_float(nums[1])
                break

        return IndexQuote(
            market=market,
            value=value,
            change=change,
            change_pct=change_pct,
            volume=volume,
            trade_value=trade_value,
            timestamp=datetime.now(),
        )
    except Exception as exc:
        logger.warning("index_parse_failed market=%s error=%s", market, exc)
        return None


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
