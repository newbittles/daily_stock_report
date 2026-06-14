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

# M7 아래 별도 표시 ETF — 한국 ETF(EWY, 한국시장 야간 프록시) + 반도체 3배(SOXL)
# EWY: iShares MSCI South Korea ETF(미국 상장). 미국 마감+애프터 등락이 한국 개장 분위기 선행지표
# (사용자 2026-06-10 장전 리포트). SOXL(#485): 반도체 3X — 삼성·하이닉스 선행.
_ETF = {"EWY": "한국 ETF(EWY)", "SOXL": "SOXL(반도체 3X)"}

# 추가 개별종목 — M7 외 사용자 지정(2026-06-14): 마이크론(MU, 메모리반도체, 하이닉스 선행).
# M7 리스트엔 넣지 않고 별도 'extra'로 분리 → 정보 다이어트한 리포트에서만 선택 표시.
_EXTRA = {"MU": "마이크론"}


def _fetch_one(sym: str) -> dict | None:
    """단일 심볼 fast_info → {price, change_pct}. 전일종가≤0/실패 시 None. 선물용(연속)."""
    import yfinance as yf

    fi = yf.Ticker(sym).fast_info
    last = float(fi.last_price)
    prev = float(fi.previous_close)
    if prev <= 0 or last <= 0:
        return None
    return {"price": round(last, 2), "change_pct": round((last / prev - 1) * 100, 2)}


def _fetch_detail(sym: str) -> dict | None:
    """info 기반 → {price, change_pct(정규장 마감 등락), session_pct, session_label}(#503).

    change_pct = regularMarketChangePercent(정규장 당일 등락), session = marketState에 따라
    프리장(PRE)/애프터(POST) 등락. 한국 아침=애프터, 한국 저녁=프리장. 장중(REGULAR)·결측은
    session 없음(마감만 표시). info 실패/결측 시 None → 호출측이 fast_info 폴백."""
    import yfinance as yf

    info = yf.Ticker(sym).info
    reg = info.get("regularMarketChangePercent")
    price = info.get("regularMarketPrice")
    if reg is None or price is None:
        return None
    state = str(info.get("marketState", "")).upper()
    if state == "PRE":
        sess, label = info.get("preMarketChangePercent"), "프리장"
    elif state in ("POST", "POSTPOST", "CLOSED"):
        sess, label = info.get("postMarketChangePercent"), "애프터"
    else:  # REGULAR(미국 장중) 등 — 세션 등락 없음
        sess, label = None, ""
    return {
        "price": round(float(price), 2),
        "change_pct": round(float(reg), 2),
        "session_pct": round(float(sess), 2) if sess is not None else None,
        "session_label": label,
    }


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
            q = _fetch_detail(sym) or _fetch_one(sym)  # 마감+프리장 분리(#503), 실패 시 fast_info
            if q:
                m7.append({"symbol": sym, "name": name, **q})
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnight_m7_failed sym=%s error=%s", sym, exc)
        time.sleep(random.uniform(0.2, 0.5))
    m7.sort(key=lambda x: x["change_pct"], reverse=True)  # 마감 등락률 내림차순

    etf: list[dict] = []  # SOXL 등 — M7 아래 별도(#485)
    for sym, name in _ETF.items():
        try:
            q = _fetch_detail(sym) or _fetch_one(sym)
            if q:
                etf.append({"symbol": sym, "name": name, **q})
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnight_etf_failed sym=%s error=%s", sym, exc)
        time.sleep(random.uniform(0.2, 0.5))

    extra: list[dict] = []  # 마이크론 등 — M7 외 사용자 지정 개별종목(2026-06-14)
    for sym, name in _EXTRA.items():
        try:
            q = _fetch_detail(sym) or _fetch_one(sym)
            if q:
                extra.append({"symbol": sym, "name": name, **q})
        except Exception as exc:  # noqa: BLE001
            logger.warning("overnight_extra_failed sym=%s error=%s", sym, exc)
        time.sleep(random.uniform(0.2, 0.5))

    logger.info("us_overnight collected futures=%d m7=%d etf=%d extra=%d",
                len(fut), len(m7), len(etf), len(extra))
    return {"futures": fut, "m7": m7, "etf": etf, "extra": extra}


async def fetch_us_overnight() -> dict:
    """미국 야간 시세 → {futures:[{symbol,name,price,change_pct}], m7:[...]}. 실패 시 빈 리스트."""
    return await asyncio.to_thread(_fetch_sync)
