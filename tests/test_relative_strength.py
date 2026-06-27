"""상대강도(relative_strength) 정규화 결정론 검증 (사용자 2026-06-22)."""
from __future__ import annotations

from src.indicators.core import relative_strength


def test_flat_when_equal_growth() -> None:
    # 종목·지수가 동일 비율로 움직이면 RS는 100 유지
    stock = [100.0, 110.0, 121.0, 133.1]
    index = [100.0, 110.0, 121.0, 133.1]
    rs = relative_strength(stock, index)
    assert all(abs(v - 100.0) < 1e-9 for v in rs)


def test_rises_when_outperforming() -> None:
    # 종목이 지수보다 강하면 RS 상승(첫날=100)
    stock = [100.0, 120.0, 150.0]
    index = [100.0, 100.0, 100.0]
    rs = relative_strength(stock, index)
    assert rs[0] == 100.0
    assert rs[1] > rs[0] and rs[2] > rs[1]


def test_none_on_zero_or_missing_index() -> None:
    rs = relative_strength([100.0, 110.0], [0.0, 100.0])
    assert rs[0] is None
    # 첫 유효일(인덱스1)을 base로 100
    assert abs(rs[1] - 100.0) < 1e-9


def test_length_is_min_of_inputs() -> None:
    rs = relative_strength([1.0, 2.0, 3.0], [1.0, 1.0])
    assert len(rs) == 2
