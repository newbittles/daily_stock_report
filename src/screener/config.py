"""스크리너 YAML 설정 로딩·검증.

config/screener.yaml → ScreenerConfig 객체.
파일 I/O는 여기서만 (engine.py는 순수 유지).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "screener.yaml"


@dataclass
class Strategy:
    name: str
    enabled: bool
    description: str
    conditions: dict[str, Any]
    opinion: str = "매수 관심"


@dataclass
class ScreenerConfig:
    universe_watchlist: bool = True
    universe_hot: bool = True
    hot_stocks_top: int = 30
    universe_market_cap: bool = False     # 시총 상위 풀 (HTS식 디텍팅)
    market_cap_kospi: int = 200           # 코스피 시총 상위 N
    market_cap_kosdaq: int = 100          # 코스닥 시총 상위 N
    market_cap_min_amount: float = 0      # 당일 거래대금 하한(원) — 활발한 종목만, 스캔 부하↓
    strategies: list[Strategy] = field(default_factory=list)
    global_filters: dict[str, Any] = field(default_factory=dict)

    def enabled_strategies(self) -> list[Strategy]:
        return [s for s in self.strategies if s.enabled]


def load_screener_config(path: Path | str | None = None) -> ScreenerConfig:
    """YAML 설정 로딩. 파일 없으면 기본값 반환 (안전)."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        logger.warning("screener_config_not_found path=%s — 기본값 사용", cfg_path)
        return ScreenerConfig()

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.error("screener_config_parse_failed error=%s", exc)
        return ScreenerConfig()

    universe = raw.get("universe", {})
    strategies_raw = raw.get("strategies", []) or []
    strategies = []
    for s in strategies_raw:
        if not isinstance(s, dict) or "name" not in s:
            continue
        strategies.append(Strategy(
            name=str(s["name"]),
            enabled=bool(s.get("enabled", True)),
            description=str(s.get("description", "")),
            conditions=s.get("conditions", {}) or {},
            opinion=str(s.get("opinion", "매수 관심")),
        ))

    cfg = ScreenerConfig(
        universe_watchlist=bool(universe.get("watchlist", True)),
        universe_hot=bool(universe.get("hot_stocks", True)),
        hot_stocks_top=int(universe.get("hot_stocks_top", 30)),
        universe_market_cap=bool(universe.get("market_cap", False)),
        market_cap_kospi=int(universe.get("market_cap_kospi", 200)),
        market_cap_kosdaq=int(universe.get("market_cap_kosdaq", 100)),
        market_cap_min_amount=float(universe.get("market_cap_min_amount", 0)),
        strategies=strategies,
        global_filters=raw.get("global_filters", {}) or {},
    )
    logger.info(
        "screener_config_loaded strategies=%d enabled=%d",
        len(cfg.strategies), len(cfg.enabled_strategies()),
    )
    return cfg
