"""보유종목 cross_signal 배지 — diagnose_holdings 신호 부착 + cross_badge 포맷."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.alerts.holdings_report import cross_badge, diagnose_holdings
from src.datasource.base import Candle


def _candles(closes: list[float]) -> list[Candle]:
    """종가 리스트 → Candle 리스트 (high/low/open/volume은 합성)."""
    out: list[Candle] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(
            date=f"2026{(i // 30) + 1:02d}{(i % 30) + 1:02d}",
            open=prev, high=max(c, prev) * 1.01, low=min(c, prev) * 0.99,
            close=c, volume=1_000_000,
        ))
        prev = c
    return out


# 추세 위 단기눌림: 강한 상승 후 소폭 조정 → MA5<MA10 + 20일이격 ≥15%
_PULLBACK = [100.0] * 48 + [150, 200, 240, 270, 290, 285, 278, 270, 262, 255, 248, 242]
# 조정시작: 완만한 흐름 + 데드크로스, 20일이격 ≤7%
_CORRECTION = [100.0] * 50 + [110, 112, 114, 116, 118, 116, 114, 112, 110, 108]
# 신호 없음: 순수 상승 추세(MA5>MA10, 정배열)
_NONE = [100.0 + i for i in range(60)]


def test_cross_badge_pure() -> None:
    assert cross_badge("PULLBACK") == " 🟢단기눌림"
    assert cross_badge("CORRECTION") == " ⚠️조정시작"
    assert cross_badge(None) == ""
    assert cross_badge("UNKNOWN") == ""


def _adapter_for(closes: list[float]) -> MagicMock:
    a = MagicMock()
    a.get_ohlcv = AsyncMock(return_value=_candles(closes))
    return a


async def test_diagnose_holdings_pullback_badge() -> None:
    adapter = _adapter_for(_PULLBACK)
    rows = await diagnose_holdings(
        adapter, holdings=[{"ticker": "005930", "name": "삼성전자", "avg_price": 100, "quantity": 10}]
    )
    assert rows and rows[0]["cross_signal"] == "PULLBACK"


async def test_diagnose_holdings_correction_badge() -> None:
    adapter = _adapter_for(_CORRECTION)
    rows = await diagnose_holdings(
        adapter, holdings=[{"ticker": "000660", "name": "SK하이닉스", "avg_price": 100, "quantity": 5}]
    )
    assert rows and rows[0]["cross_signal"] == "CORRECTION"


async def test_diagnose_holdings_no_cross_is_none() -> None:
    adapter = _adapter_for(_NONE)
    rows = await diagnose_holdings(
        adapter, holdings=[{"ticker": "035420", "name": "NAVER", "avg_price": 100, "quantity": 1}]
    )
    assert rows and rows[0]["cross_signal"] is None
