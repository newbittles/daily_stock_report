"""장중 분봉 흐름 수집 (#473/#474) — flow_summary 순수 + fetch 배치(스텁)."""
from __future__ import annotations

import asyncio

from src.datasource.intraday_flow import fetch_intraday_flows, flow_summary


def _m(hhmm: str, o: float, h: float, low: float, c: float) -> dict:
    return {"hhmm": hhmm, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}


def test_flow_summary_v_rebound() -> None:
    raw = [_m("0900", 98, 98, 90, 91), _m("0930", 91, 93, 90, 92), _m("1100", 95, 97, 94, 97)]
    s = flow_summary(raw, 100.0)
    assert s is not None
    assert s["shape"] == "V_REBOUND"
    assert s["cur_pct"] == -3.0 and s["low_pct"] == -10.0
    assert "반등" in s["desc"]


def test_flow_summary_none_without_data_or_prevclose() -> None:
    assert flow_summary([], 100.0) is None
    assert flow_summary([_m("0900", 1, 1, 1, 1)], 0) is None


class _FakeAdapter:
    def __init__(self, table: dict[str, list[dict]]) -> None:
        self.table = table
        self.calls: list[str] = []

    async def get_today_minutes(self, ticker: str, day=None) -> list[dict]:
        self.calls.append(ticker)
        return self.table.get(ticker, [])


def test_fetch_intraday_flows_batch_and_skips() -> None:
    table = {
        "000660": [_m("0900", 98, 98, 90, 91), _m("1100", 95, 97, 94, 97)],
        "005930": [],  # 분봉 없음 → 제외
    }
    adapter = _FakeAdapter(table)
    items = [("000660", 100.0), ("005930", 100.0), ("123456", 0.0)]  # 마지막 prev_close=0 스킵
    out = asyncio.run(fetch_intraday_flows(adapter, items))
    assert set(out.keys()) == {"000660"}
    assert out["000660"]["shape"] == "V_REBOUND"
    assert "123456" not in adapter.calls  # prev_close=0은 호출도 안 함


def test_fetch_dedups_tickers() -> None:
    adapter = _FakeAdapter({"000660": [_m("0900", 99, 99, 96, 97), _m("1100", 95, 95, 93, 94)]})
    out = asyncio.run(fetch_intraday_flows(adapter, [("000660", 100.0), ("000660", 100.0)]))
    assert adapter.calls == ["000660"]  # 중복 1회만
    assert out["000660"]["shape"] == "WEAK"
