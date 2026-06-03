"""L1/L2 tests — 미국 종목 스크리닝 (us_screening).

- _df_to_candles: yfinance DataFrame → Candle 변환 결정론 (NaN 제거)
- _is_us_etf: ETF 명칭 제외
- run_us_screening: OHLCV 배치를 모킹해 C전략 매칭·필터 결정론 검증 (네트워크 없음)

design: docs/02-design/features/us-screening.design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.datasource.base import Candle
from src.datasource.us import fdr_source
from src.datasource.us.fdr_source import _df_to_candles
from src.datasource.us.universe import USStock
from src.screener import us_pipeline
from src.screener.config import ScreenerConfig, Strategy
from src.screener.us_pipeline import USStockPick, _is_us_etf, run_us_screening


# ─── _df_to_candles ──────────────────────────────────────────────────────
def test_df_to_candles_basic():
    idx = pd.to_datetime(["2026-05-28", "2026-05-29", "2026-06-01"])
    df = pd.DataFrame(
        {"Open": [10, 11, 12], "High": [11, 12, 13], "Low": [9, 10, 11],
         "Close": [10.5, 11.5, 12.5], "Volume": [1000, 2000, 3000]},
        index=idx,
    )
    candles = _df_to_candles(df)
    assert len(candles) == 3
    assert candles[0].date == "20260528"
    assert candles[-1].close == 12.5
    assert candles[-1].volume == 3000


def test_df_to_candles_drops_nan_rows():
    idx = pd.to_datetime(["2026-05-28", "2026-05-29"])
    df = pd.DataFrame(
        {"Open": [10, None], "High": [11, None], "Low": [9, None],
         "Close": [10.5, None], "Volume": [1000, None]},
        index=idx,
    )
    candles = _df_to_candles(df)
    assert len(candles) == 1  # NaN 행 제거
    assert candles[0].date == "20260528"


# ─── _is_us_etf ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,expected", [
    ("SPDR S&P 500 ETF Trust", True),
    ("iShares Core", True),
    ("Invesco QQQ Trust", True),
    ("Apple Inc", False),
    ("NVIDIA Corp", False),
])
def test_is_us_etf(name, expected):
    assert _is_us_etf(name) is expected


# ─── run_us_screening (모킹) ──────────────────────────────────────────────
def _uptrend(n: int = 130, start: float = 100.0, step: float = 1.5,
             vol: int = 5_000_000) -> list[Candle]:
    """우상향 추세 합성 일봉 — 정배열+신고가(마지막 최고) → C전략 매칭 기대."""
    base = datetime(2025, 1, 1)
    out: list[Candle] = []
    for i in range(n):
        c = start + step * i
        d = (base + timedelta(days=i)).strftime("%Y%m%d")
        out.append(Candle(date=d, open=c * 0.99, high=c * 1.01,
                          low=c * 0.985, close=c, volume=vol))
    return out


def _flat(n: int = 130, price: float = 50.0, vol: int = 5_000_000) -> list[Candle]:
    """횡보 — 신고가·정배열 미충족 → 비매칭 기대."""
    base = datetime(2025, 1, 1)
    return [Candle(date=(base + timedelta(days=i)).strftime("%Y%m%d"),
                   open=price, high=price * 1.005, low=price * 0.995,
                   close=price, volume=vol) for i in range(n)]


def _c_strategy_cfg() -> ScreenerConfig:
    return ScreenerConfig(
        strategies=[Strategy(
            name="C. 추세추종", enabled=True, description="",
            conditions={
                "trend_follow": {
                    "nh_lookback": 60, "nh_tol": 0.03, "div_lookback": 40,
                    "div_rsi_margin": 5.0, "rollover_peak_min": 50.0,
                    "rollover_ratio": 0.55,
                },
                "min_trade_value": 50_000_000,
            },
            opinion="추세추종(C)",
        )],
        global_filters={"min_price": 5, "exclude_etf": True},
    )


@pytest.fixture
def patch_ohlcv(monkeypatch):
    """fetch_us_ohlcv_batch 를 주어진 data_map 으로 대체."""
    def _apply(data_map: dict[str, list[Candle]]):
        async def fake_batch(symbols, days=120):
            return {s: data_map[s] for s in symbols if s in data_map}
        monkeypatch.setattr(us_pipeline, "fetch_us_ohlcv_batch", fake_batch)
    return _apply


async def test_uptrend_matches_c(patch_ohlcv):
    patch_ohlcv({"UP": _uptrend()})
    uni = [USStock("UP", "Uptrend Co", "Information Technology", "Software")]
    picks = await run_us_screening(cfg=_c_strategy_cfg(), universe=uni)
    assert len(picks) == 1
    p = picks[0]
    assert p.symbol == "UP"
    assert p.sector == "Information Technology"
    assert p.opinions  # 매수 의견 존재
    assert p.all_reasons  # 근거 수치 동반 (CLAUDE.md §2)


async def test_flat_does_not_match(patch_ohlcv):
    patch_ohlcv({"FLAT": _flat()})
    uni = [USStock("FLAT", "Flat Co")]
    picks = await run_us_screening(cfg=_c_strategy_cfg(), universe=uni)
    assert picks == []


async def test_min_price_filter(patch_ohlcv):
    # 우상향이지만 가격대가 $5 미만 → 제외
    patch_ohlcv({"PENNY": _uptrend(start=1.0, step=0.01)})
    uni = [USStock("PENNY", "Penny Co")]
    picks = await run_us_screening(cfg=_c_strategy_cfg(), universe=uni)
    assert picks == []


async def test_etf_excluded_from_universe(patch_ohlcv):
    patch_ohlcv({"UP": _uptrend(), "ETFX": _uptrend()})
    uni = [USStock("UP", "Uptrend Co"), USStock("ETFX", "Some SPDR ETF Trust")]
    picks = await run_us_screening(cfg=_c_strategy_cfg(), universe=uni)
    symbols = {p.symbol for p in picks}
    assert "UP" in symbols
    assert "ETFX" not in symbols  # ETF 명칭 제외


async def test_insufficient_candles_skipped(patch_ohlcv):
    patch_ohlcv({"SHORT": _uptrend(n=30)})  # 60봉 미만
    uni = [USStock("SHORT", "Short History")]
    picks = await run_us_screening(cfg=_c_strategy_cfg(), universe=uni)
    assert picks == []


# ─── screener_us.yaml 실제 설정 ───────────────────────────────────────────
def test_screener_us_config_loads_a_and_c():
    """config/screener_us.yaml 이 A·C 전략을 달러 거래대금 기준으로 로드한다."""
    from src.screener.config import load_screener_config
    from src.screener.us_pipeline import SCREENER_US_PATH

    cfg = load_screener_config(SCREENER_US_PATH)
    enabled = cfg.enabled_strategies()
    initials = {s.name[:1] for s in enabled}
    assert {"A", "B", "C", "D"} <= initials
    # 달러 기준: 거래대금 $50M, 최소가 $5
    for s in enabled:
        assert s.conditions.get("min_trade_value") == 50_000_000
    assert cfg.global_filters.get("min_price") == 5


# ─── 유니버스 확장 (나스닥 핫 + combined) ──────────────────────────────────
from src.datasource.us import universe as U  # noqa: E402


async def test_nasdaq_hot_filters_and_ranks(monkeypatch, tmp_path):
    """거래대금 내림차순 + min_price·min_amount 필터 → 상위 top."""
    monkeypatch.setattr(U, "_NASDAQ_CACHE", tmp_path / "nq.json")
    listing = [
        USStock("AAA", "Alpha Co", "반도체", "반도체"),
        USStock("BBB", "Beta Co", "소프트웨어", "소프트웨어"),
        USStock("PNY", "Penny Co", "기타", "기타"),     # 저가 → 제외
        USStock("THIN", "Thin Co", "기타", "기타"),      # 저거래대금 → 제외
    ]
    monkeypatch.setattr(U, "_nasdaq_listing", lambda: listing)

    async def fake_turnover(syms, lookback=7):
        return {
            "AAA": {"price": 100.0, "turnover": 800_000_000, "change_pct": 5.0, "date": "20260602"},
            "BBB": {"price": 50.0, "turnover": 300_000_000, "change_pct": 2.0, "date": "20260602"},
            "PNY": {"price": 2.0, "turnover": 900_000_000, "change_pct": 9.0, "date": "20260602"},
            "THIN": {"price": 80.0, "turnover": 10_000_000, "change_pct": 1.0, "date": "20260602"},
        }
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_daily_turnover", fake_turnover)

    hot = await U.get_nasdaq_hot_universe(top=10, min_amount=50_000_000, min_price=5.0)
    syms = [u.symbol for u in hot]
    assert syms == ["AAA", "BBB"]            # 거래대금 순, PNY(저가)·THIN(저거래) 제외
    assert hot[0].sector == "반도체"          # Industry 한글 보존


async def test_nasdaq_hot_respects_top(monkeypatch, tmp_path):
    monkeypatch.setattr(U, "_NASDAQ_CACHE", tmp_path / "nq.json")
    listing = [USStock(f"S{i}", f"Co{i}") for i in range(10)]
    monkeypatch.setattr(U, "_nasdaq_listing", lambda: listing)

    async def fake_turnover(syms, lookback=7):
        return {f"S{i}": {"price": 100.0, "turnover": (10 - i) * 1e8,
                          "change_pct": 1.0, "date": "20260602"} for i in range(10)}
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_daily_turnover", fake_turnover)

    hot = await U.get_nasdaq_hot_universe(top=3, min_amount=0, min_price=0)
    assert [u.symbol for u in hot] == ["S0", "S1", "S2"]  # 거래대금 상위 3


async def test_combined_dedup_sp500_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(U, "_NASDAQ_CACHE", tmp_path / "nq.json")
    monkeypatch.setattr(U, "get_sp500_universe",
                        lambda: [USStock("AAA", "Alpha SP", "Information Technology", "Software")])
    monkeypatch.setattr(U, "_nasdaq_listing", lambda: [
        USStock("AAA", "Alpha NQ", "반도체", "반도체"),   # 중복 → sp500 우선
        USStock("ZZZ", "Zeta NQ", "소프트웨어", "소프트웨어"),
    ])

    async def fake_turnover(syms, lookback=7):
        return {"AAA": {"price": 100.0, "turnover": 5e8, "change_pct": 1.0, "date": "x"},
                "ZZZ": {"price": 100.0, "turnover": 5e8, "change_pct": 1.0, "date": "x"}}
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_daily_turnover", fake_turnover)

    uni = await U.get_combined_universe(nasdaq_hot_top=10, min_amount=0, min_price=0)
    syms = [u.symbol for u in uni]
    assert "AAA" in syms and "ZZZ" in syms
    assert syms.count("AAA") == 1                       # 중복 제거
    aaa = next(u for u in uni if u.symbol == "AAA")
    assert aaa.sector == "Information Technology"        # sp500 GICS 우선


# ─── 리포트 빌더 (P4) ──────────────────────────────────────────────────────
from src.screener.engine import ScreenMatch  # noqa: E402
from src.screener.us_report import build_us_screening_report  # noqa: E402


def _pick(symbol, name, sector, strat, reason):
    cs = [Candle("20260602", 100, 100, 100, 100, 1_000_000)]
    return USStockPick(
        symbol=symbol, name=name, price=100.0, change_pct=1.5,
        sector=sector, industry=sector,
        matches=[ScreenMatch(matched=True, strategy_name=strat, opinion="op", reasons=[reason])],
        candles=cs,
    )


def test_build_report_groups_disclaimer_backtest_note():
    picks = [
        _pick("NVDA", "Nvidia", "반도체", "C. 추세추종", "대세 정배열 + 신고가"),
        _pick("AAPL", "Apple", "IT", "B. 눌림목", "20선 눌림"),
    ]
    text = build_us_screening_report(picks, top_n=5, as_of="2026-06-03")
    assert "미국 종목 스크리닝" in text
    assert "NVDA" in text and "AAPL" in text
    assert "C 추세추종" in text and "B 20일선" in text   # 전략별 그룹
    assert "참고용" in text                              # 면책 (CLAUDE.md §2)
    assert "백테스트" in text                            # 백테스트 보조 주의
    # C가 B보다 먼저 (백테스트 우위순)
    assert text.index("C 추세추종") < text.index("B 20일선")


def test_build_report_empty():
    text = build_us_screening_report([], as_of="2026-06-03")
    assert "없습니다" in text
    assert "참고용" in text


# ─── cross_signal (대세상승주 단기조정/고점 보조신호, domain SSOT) ──────────
from src.patterns.core import cross_signal  # noqa: E402


def _closes_to_candles(closes: list[float]) -> list[Candle]:
    base = datetime(2024, 1, 1)
    return [Candle((base + timedelta(days=i)).strftime("%Y%m%d"), c, c, c, c, 1000)
            for i, c in enumerate(closes)]


def test_cross_signal_pullback():
    """급등으로 20선 이격 큼(≥15%) + 최근 5<10 데드 → 단기눌림(매수 기회)."""
    closes = [100] * 5 + [200] * 5 + [400] * 8 + [395, 390]
    assert cross_signal(_closes_to_candles(closes)) == "pullback"


def test_cross_signal_correction():
    """횡보 후 하락 → 20선 근접(이격≤7%) + 5<10 데드 → 조정 시작(경고)."""
    closes = [200] * 25 + [198, 196, 194, 192, 190]
    assert cross_signal(_closes_to_candles(closes)) == "correction"


def test_cross_signal_none_on_uptrend():
    """정배열 상승(5>10) → 신호 없음."""
    closes = [100 + i for i in range(40)]
    assert cross_signal(_closes_to_candles(closes)) == ""


def test_report_shows_cross_badge():
    """리포트에 cross_signal 배지(🟢단기눌림/⚠️조정시작) 표기."""
    p_pull = _pick("NVDA", "Nvidia", "반도체", "C. 추세추종", "정배열")
    p_pull.cross_signal = "pullback"
    p_corr = _pick("AMD", "AMD", "반도체", "C. 추세추종", "정배열")
    p_corr.cross_signal = "correction"
    text = build_us_screening_report([p_pull, p_corr], top_n=5, as_of="2026-06-03")
    assert "🟢단기눌림" in text
    assert "⚠️조정시작" in text
