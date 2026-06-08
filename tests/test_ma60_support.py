"""F. 60일선 지지 마감(is_ma60_support) — 순수 패턴 결정론 검증(사용자 2026-06-09).

피에스케이 6/2 사례: 장중 60일선 하회 후 60일선 위로 끌어올려 마감(아랫꼬리 지지).
E(투매바닥)가 RSI≤30을 요구해 못 잡던 '얕은 눌림 지지'를 F가 잡는지.
"""
from __future__ import annotations

from src.datasource.base import Candle
from src.indicators.core import moving_average
from src.patterns.core import is_ma60_support


def _rising(n: int = 66, start: float = 100.0, step: float = 0.5) -> list[Candle]:
    """완만한 상승 추세 캔들 → MA60 우상향."""
    out: list[Candle] = []
    for i in range(n):
        c = start + i * step
        out.append(Candle(date=f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
                           open=c - 0.2, high=c + 0.3, low=c - 0.3, close=c, volume=1000))
    return out


def _ma60_prior(base: list[Candle]) -> float:
    return moving_average([c.close for c in base], 60)[-1]


def test_ma60_support_matches_pullback_hold() -> None:
    base = _rising(66)
    m = _ma60_prior(base)
    # 지지일: 저가가 60선 아래(-4%)로 찍고 종가는 60선 위(+1.5%, 캔들 상단부)
    support = Candle(date="20260602", open=m * 1.00, high=m * 1.03,
                     low=m * 0.96, close=m * 1.015, volume=3000)
    r = is_ma60_support(base + [support])
    assert r.matched, r.reason
    assert r.metrics["ma60_gap_low"] < 0 and r.metrics["ma60_gap_close"] >= 0


def test_ma60_support_rejects_close_below_line() -> None:
    base = _rising(66)
    m = _ma60_prior(base)
    support = Candle(date="20260602", open=m, high=m * 1.01, low=m * 0.95, close=m * 0.985, volume=3000)
    r = is_ma60_support(base + [support])
    assert not r.matched and "이탈" in r.reason


def test_ma60_support_rejects_no_touch() -> None:
    base = _rising(66)
    m = _ma60_prior(base)
    support = Candle(date="20260602", open=m * 1.05, high=m * 1.07, low=m * 1.04, close=m * 1.06, volume=3000)
    r = is_ma60_support(base + [support])
    assert not r.matched and "미접촉" in r.reason


def test_ma60_support_rejects_weak_lower_wick() -> None:
    base = _rising(66)
    m = _ma60_prior(base)
    # 저가는 60선 아래지만 종가가 캔들 하단부(긴 윗꼬리) → 지지 약함
    support = Candle(date="20260602", open=m * 1.06, high=m * 1.10, low=m * 0.96, close=m * 1.01, volume=3000)
    r = is_ma60_support(base + [support])
    assert not r.matched and "아랫꼬리" in r.reason


def test_ma60_support_rejects_falling_ma60() -> None:
    # 하락 추세(60선 우하향)에서의 60선 터치는 falling-knife → 제외
    base = _rising(66, start=140.0, step=-0.5)  # 우하향
    m = _ma60_prior(base)
    support = Candle(date="20260602", open=m, high=m * 1.03, low=m * 0.96, close=m * 1.015, volume=3000)
    r = is_ma60_support(base + [support])
    assert not r.matched and "우상향" in r.reason


def test_ma60_support_insufficient_data() -> None:
    r = is_ma60_support(_rising(40))
    assert not r.matched and "데이터 부족" in r.reason
