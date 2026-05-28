"""L2 Integration tests for KiwoomAdapter (OCX/COM — pykiwoom mocked)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.datasource.base import RankingKind
from src.datasource.kiwoom.adapter import KiwoomAdapter, KiwoomError, _clean, _to_float, _to_int


# ── Helper factories ───────────────────────────────────────────────────────────

def _make_quote_df(
    name="삼성전자", price="+75000", change_pct="+1.23", volume="12345678", date="20260526"
) -> pd.DataFrame:
    return pd.DataFrame([{
        "종목명": name,
        "현재가": price,
        "등락율": change_pct,
        "거래량": volume,
        "기준일자": date,
    }])


def _make_ohlcv_df(rows: int = 5) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "일자": f"202605{20 + i:02d}",
            "시가": f"+{70000 + i * 100}",
            "고가": f"+{71000 + i * 100}",
            "저가": f"+{69000 + i * 100}",
            "현재가": f"+{70500 + i * 100}",
            "거래량": f"{1000000 + i * 10000}",
        }
        for i in range(rows)
    ])


def _make_ranking_df(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "종목코드": f"00{i:04d}",
            "종목명": f"테스트{i}",
            "현재가": f"+{10000 * (i + 1)}",
            "등락율": f"+{i + 1}.50",
            "거래량": f"{500000 * (i + 1)}",
        }
        for i in range(n)
    ])


@pytest.fixture
def adapter() -> KiwoomAdapter:
    ad = KiwoomAdapter(account_no="50123456789", env="paper")
    # Inject a mocked pykiwoom instance directly
    mock_kiwoom = MagicMock()
    ad._kiwoom = mock_kiwoom
    return ad


# ── Value parsing utilities ────────────────────────────────────────────────────

def test_clean_removes_comma_and_plus():
    assert _clean("+75,000") == "75000"
    assert _clean("-1,234") == "-1234"


def test_to_float_parses_signed():
    assert _to_float("+1.23") == pytest.approx(1.23)
    assert _to_float("-2.50") == pytest.approx(-2.50)
    assert _to_float("0") == pytest.approx(0.0)


def test_to_int_strips_comma():
    assert _to_int("12,345,678") == 12345678


# ── get_quote ─────────────────────────────────────────────────────────────────

async def test_get_quote_parses_correctly(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = _make_quote_df()

    quote = await adapter.get_quote("005930")

    assert quote.ticker == "005930"
    assert quote.name == "삼성전자"
    assert quote.price == pytest.approx(75000.0)
    assert quote.change_pct == pytest.approx(1.23)
    assert quote.volume == 12345678


async def test_get_quote_empty_raises(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = pd.DataFrame()

    with pytest.raises(KiwoomError):
        await adapter.get_quote("005930")


async def test_get_quote_none_raises(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = None

    with pytest.raises(KiwoomError):
        await adapter.get_quote("005930")


# ── get_ohlcv ─────────────────────────────────────────────────────────────────

async def test_get_ohlcv_returns_candles(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = _make_ohlcv_df(5)

    candles = await adapter.get_ohlcv("005930", days=5)

    assert len(candles) == 5
    assert candles[0].date == "20260520"
    assert candles[0].open > 0
    assert candles[0].volume > 0


async def test_get_ohlcv_respects_days_limit(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = _make_ohlcv_df(20)

    candles = await adapter.get_ohlcv("005930", days=10)
    assert len(candles) == 10


async def test_get_ohlcv_empty_returns_empty_list(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = pd.DataFrame()
    assert await adapter.get_ohlcv("005930") == []


# ── get_ranking ────────────────────────────────────────────────────────────────

async def test_get_ranking_change_pct(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = _make_ranking_df(3)

    stocks = await adapter.get_ranking(RankingKind.CHANGE_PCT, top=3)

    assert len(stocks) == 3
    assert stocks[0].rank == 1
    assert stocks[0].ticker == "000000"
    assert stocks[0].price > 0


async def test_get_ranking_volume(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.return_value = _make_ranking_df(5)
    stocks = await adapter.get_ranking(RankingKind.VOLUME, top=5)
    assert len(stocks) == 5


# ── Retry on error ─────────────────────────────────────────────────────────────

async def test_retry_on_exception(adapter: KiwoomAdapter):
    """block_request가 2번 실패 후 3번째 성공하는 경우."""
    adapter._kiwoom.block_request.side_effect = [
        RuntimeError("timeout"),
        RuntimeError("timeout"),
        _make_quote_df(),
    ]

    quote = await adapter.get_quote("005930")
    assert quote.ticker == "005930"
    assert adapter._kiwoom.block_request.call_count == 3


async def test_exhausted_retries_raises(adapter: KiwoomAdapter):
    adapter._kiwoom.block_request.side_effect = RuntimeError("always fails")

    with pytest.raises(KiwoomError):
        await adapter.get_quote("005930")

    assert adapter._kiwoom.block_request.call_count == 3
