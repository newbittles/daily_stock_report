"""순수 함수 테스트 — sizing / ma_exit (외부 의존 0)."""
from __future__ import annotations

from src.indicators.core import moving_average
from src.patterns.core import ma_cross_signal
from src.trading.ma_exit import consecutive_below, exit_decision
from src.trading.sizing import calc_qty, split_sell_qty


def test_ma_cross_signal():
    # 🟢 PULLBACK: 폭등 후 얕은 단기약세 (MA5<MA10 이지만 가격은 MA20 대비 ≥15%)
    pullback = [100.0] * 25 + [350.0, 400.0, 440.0, 460.0, 470.0, 465.0, 430.0, 410.0, 395.0, 385.0]
    assert moving_average(pullback, 5)[-1] < moving_average(pullback, 10)[-1]  # 단기 데드
    assert ma_cross_signal(pullback) == "PULLBACK"

    # ⚠️ CORRECTION: 상승 후 MA20 부근까지 되밀림 (MA5<MA10 + 이격 ≤7%)
    correction = [100.0] * 20 + [110.0, 120.0, 130.0, 128.0, 124.0, 118.0, 112.0, 108.0, 105.0, 103.0]
    assert moving_average(correction, 5)[-1] < moving_average(correction, 10)[-1]
    assert ma_cross_signal(correction) == "CORRECTION"

    # 정배열(MA5>MA10) → None
    assert ma_cross_signal([float(i) for i in range(1, 40)]) is None
    # 데이터 부족 → None
    assert ma_cross_signal([100.0] * 5) is None


def test_decide_exit():
    from src.trading.ma_exit import decide_exit

    # 🟢 PULLBACK → HOLD (건강한 눌림 보호)
    pullback = [100.0] * 25 + [350.0, 400.0, 440.0, 460.0, 470.0, 465.0, 430.0, 410.0, 395.0, 385.0]
    assert decide_exit(pullback)[0] == "HOLD"
    # ⚠️ CORRECTION → SELL_HALF (선제 50%)
    correction = [100.0] * 20 + [110.0, 120.0, 130.0, 128.0, 124.0, 118.0, 112.0, 108.0, 105.0, 103.0]
    assert decide_exit(correction)[0] == "SELL_HALF"
    # 60MA 2연속 이탈 → SELL_ALL (전량 우선)
    assert decide_exit([100.0] * 61 + [40.0, 40.0])[0] == "SELL_ALL"
    # 정상 상승 → HOLD
    assert decide_exit([float(i) for i in range(1, 80)])[0] == "HOLD"


def test_decide_exit_per_strategy():
    """ABCDE별 손절(2026-06-07): A/B=20일선 2일이탈 전량, C/D=기존 단계청산(20MA 50%→60MA 전량)."""
    from src.trading.ma_exit import decide_exit

    breach20 = [100.0] * 19 + [90.0, 90.0]   # 20MA 2연속 이탈 (60MA는 None)
    # tight(A/B만): 20일선이 최종 손절선 → 전량
    assert decide_exit(breach20, strategies=["B"])[0] == "SELL_ALL"
    assert decide_exit(breach20, strategies=["A", "B"])[0] == "SELL_ALL"
    # wide(C/D 포함): 기존 단계청산 → 20MA는 50%
    assert decide_exit(breach20, strategies=["A", "C"])[0] == "SELL_HALF"
    assert decide_exit(breach20, strategies=["D"])[0] == "SELL_HALF"
    # 전략정보 없음(기존 포지션) → wide 폴백
    assert decide_exit(breach20, strategies=None)[0] == "SELL_HALF"
    assert decide_exit(breach20, strategies=[])[0] == "SELL_HALF"

    # 60MA 2연속 이탈은 어느 체계든 전량
    breach60 = [100.0] * 61 + [40.0, 40.0]
    assert decide_exit(breach60, strategies=["B"])[0] == "SELL_ALL"
    assert decide_exit(breach60, strategies=["C"])[0] == "SELL_ALL"

    # 🟢 PULLBACK 보호는 tight에도 적용 (B 눌림목 오탐 방어)
    pullback = [100.0] * 25 + [350.0, 400.0, 440.0, 460.0, 470.0, 465.0, 430.0, 410.0, 395.0, 385.0]
    assert decide_exit(pullback, strategies=["B"])[0] == "HOLD"

    # ⚠️ CORRECTION 선제 50%는 tight에도 유지 (20MA 미이탈 + 5<10 데드 + 이격≤7%)
    corr_above20 = [100.0] * 20 + [110.0, 120.0, 130.0, 128.0, 124.0, 118.0, 114.0, 112.0, 110.0, 109.0]
    assert decide_exit(corr_above20, strategies=["A"])[0] == "SELL_HALF"


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
