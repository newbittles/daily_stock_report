"""NXT 시간외 상위 상승률 — 어댑터 계산(모킹) + 마감후 텔레그램 섹션(사용자 2026-06-05).

정규장 종가 대비 NXT 변동률 계산·양수만·내림차순. 텔레그램 마감후 섹션 노출.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from src.datasource.kis.adapter import KisAdapter
from src.market_report.models import MarketSnapshot
from src.market_report.telegram_notify import _format_post_summary


def test_get_nxt_overtime_gainers_computes_vs_regclose(monkeypatch) -> None:
    a = KisAdapter("k", "s", "12345678", "real")

    async def fake_request(path, tr, params):
        if "ranking/fluctuation" in path:
            return {"output": [
                {"stck_shrn_iscd": "111111", "hts_kor_isnm": "에이", "stck_prpr": "11000"},
                {"stck_shrn_iscd": "222222", "hts_kor_isnm": "비이", "stck_prpr": "9500"},
                {"stck_shrn_iscd": "333333", "hts_kor_isnm": "씨이", "stck_prpr": "10500"},
            ]}
        if "inquire-price" in path:  # 정규장 종가 = 전부 10000
            return {"output": {"stck_prpr": "10000"}}
        return {"output": []}

    monkeypatch.setattr(a, "_request", fake_request)
    out = asyncio.run(a.get_nxt_overtime_gainers(top=7))
    # 에이 +10%, 씨이 +5% (양수만), 비이 -5%는 제외. 내림차순.
    assert [g["ticker"] for g in out] == ["111111", "333333"]
    assert out[0]["overtime_pct"] == 10.0 and out[1]["overtime_pct"] == 5.0


def test_post_summary_shows_overtime_section() -> None:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 5, 16, 30))
    snap.overtime_gainers = [
        {"ticker": "111111", "name": "에이", "nxt_price": 11000, "reg_close": 10000, "overtime_pct": 10.0},
    ]
    msg = _format_post_summary(snap)
    assert "시간외(NXT) 상위 상승률" in msg
    assert "에이" in msg and "+10.0%" in msg
    # MC1(2026-06-14): 종목명/시세 줄바꿈 분리 — 시세는 들여쓰기된 별도 줄
    assert "    11,000원 (+10.0%)" in msg


def test_post_summary_no_overtime_when_empty() -> None:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 5, 16, 30))
    assert "시간외(NXT)" not in _format_post_summary(snap)
