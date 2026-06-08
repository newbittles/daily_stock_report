"""미국 야간 시세 — 나스닥 선물 + M7 (한국 리포트 최상단용, 사용자 #476).

한국 아침/장중엔 미국 정규장이 마감된 상태 → 나스닥 선물(NQ=F, 거의 24h 거래)과
M7 애프터/현재가로 '간밤 미국 분위기'를 보여준다. yfinance fast_info(실시간성, info보다 가벼움).
change_pct = 마지막가(애프터·선물 포함) ÷ 전일 정규장 종가 - 1.

§7: 종목 분산 딜레이, 실패는 부분 결과(리포트 best-effort). 동기 yfinance라 to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

logger = logging.getLogger(__name__)

# 지수 선물 (사용자 #476 나스닥 + #497 프리장 지수=선물 실시간)
_FUTURES = {"NQ=F": "나스닥 선물", "ES=F": "S&P500 선물"}

# M7 — 표시 순서는 등락률순으로 정렬되므로 여기선 정의용
_M7 = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "알파벳",
    "AMZN": "아마존", "META": "메타", "TSLA": "테슬라",
}

# M7 아래 별도 표시 ETF (사용자 #485) — 반도체 3배 레버리지
_ETF = {"SOXL": "SOXL(반도체 3X)"}


def _fetch_one(sym: str) -> dict | None:
    """단일 심볼 fast_info → {price, change_pct}. 전일종가≤0/실패 시 None."""
    import yfinance as yf

    fi = yf.Ticker(sym).fast_info
    last = float(fi.last_price)
    prev = float(fi.previous_close)
    if prev <= 0 or last <= 0:
        return None
    return {"price": round(last, 2), "change_pct": round((last / prev - 1) * 100, 2)}


def _fetch_sync() -> dict:
    fut: list[dict] = []
    for sym, name in _FUTURES.items():
        try:
            q = _fetch_one(sym)
            if q:
                fut.append({"symbol": sym, "name": name, **q})
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnight_futures_failed sym=%s error=%s", sym, exc)
        time.sleep(random.uniform(0.2, 0.5))  # §7 분산 딜레이

    m7: list[dict] = []
    for sym, name in _M7.items():
        try:
            q = _fetch_one(sym)
            if q:
                m7.append({"symbol": sym, "name": name, **q})
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnight_m7_failed sym=%s error=%s", sym, exc)
        time.sleep(random.uniform(0.2, 0.5))
    m7.sort(key=lambda x: x["change_pct"], reverse=True)  # 등락률 내림차순

    etf: list[dict] = []  # SOXL 등 — M7 아래 별도(#485)
    for sym, name in _ETF.items():
        try:
            q = _fetch_one(sym)
            if q:
                etf.append({"symbol": sym, "name": name, **q})
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnight_etf_failed sym=%s error=%s", sym, exc)
        time.sleep(random.uniform(0.2, 0.5))
    logger.info("us_overnight collected futures=%d m7=%d etf=%d", len(fut), len(m7), len(etf))
    return {"futures": fut, "m7": m7, "etf": etf}


async def fetch_us_overnight() -> dict:
    """미국 야간 시세 → {futures:[{symbol,name,price,change_pct}], m7:[...]}. 실패 시 빈 리스트."""
    return await asyncio.to_thread(_fetch_sync)
