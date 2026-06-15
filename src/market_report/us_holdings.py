"""미국(해외) 보유종목 상태 — 라이브 시세 연동(yfinance 일봉) + config 평단.

#971/item③: 미국 리포트 보유칸은 '미국 종목'을 라이브로(현재가·평가손익) 표시한다.
상태 판정은 도메인 diagnose_holding(순수)을 KR과 동일하게 재사용(이평선 기반 홀딩/손절/추가매수).
표시 통화는 USD. 오너 전용 게이트는 템플릿(audience=owner)에서 처리.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from src.patterns.core import diagnose_holding, ma_cross_signal

logger = logging.getLogger(__name__)

_HOLDINGS_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "holdings.yaml"


def load_us_holdings() -> list[dict]:
    """config/holdings.yaml의 us_holdings 로드. 없으면 빈 리스트. 항목: {ticker,name,quantity,avg_price}."""
    try:
        raw = yaml.safe_load(_HOLDINGS_CONFIG.read_text(encoding="utf-8")) or {}
        return [
            h for h in raw.get("us_holdings", [])
            if isinstance(h, dict) and h.get("ticker")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_holdings_config_load_failed error=%s", exc)
        return []


def build_us_holding_status(holding: dict, candles: list) -> dict | None:
    """평단 + 일봉 → 미국 보유종목 상태 dict(USD). 데이터 부족(<25봉) 시 None.

    KR holdings_status와 동일 키 형태(price/profit_rate/state/reason/eval_pl/...)로 맞춰 템플릿 재사용.
    """
    closes = [c.close for c in candles]
    if len(closes) < 25:
        return None
    r = diagnose_holding(candles)
    state = str(r.metrics.get("state", "UNKNOWN"))
    cross = ma_cross_signal(closes)
    price = closes[-1]
    avg = holding.get("avg_price")
    profit = (price - avg) / avg * 100 if avg else 0.0
    qty = holding.get("quantity")
    eval_pl = (price - avg) * qty if (avg and qty) else None
    prev_close = closes[-2] if len(closes) >= 2 else None
    today_pct = ((price / prev_close - 1) * 100) if prev_close else None
    return {
        "ticker": holding["ticker"],
        "name": holding.get("name", holding["ticker"]),
        "state": state,
        "reason": r.reason,
        "price": price,
        "avg_price": avg,
        "quantity": qty,
        "eval_pl": eval_pl,
        "profit_rate": profit,
        "today_pct": round(today_pct, 2) if today_pct is not None else None,
        "endstage": bool(r.metrics.get("endstage")),
        "cross_signal": cross,
    }


async def collect_us_holdings_status() -> list[dict]:
    """config us_holdings + yfinance 일봉으로 미국 보유종목 상태 리스트 생성. best-effort."""
    holdings = load_us_holdings()
    if not holdings:
        return []
    from src.datasource.us.fdr_source import fetch_us_ohlcv_batch
    tickers = [h["ticker"] for h in holdings]
    try:
        ohlcv = await fetch_us_ohlcv_batch(tickers, days=180)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_holdings_ohlcv_failed error=%s", exc)
        return []
    out: list[dict] = []
    for h in holdings:
        candles = ohlcv.get(h["ticker"]) or []
        s = build_us_holding_status(h, candles)
        if s is not None:
            out.append(s)
    return out


async def attach_us_holdings(snap) -> None:
    """미국 리포트 snap에 us_holdings_status 부착. best-effort(실패해도 리포트 진행)."""
    try:
        snap.us_holdings_status = await collect_us_holdings_status()
        logger.info("us_holdings_attached count=%d", len(snap.us_holdings_status or []))
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_holdings_attach_failed error=%s", exc)
