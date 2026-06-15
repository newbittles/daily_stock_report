"""미국 보유종목 상태 빌더 — 라이브 일봉 + 평단으로 상태/수익률 산출(순수)."""
from __future__ import annotations

from collections import namedtuple

from src.market_report.us_holdings import build_us_holding_status, load_us_holdings

_C = namedtuple("Candle", "date open high low close volume")


def _rising(n=130, start=50.0, step=1.0):
    """정배열 상승 일봉 n개 — diagnose_holding이 HOLD류로 보는 형태."""
    out = []
    for i in range(n):
        c = start + i * step
        out.append(_C(f"d{i}", c, c + 0.5, c - 0.5, c, 1000))
    return out


def test_build_status_computes_profit_and_fields():
    candles = _rising()  # 마지막 종가 = 50 + 129 = 179
    holding = {"ticker": "HOOD", "name": "로빈후드", "quantity": 34, "avg_price": 90.97}
    s = build_us_holding_status(holding, candles)
    assert s["ticker"] == "HOOD"
    assert s["name"] == "로빈후드"
    assert s["price"] == 179.0
    # 수익률 = (179-90.97)/90.97*100 ≈ +96.8%
    assert round(s["profit_rate"], 1) == 96.8
    # 평가손익 = (179-90.97)*34
    assert round(s["eval_pl"], 2) == round((179.0 - 90.97) * 34, 2)
    assert "state" in s and "reason" in s


def test_build_status_none_when_insufficient_candles():
    holding = {"ticker": "X", "name": "엑스", "quantity": 1, "avg_price": 10.0}
    assert build_us_holding_status(holding, _rising(n=10)) is None


def test_load_us_holdings_from_config():
    rows = load_us_holdings()
    tickers = {r["ticker"] for r in rows}
    assert {"HOOD", "MRVL", "NFLX", "PLTR", "SOXL", "SPCX"} <= tickers
    hood = next(r for r in rows if r["ticker"] == "HOOD")
    assert hood["quantity"] == 34 and hood["avg_price"] == 90.97
