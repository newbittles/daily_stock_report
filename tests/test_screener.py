"""스크리너 설정 로딩 + 매칭 엔진 테스트."""
from __future__ import annotations

from pathlib import Path

from src.datasource.base import Candle
from src.screener.config import load_screener_config
from src.screener.engine import evaluate_strategy, screen_stock


def _make_candles(closes, volumes=None):
    vols = volumes or [2000] * len(closes)
    return [
        Candle(date=f"d{i}", open=c, high=c + 1, low=c - 1, close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def test_load_default_config():
    cfg = load_screener_config()
    # config/screener.yaml 존재 → 전략 로딩됨
    assert cfg.universe_watchlist in (True, False)
    assert len(cfg.strategies) >= 1


def test_load_missing_config_returns_default(tmp_path):
    cfg = load_screener_config(tmp_path / "nonexistent.yaml")
    assert cfg.universe_watchlist is True
    assert cfg.strategies == []


def test_evaluate_ma_alignment_pass():
    candles = _make_candles([100 + i for i in range(60)])
    result = evaluate_strategy(
        "테스트", "매수", {"ma_alignment": {"periods": [5, 20, 60]}}, candles
    )
    assert result.matched
    assert result.opinion == "매수"


def test_evaluate_rsi_between():
    # 상승 일변도 → RSI 높음 → 25~45 범위 밖
    candles = _make_candles([100 + i for i in range(30)])
    result = evaluate_strategy("테스트", "매수", {"rsi_between": [25, 45]}, candles)
    assert not result.matched


def test_evaluate_change_pct_between():
    candles = _make_candles([100] * 60)
    # change_pct -2 → [-7, 3] 범위 안
    result = evaluate_strategy(
        "테스트", "매수", {"change_pct_between": [-7, 3]}, candles, change_pct=-2.0
    )
    assert result.matched


def test_evaluate_change_pct_out_of_range():
    candles = _make_candles([100] * 60)
    result = evaluate_strategy(
        "테스트", "매수", {"change_pct_between": [-7, 3]}, candles, change_pct=10.0
    )
    assert not result.matched


def test_screen_stock_multiple_strategies():
    from src.screener.config import Strategy

    candles = _make_candles([100 + i for i in range(60)])
    strategies = [
        Strategy("정배열", True, "", {"ma_alignment": {"periods": [5, 20, 60]}}, "매수"),
        Strategy("불가능", True, "", {"rsi_between": [25, 30]}, "매수"),  # 매칭 안 됨
    ]
    matches = screen_stock(strategies, candles)
    assert len(matches) == 1
    assert matches[0].strategy_name == "정배열"
