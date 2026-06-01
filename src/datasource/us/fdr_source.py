"""미국 증시 시세 — FinanceDataReader 기반 (us_morning 리포트용).

KIS 해외 API 대신 무료 FDR 사용. 미국장 마감 후(한국 아침) 지연 일봉으로
지수·빅테크·섹터 ETF 등락을 수집한다. 동기 라이브러리라 asyncio.to_thread로 감싼다.
FDR 심볼 검증 완료(2026-06-02): US500·IXIC·DJI·^SOX·NVDA·AAPL·TSLA 등.

design: docs/02-design/features/us-morning-report.design.md (U1 데이터소스=FDR)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 지수 (심볼 → 표시명)
US_INDICES = {
    "US500": "S&P500",
    "IXIC": "나스닥",
    "DJI": "다우",
    "^SOX": "필라델피아반도체",
}

# 빅테크 + 주요 종목 (심볼 → 한글명)
US_BIGTECH = {
    "NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트",
    "GOOGL": "알파벳", "AMZN": "아마존", "META": "메타", "TSLA": "테슬라",
    "AVGO": "브로드컴", "AMD": "AMD", "NFLX": "넷플릭스",
}

# 섹터/테마 대표 ETF (강세 테마 추출용) — 심볼 → 테마명
US_SECTORS = {
    "SOXX": "반도체", "XLK": "기술/IT", "XLE": "에너지", "XLF": "금융",
    "XLV": "헬스케어/바이오", "XLY": "경기소비재", "ITA": "방산/우주항공",
    "TAN": "태양광/신재생", "LIT": "2차전지/리튬",
}


@dataclass(frozen=True)
class USQuote:
    symbol: str
    name: str
    price: float
    change_pct: float  # 전일 대비 등락률(%)


def _fetch_quotes_sync(symbols: dict[str, str]) -> list[USQuote]:
    """동기 — 각 심볼 최근 2영업일 종가로 등락률 계산."""
    import FinanceDataReader as fdr
    from datetime import datetime, timedelta

    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    out: list[USQuote] = []
    for sym, name in symbols.items():
        try:
            df = fdr.DataReader(sym, start)
            if df is None or len(df) < 2 or "Close" not in df.columns:
                continue
            closes = df["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            chg = (last - prev) / prev * 100 if prev else 0.0
            out.append(USQuote(sym, name, round(last, 2), round(chg, 2)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("us_fetch_failed symbol=%s error=%s", sym, exc)
    return out


async def fetch_us_indices() -> list[USQuote]:
    """미국 주요 지수 등락 (S&P500·나스닥·다우·SOX)."""
    return await asyncio.to_thread(_fetch_quotes_sync, US_INDICES)


async def fetch_us_bigtech() -> list[USQuote]:
    """빅테크/주요 종목 등락 → 상승률 내림차순."""
    quotes = await asyncio.to_thread(_fetch_quotes_sync, US_BIGTECH)
    return sorted(quotes, key=lambda q: q.change_pct, reverse=True)


async def fetch_us_sectors(threshold: float = 1.0) -> list[USQuote]:
    """섹터 ETF 등락 → 강세 섹터(>=threshold%) 내림차순. 미국 강세테마 추출용."""
    quotes = await asyncio.to_thread(_fetch_quotes_sync, US_SECTORS)
    strong = [q for q in quotes if q.change_pct >= threshold]
    return sorted(strong, key=lambda q: q.change_pct, reverse=True)
