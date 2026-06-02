"""매크로 지표 — 환율(USD/KRW)·국제유가(WTI). 지수 2x2 매트릭스용.

yfinance 'KRW=X'(달러/원), 'CL=F'(WTI 선물). 전일 대비 등락률 포함.
동기 라이브러리라 asyncio.to_thread로 감싼다.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_SYMBOLS = {"fx": ("KRW=X", "원/달러"), "wti": ("CL=F", "WTI 유가")}


def _fetch_macro_sync() -> dict:
    import yfinance as yf
    out: dict = {}
    for key, (sym, name) in _SYMBOLS.items():
        try:
            df = yf.download(sym, period="7d", interval="1d", progress=False, auto_adjust=True)
            if df is None or len(df) < 2:
                continue
            close = df["Close"].squeeze().dropna()
            if len(close) < 2:
                continue
            last, prev = float(close.iloc[-1]), float(close.iloc[-2])
            out[key] = {
                "name": name,
                "value": round(last, 2),
                "change_pct": round((last - prev) / prev * 100, 2) if prev else 0.0,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("macro_fetch_failed sym=%s error=%s", sym, exc)
    return out


async def fetch_macro() -> dict:
    """{'fx': {name,value,change_pct}, 'wti': {...}} — 실패 키는 생략."""
    return await asyncio.to_thread(_fetch_macro_sync)
