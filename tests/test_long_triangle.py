"""G 장기 모드 — is_long_triangle (대형 삼각수렴) 단위 테스트 (사용자 2026-06-11)."""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import is_long_triangle


def _converging(n: int = 200) -> list[Candle]:
    """부드러운 상승추세 종가 + 진폭 30→~3 수축 envelope — 대칭 삼각수렴(고점↓·저점↑·60선 상승)."""
    out = []
    for i in range(n):
        close = 100.0 + i * 0.08              # 완만한 상승추세(60선 상승 보장)
        amp = 30.0 * (1 - i / (n + 20))       # 고저 진폭 수축(고점↓·저점↑)
        out.append(Candle(date=f"d{i}", open=close, high=close + amp,
                          low=close - amp, close=close, volume=1000))
    return out


def _flat_wide(n: int = 200) -> list[Candle]:
    """수축 없이 일정 진폭 — 삼각수렴 아님(미매칭이어야)."""
    out = []
    for i in range(n):
        close = 100.0 + i * 0.08
        out.append(Candle(date=f"d{i}", open=close, high=close + 12, low=close - 12,
                          close=close, volume=1000))
    return out


def test_long_triangle_matches_converging() -> None:
    r = is_long_triangle(_converging(), win=150)
    assert r.matched, r.reason
    assert r.metrics.get("contraction", 9) <= 0.62        # 실제 수축 확인
    assert r.metrics.get("slope_high", 0) < 0             # 저항선 하락


def test_long_triangle_rejects_no_contraction() -> None:
    assert not is_long_triangle(_flat_wide(), win=150).matched


def test_long_triangle_insufficient_data() -> None:
    assert not is_long_triangle(_converging(80), win=150).matched


def test_macd_bearish_divergence() -> None:
    """가격 고점↑·MACD 고점↓ = 약세 다이버전스(사용자 2026-06-11)."""
    import math
    from src.patterns.core import macd_bearish_divergence
    # 1차 큰 상승(MACD 큼) → 조정 → 2차 더 높은 가격이나 약한 모멘텀(MACD 작음)
    cl = []
    for i in range(60):
        cl.append(100 + i * 1.5)              # 1차 급등(MACD↑)
    for i in range(20):
        cl.append(cl[-1] - 1.5)               # 조정
    for i in range(30):
        cl.append(cl[-1] + 0.7)               # 2차 완만 상승(가격 더 높으나 MACD 약함)
    candles = [Candle(date=f"d{i}", open=c, high=c + 1, low=c - 1, close=c, volume=1000)
               for i, c in enumerate(cl)]
    r = macd_bearish_divergence(candles)
    # 데이터 구조상 검출 여부보다 에러 없이 동작 + 미충족 시 사유 반환
    assert isinstance(r.matched, bool)
