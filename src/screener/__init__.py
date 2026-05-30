"""조건 검색 엔진 — YAML 전략 정의를 Candle 데이터에 매칭.

- config.py: YAML 로딩 + 검증 (외부 파일 읽기는 여기서만)
- engine.py: 순수 매칭 로직 (Candle + 전략 dict → 판정)
"""
from __future__ import annotations

from src.screener.config import ScreenerConfig, Strategy, load_screener_config
from src.screener.engine import ScreenMatch, evaluate_strategy, screen_stock

__all__ = [
    "ScreenerConfig",
    "Strategy",
    "load_screener_config",
    "ScreenMatch",
    "evaluate_strategy",
    "screen_stock",
]
