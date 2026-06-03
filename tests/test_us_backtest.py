"""L1 단위 — 미국 백테스트 엔진 (src/backtest/us_engine.py). 결정론, 네트워크 없음."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.backtest.us_engine import (
    Trade,
    backtest_ma_stop,
    backtest_with_exit,
    make_a_exit,
    make_ma_stop_exit,
    summarize,
)
from src.datasource.base import Candle


def _candles(closes: list[float], opens: list[float] | None = None) -> list[Candle]:
    base = datetime(2024, 1, 1)
    out: list[Candle] = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        out.append(Candle(
            date=(base + timedelta(days=i)).strftime("%Y%m%d"),
            open=o, high=max(o, c), low=min(o, c), close=c, volume=1000,
        ))
    return out


def test_uptrend_holds_until_end():
    """우상향 → MA 이탈 없음 → 미청산, 수익 양(+)."""
    candles = _candles([100 + i for i in range(200)])
    target = candles[140].date
    trades = backtest_ma_stop(candles, lambda cs: cs[-1].date == target,
                              ma_stop=20, warmup=130, min_gap_days=3)
    assert len(trades) == 1
    assert trades[0].exit_reason == "미청산"
    assert trades[0].ret_pct > 0


def test_drop_triggers_ma_stop_exit():
    """상승 후 급락 → MA20 2일연속 이탈 청산."""
    closes = [100 + i for i in range(150)] + [250 - 5 * i for i in range(50)]
    candles = _candles(closes)
    target = candles[148].date
    trades = backtest_ma_stop(candles, lambda cs: cs[-1].date == target,
                              ma_stop=20, warmup=130, min_gap_days=0)
    assert len(trades) == 1
    assert "2일이탈" in trades[0].exit_reason
    assert trades[0].hold_days > 0


def test_continuous_signal_enters_once():
    """연속 진입 시그널은 클러스터 첫날만 진입(min_gap_days)."""
    candles = _candles([100 + i for i in range(200)])
    trades = backtest_ma_stop(candles, lambda cs: True,
                              ma_stop=20, warmup=130, min_gap_days=3)
    assert len(trades) == 1


def test_no_signal_no_trades():
    candles = _candles([100 + i for i in range(200)])
    trades = backtest_ma_stop(candles, lambda cs: False, ma_stop=20, warmup=130)
    assert trades == []


def test_summarize_basic():
    trades = [
        Trade("d1", 100, "d2", 110, 10.0, 5, "r"),
        Trade("d3", 100, "d4", 90, -10.0, 3, "r"),
        Trade("d5", 100, "d6", 130, 30.0, 7, "r"),
    ]
    s = summarize(trades)
    assert s["n"] == 3
    assert s["win_pct"] == round(2 / 3 * 100, 1)
    assert s["avg_ret"] == round((10 - 10 + 30) / 3, 2)
    assert s["worst"] == -10.0
    assert s["best"] == 30.0


def test_summarize_empty():
    assert summarize([])["n"] == 0


def test_cost_pct_deducted():
    """왕복 거래비용 cost_pct가 수익률에서 차감된다."""
    candles = _candles([100 + i for i in range(200)])
    target = candles[140].date
    t0 = backtest_with_exit(candles, lambda cs: cs[-1].date == target,
                            make_ma_stop_exit(20), warmup=130, min_gap_days=3, cost_pct=0.0)
    t1 = backtest_with_exit(candles, lambda cs: cs[-1].date == target,
                            make_ma_stop_exit(20), warmup=130, min_gap_days=3, cost_pct=0.5)
    assert len(t0) == len(t1) == 1
    assert abs((t0[0].ret_pct - t1[0].ret_pct) - 0.5) < 0.02  # 0.5%p 차감


def test_a_exit_triggers_on_drop():
    """A 복합청산: 상승 후 급락 시 구름이탈/MACD/20선 중 하나로 청산."""
    closes = [100 + i for i in range(150)] + [250 - 6 * i for i in range(50)]
    candles = _candles(closes)
    target = candles[148].date
    trades = backtest_with_exit(candles, lambda cs: cs[-1].date == target,
                                make_a_exit(), warmup=130, min_gap_days=0, cost_pct=0.0)
    assert len(trades) == 1
    assert trades[0].exit_reason in ("구름이탈", "MACD약화+20선", "20선2일이탈")


def test_a_exit_holds_on_uptrend():
    """우상향 지속 → A 청산 없음 (미청산)."""
    candles = _candles([100 + i for i in range(200)])
    target = candles[140].date
    trades = backtest_with_exit(candles, lambda cs: cs[-1].date == target,
                                make_a_exit(), warmup=130, min_gap_days=3, cost_pct=0.0)
    assert len(trades) == 1
    assert trades[0].exit_reason == "미청산"
