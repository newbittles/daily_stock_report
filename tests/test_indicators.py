"""순수 지표 함수 단위 테스트 — 결정론적 검증."""
from __future__ import annotations

from src.indicators.core import (
    bollinger_bands,
    cci,
    ema,
    ichimoku,
    macd,
    moving_average,
    rsi,
)


def test_moving_average_basic():
    values = [1, 2, 3, 4, 5]
    ma = moving_average(values, 3)
    assert ma[0] is None
    assert ma[1] is None
    assert ma[2] == 2.0  # (1+2+3)/3
    assert ma[3] == 3.0
    assert ma[4] == 4.0


def test_moving_average_length_matches():
    values = [float(i) for i in range(50)]
    assert len(moving_average(values, 20)) == 50


def test_ema_seed_is_sma():
    values = [float(i) for i in range(30)]
    e = ema(values, 10)
    # 첫 유효값(index 9)은 SMA(0..9) = 4.5
    assert abs(e[9] - 4.5) < 1e-9


def test_rsi_all_gains_is_100():
    values = [float(i) for i in range(30)]  # 계속 상승
    r = rsi(values, 14)
    assert r[-1] == 100.0


def test_rsi_range():
    values = [10, 11, 10.5, 12, 11, 13, 12.5, 14, 13, 15, 14, 16, 15, 17, 16, 18]
    r = rsi(values, 14)
    last = r[-1]
    assert last is not None
    assert 0 <= last <= 100


def test_macd_structure():
    values = [float(i % 10) for i in range(60)]
    macd_line, signal, hist = macd(values)
    assert len(macd_line) == 60
    assert len(signal) == 60
    assert len(hist) == 60


def test_bollinger_bands_order():
    values = [10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16, 18, 17, 19, 18, 20, 19, 21]
    upper, mid, lower = bollinger_bands(values, 20, 2.0)
    assert upper[-1] is not None
    assert upper[-1] > mid[-1] > lower[-1]


def test_cci_computable():
    n = 30
    highs = [10 + i * 0.5 for i in range(n)]
    lows = [9 + i * 0.5 for i in range(n)]
    closes = [9.5 + i * 0.5 for i in range(n)]
    c = cci(highs, lows, closes, 20)
    assert c[-1] is not None


def test_ichimoku_keys():
    n = 60
    highs = [10 + i * 0.3 for i in range(n)]
    lows = [9 + i * 0.3 for i in range(n)]
    closes = [9.5 + i * 0.3 for i in range(n)]
    result = ichimoku(highs, lows, closes)
    assert set(result.keys()) == {"tenkan", "kijun", "senkou_a", "senkou_b"}
    assert result["tenkan"][-1] is not None
    assert result["kijun"][-1] is not None
