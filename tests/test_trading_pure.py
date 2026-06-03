"""순수 함수 테스트 — sizing / ma_exit (외부 의존 0)."""
from __future__ import annotations

from src.trading.ma_exit import consecutive_below, exit_decision
from src.trading.sizing import calc_qty, split_sell_qty


def test_calc_qty():
    assert calc_qty(82500) == 12          # 1,000,000 // 82500
    assert calc_qty(1_000_000) == 1
    assert calc_qty(1_200_000) == 0       # 1주도 예산 초과
    assert calc_qty(0) == 0
    assert calc_qty(-5) == 0
    assert calc_qty(50000, budget=500_000) == 10


def test_split_sell_qty():
    assert split_sell_qty(12) == (6, 6)
    assert split_sell_qty(11) == (5, 6)
    assert split_sell_qty(1) == (1, 0)    # 1주는 쪼갤 수 없음 → 전량
    assert split_sell_qty(2) == (1, 1)


def test_consecutive_below():
    closes = [10, 10, 10]
    ma = [9, 11, 11]   # 최근 2개 모두 close<ma → True
    assert consecutive_below(closes, ma, 2) is True
    ma2 = [9, 9, 11]   # 마지막만 이탈 → False
    assert consecutive_below(closes, ma2, 2) is False
    assert consecutive_below([10], [9], 2) is False  # 길이 부족
    assert consecutive_below([10, 10], [None, 9], 2) is False  # MA None


def test_exit_decision():
    # 60MA 2연속 이탈(가장 심각) → SELL_ALL (60MA 2값 필요 → ≥61봉)
    closes_all = [100.0] * 61 + [40.0, 40.0]
    assert exit_decision(closes_all) == "SELL_ALL"
    # 20MA만 2연속 이탈 → SELL_HALF (21봉: 60MA는 전부 None이라 미발동)
    closes_half = [100.0] * 19 + [90.0, 90.0]
    assert exit_decision(closes_half) == "SELL_HALF"
    # 정상 상승 → HOLD
    assert exit_decision([float(i) for i in range(1, 80)]) == "HOLD"
