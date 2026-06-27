"""60선 이탈 후 직전고점 복귀실패(lower high) 판정 결정론 검증 (사용자 2026-06-22).

상승추세 종목이 60선 이탈 후 직전 고점을 회복 못하면 matched(추세 훼손 경고). 가중치0 경고.
"""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import ma60_recovery_failure


def _candles(closes: list[float]) -> list[Candle]:
    return [Candle(date=str(i), open=c, high=c, low=c, close=c, volume=0)
            for i, c in enumerate(closes)]


def test_failed_recovery_after_breach() -> None:
    # 0~100: 100→200 강한 상승추세(60선 우상향, 직전고점 ≈ 200)
    # 100~130: 200→160 하락(60선 이탈, 직전고점 미복귀)
    closes = [100 + i for i in range(101)]              # 100 → 200
    closes += [200 - (i + 1) * 1.3 for i in range(30)]  # 200 → 161 (이탈·미복귀)
    r = ma60_recovery_failure(_candles(closes))
    assert r.matched is True
    assert r.metrics["failed"] == 1
    assert r.metrics["reclaimed"] == 0
    assert r.metrics["from_prior_high_pct"] < 0


def test_no_breach_recent() -> None:
    # 줄곧 상승추세 유지 — 최근 60선 이탈 없음
    closes = [100 + i for i in range(160)]
    r = ma60_recovery_failure(_candles(closes))
    assert r.matched is False
    assert r.metrics["failed"] == 0


def test_reclaimed_is_not_failure() -> None:
    # 상승 → 잠깐 60선 이탈 → 직전고점 위로 복귀(눌림) → 경고 아님
    closes = [100 + i for i in range(101)]                 # 100 → 200
    closes += [200 - (i + 1) * 3 for i in range(10)]       # 일시 하락(이탈)
    closes += [170 + (i + 1) * 5 for i in range(15)]       # 다시 급등 → 직전고점(200) 상향 복귀
    r = ma60_recovery_failure(_candles(closes))
    assert r.matched is False
    assert r.metrics.get("reclaimed") == 1


def test_insufficient_data() -> None:
    r = ma60_recovery_failure(_candles([100.0] * 40))
    assert r.matched is False
    assert r.metrics["failed"] == 0
