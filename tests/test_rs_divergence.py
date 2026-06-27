"""RS 약세 다이버전스 / RS 강세 유지 판정 결정론 검증 (사용자 2026-06-22).

가격 신고가권인데 RS(종목/지수)가 고점서 꺾이면 rs_warn(matched). RS가 자기 고점 유지면 confirming.
가중치0 참고 신호 — 본 테스트는 판정 로직만 검증(엣지 주장 아님).
"""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import rs_divergence


def _candles(closes: list[float]) -> list[Candle]:
    return [Candle(date=str(i), open=c, high=c, low=c, close=c, volume=0)
            for i, c in enumerate(closes)]


def test_rs_warn_when_price_high_but_rs_rolled_over() -> None:
    # 전반: 종목 급등(지수 완만) → RS 고점. 후반: 종목은 신고가 행진이나 지수가 급등해 RS 후퇴.
    stock, index = [], []
    for i in range(150):
        if i <= 100:
            stock.append(100 + i * 1.0)       # 100 → 200 (강세)
            index.append(100 + i * 0.10)      # 100 → 110 (완만)
        else:
            stock.append(200 + (i - 100) * 0.1)   # 200 → 204.9 (신고가 지속)
            index.append(110 + (i - 100) * 1.0)   # 110 → 159 (급등 → RS 후퇴)
    r = rs_divergence(_candles(stock), index)
    assert r.matched is True
    assert r.metrics["near_high"] == 1
    assert r.metrics["confirming"] == 0
    assert r.metrics["rs_from_hi_pct"] < 0


def test_rs_confirming_when_outperforming_to_the_end() -> None:
    # 종목이 끝까지 지수를 압도 → RS 신고가 유지(confirming), 경고 아님
    stock = [100 + i * 1.0 for i in range(150)]   # 100 → 249
    index = [100 + i * 0.33 for i in range(150)]  # 완만
    r = rs_divergence(_candles(stock), index)
    assert r.matched is False
    assert r.metrics["confirming"] == 1


def test_no_warn_when_price_off_high() -> None:
    # 가격이 고점서 크게 빠짐(신고가권 아님) → 경고/강세 아님(중립)
    stock = [100 + i for i in range(100)] + [200 - (i + 1) * 2 for i in range(50)]  # 고점후 급락
    index = [100 + i * 0.3 for i in range(150)]
    r = rs_divergence(_candles(stock), index)
    assert r.matched is False
    assert r.metrics["near_high"] == 0


def test_insufficient_data() -> None:
    r = rs_divergence(_candles([100.0] * 30), [100.0] * 30)
    assert r.matched is False
    assert r.metrics["confirming"] == 0
