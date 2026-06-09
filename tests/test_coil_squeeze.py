"""G. 삼각수렴(코일) 임박(is_coil_squeeze) — 순수 패턴 결정론 검증(사용자 2026-06-09).

테크윙 089030 발단. 상승추세 베이스 + 20일 코일 꼬리(형태별)로 검증.
확정 파라미터(2차 백테스트): bb_max=15·ma_conv_max=3·vol_dry=0.8·win=20, NR7 미사용.
"""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import is_coil_squeeze

CENTER = 200.0


def _base(n: int = 110, start: float = 100.0, top: float = CENTER) -> list[Candle]:
    """완만한 상승 추세 베이스 → 종가>120선, MA60 우상향."""
    out: list[Candle] = []
    for i in range(n):
        c = start + (top - start) * i / (n - 1)
        out.append(Candle(date=str(i), open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000))
    return out


def _tail(shape: str, vol_last: int = 600) -> list[Candle]:
    """20일 코일 꼬리. shape: sym(대칭)/flat(바닥지지)/box/wide."""
    out: list[Candle] = []
    for k in range(20):
        a = 7.0 * (1 - k / 19) + 0.4  # 진폭 7.4 → 0.4 (수렴)
        vol = 1000 if k < 10 else vol_last  # 후반 거래량 건조
        if shape == "sym":
            hi, lo, cl = CENTER + a, CENTER - a, CENTER          # 고점↓ 저점↑
        elif shape == "flat":
            hi, lo, cl = CENTER + a, CENTER, CENTER              # 저점 평평 + 고점↓
        elif shape == "box":
            hi, lo, cl = CENTER + 6, CENTER - 6, CENTER          # 고저 평평(수렴 아님)
        elif shape == "wide":
            cl = 180.0 if k % 2 else 220.0                        # 큰 변동(BB폭 큼)
            hi, lo = cl * 1.02, cl * 0.98
        else:
            raise ValueError(shape)
        out.append(Candle(date=f"t{k}", open=cl, high=hi, low=lo, close=cl, volume=vol))
    return out


def test_coil_matches_symmetric() -> None:
    r = is_coil_squeeze(_base() + _tail("sym"))
    assert r.matched, r.reason
    assert r.metrics["shape"] == 1  # 대칭수렴
    assert r.metrics["slope_high"] < 0 < r.metrics["slope_low"]
    assert r.metrics["bb_width"] <= 15 and r.metrics["ma_conv"] <= 3


def test_coil_matches_flat_bottom() -> None:
    r = is_coil_squeeze(_base() + _tail("flat"))
    assert r.matched, r.reason
    assert r.metrics["shape"] == 2  # 바닥지지수렴
    assert r.metrics["slope_high"] < 0


def test_coil_rejects_box_no_convergence() -> None:
    r = is_coil_squeeze(_base() + _tail("box"))
    assert not r.matched and "형태 아님" in r.reason


def test_coil_rejects_wide_volatility() -> None:
    r = is_coil_squeeze(_base() + _tail("wide"))
    assert not r.matched and "수축 부족" in r.reason


def test_coil_rejects_volume_not_dry() -> None:
    # 대칭 형태지만 후반 거래량이 안 마름(오히려 증가) → 코일 미형성
    r = is_coil_squeeze(_base() + _tail("sym", vol_last=1500))
    assert not r.matched and "거래량" in r.reason


def test_coil_rejects_downtrend() -> None:
    # 하락 추세(종가<120선 / 60선 우하향)에서의 수렴은 falling-knife → 제외
    r = is_coil_squeeze(_base(start=300.0, top=CENTER) + _tail("sym"))
    assert not r.matched


def test_coil_insufficient_data() -> None:
    r = is_coil_squeeze(_base(n=40))
    assert not r.matched and "데이터 부족" in r.reason
