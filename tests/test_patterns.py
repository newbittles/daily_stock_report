"""순수 패턴 판정 단위 테스트 — 픽스처 기반 결정론."""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import (
    is_above_ichimoku_cloud,
    is_breakout,
    is_ma_alignment,
    is_pullback,
    is_volume_surge,
)


def _make_candles(closes: list[float], volumes: list[int] | None = None) -> list[Candle]:
    vols = volumes or [1000] * len(closes)
    return [
        Candle(date=f"d{i}", open=c, high=c + 1, low=c - 1, close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def test_ma_alignment_uptrend_true():
    # 꾸준한 상승 → 정배열
    candles = _make_candles([100 + i for i in range(60)])
    result = is_ma_alignment(candles, (5, 20, 60))
    assert result.matched
    assert "ma5" in result.metrics


def test_ma_alignment_downtrend_false():
    # 꾸준한 하락 → 역배열
    candles = _make_candles([200 - i for i in range(60)])
    result = is_ma_alignment(candles, (5, 20, 60))
    assert not result.matched


def test_ma_alignment_insufficient_data():
    candles = _make_candles([100, 101, 102])
    result = is_ma_alignment(candles, (5, 20, 60))
    assert not result.matched
    assert "부족" in result.reason


def test_breakout_with_volume():
    # 60봉 횡보 후 마지막 봉 고가 돌파 + 거래량 급증
    closes = [100] * 59 + [110]
    volumes = [1000] * 59 + [3000]
    candles = _make_candles(closes, volumes)
    result = is_breakout(candles, lookback=20, vol_mult=1.5)
    assert result.matched
    assert result.metrics["vol_ratio"] >= 1.5


def test_breakout_no_volume_fails():
    closes = [100] * 59 + [110]
    volumes = [1000] * 60  # 거래량 그대로
    candles = _make_candles(closes, volumes)
    result = is_breakout(candles, lookback=20, vol_mult=1.5)
    assert not result.matched


def test_volume_surge():
    closes = [100] * 10
    volumes = [1000] * 9 + [5000]
    candles = _make_candles(closes, volumes)
    result = is_volume_surge(candles, lookback=5, mult=2.0)
    assert result.matched
    assert result.metrics["vol_ratio"] >= 2.0


def test_pullback_insufficient_data():
    candles = _make_candles([100] * 30)
    result = is_pullback(candles)
    assert not result.matched


def test_above_ichimoku_strong_uptrend():
    # 강한 상승 추세 → 현재가가 구름 위
    candles = _make_candles([100 + i * 1.5 for i in range(60)])
    result = is_above_ichimoku_cloud(candles)
    assert result.matched
    assert "강세" in result.reason
