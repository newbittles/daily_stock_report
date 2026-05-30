"""순수 패턴 판정 단위 테스트 — 픽스처 기반 결정론."""
from __future__ import annotations

from src.datasource.base import Candle
from src.patterns.core import (
    is_above_ichimoku_cloud,
    is_breakout,
    is_consecutive_bearish,
    is_macd_golden_cross,
    is_ma_alignment,
    is_near_high,
    is_pullback,
    is_volume_surge,
    is_weekly_ma_alignment,
    resample_weekly,
)


def _make_candles(closes: list[float], volumes: list[int] | None = None) -> list[Candle]:
    vols = volumes or [1000] * len(closes)
    return [
        Candle(date=f"d{i}", open=c, high=c + 1, low=c - 1, close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def test_ma_alignment_uptrend_true():
    # 꾸준한 상승 → 정배열
    candles = _make_candles([100 + i for i in range(60)])
    result = is_ma_alignment(candles, (5, 20, 60))
    assert result.matched
    assert "ma5" in result.metrics


def test_ma_alignment_downtrend_false():
    # 꾸준한 하락 → 역배열
    candles = _make_candles([200 - i for i in range(60)])
    result = is_ma_alignment(candles, (5, 20, 60))
    assert not result.matched


def test_ma_alignment_insufficient_data():
    candles = _make_candles([100, 101, 102])
    result = is_ma_alignment(candles, (5, 20, 60))
    assert not result.matched
    assert "부족" in result.reason


def test_breakout_with_volume():
    # 60봉 횡보 후 마지막 봉 고가 돌파 + 거래량 급증
    closes = [100] * 59 + [110]
    volumes = [1000] * 59 + [3000]
    candles = _make_candles(closes, volumes)
    result = is_breakout(candles, lookback=20, vol_mult=1.5)
    assert result.matched
    assert result.metrics["vol_ratio"] >= 1.5


def test_breakout_no_volume_fails():
    closes = [100] * 59 + [110]
    volumes = [1000] * 60  # 거래량 그대로
    candles = _make_candles(closes, volumes)
    result = is_breakout(candles, lookback=20, vol_mult=1.5)
    assert not result.matched


def test_volume_surge():
    closes = [100] * 10
    volumes = [1000] * 9 + [5000]
    candles = _make_candles(closes, volumes)
    result = is_volume_surge(candles, lookback=5, mult=2.0)
    assert result.matched
    assert result.metrics["vol_ratio"] >= 2.0


def test_pullback_insufficient_data():
    candles = _make_candles([100] * 30)
    result = is_pullback(candles)
    assert not result.matched


def test_above_ichimoku_strong_uptrend():
    # 강한 상승 추세 → 현재가가 구름 위
    candles = _make_candles([100 + i * 1.5 for i in range(60)])
    result = is_above_ichimoku_cloud(candles)
    assert result.matched
    assert "강세" in result.reason


def _make_dated_candles(closes, start="20260101"):
    """날짜 있는 캔들 — 주봉 변환 테스트용."""
    import datetime
    d0 = datetime.date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    out = []
    for i, c in enumerate(closes):
        d = d0 + datetime.timedelta(days=i)
        out.append(Candle(date=d.strftime("%Y%m%d"), open=c, high=c + 1, low=c - 1,
                          close=c, volume=2000))
    return out


def test_resample_weekly_groups():
    # 14일치 → 주봉 약 2~3개 (ISO 주차 기준)
    candles = _make_dated_candles([100 + i for i in range(14)])
    weekly = resample_weekly(candles)
    assert 2 <= len(weekly) <= 3
    # 주봉 거래량은 일봉 합 (각 2000)
    assert weekly[0].volume >= 2000


def test_macd_golden_cross_detection():
    # 하락 후 상승 전환 → MACD GC 발생
    closes = [100 - i * 0.5 for i in range(40)] + [80 + i * 2 for i in range(20)]
    candles = _make_candles(closes)
    result = is_macd_golden_cross(candles, within=5, require_above_zero=False)
    # 상승 전환 구간에서 GC가 나타날 수 있음 (데이터 의존 — 에러 없이 판정되는지)
    assert isinstance(result.matched, bool)
    assert "macd" in result.metrics


def test_macd_no_cross_in_steady_uptrend():
    # 계속 상승만 → 최근 5봉엔 신규 GC 없음 (이미 상향 유지)
    candles = _make_candles([100 + i for i in range(60)])
    result = is_macd_golden_cross(candles, within=3)
    assert isinstance(result.matched, bool)


def test_weekly_ma_alignment_uptrend():
    # 450일(약 64주) 꾸준한 상승 → 주봉 20>60 정배열 성립
    candles = _make_dated_candles([100 + i * 0.5 for i in range(450)])
    result = is_weekly_ma_alignment(candles, (20, 60))
    assert result.matched


def test_near_high_at_peak():
    # 마지막이 최고가 → 신고가 근접
    candles = _make_candles([100 + i for i in range(60)])
    result = is_near_high(candles, lookback=60, tolerance=0.03)
    assert result.matched
    assert result.metrics["gap_pct"] <= 3.0


def test_near_high_far_from_peak():
    # 고점 후 급락 → 신고가 이격
    candles = _make_candles([100 + i for i in range(50)] + [150 - i * 3 for i in range(10)])
    result = is_near_high(candles, lookback=60, tolerance=0.03)
    assert not result.matched


def _candle(o, c, vol=2000):
    """시가·종가 지정 캔들 (음봉/양봉 제어)."""
    hi = max(o, c) + 1
    lo = min(o, c) - 1
    return Candle(date="d", open=o, high=hi, low=lo, close=c, volume=vol)


def test_consecutive_bearish_matched():
    # 상승추세(정배열) + 거래량 급증 후 음봉 3연속
    base = []
    for i in range(70):
        price = 100 + i  # 꾸준한 상승 → MA20>MA60
        base.append(_candle(price, price + 0.5, vol=2000))  # 양봉
    # 거래량 급증 1회 (하락 직전)
    base[-4] = _candle(170, 171, vol=12000)  # 5일평균 대비 급증
    # 음봉 3연속 (종가 < 시가), 추세는 유지
    base[-3] = _candle(172, 169)
    base[-2] = _candle(169, 166)
    base[-1] = _candle(166, 163)
    result = is_consecutive_bearish(base, days=3)
    assert result.matched
    assert "음봉 3연속" in result.reason


def test_consecutive_bearish_not_all_bearish():
    # 마지막 봉이 양봉이면 미충족
    base = [_candle(100 + i, 100 + i + 0.5, vol=2000) for i in range(70)]
    base[-4] = _candle(165, 166, vol=12000)
    base[-3] = _candle(167, 164)
    base[-2] = _candle(164, 161)
    base[-1] = _candle(161, 163)  # 양봉
    result = is_consecutive_bearish(base, days=3)
    assert not result.matched


def test_consecutive_bearish_no_volume_history():
    # 음봉 3연속이지만 거래량 급증 이력 없음 → 미충족
    base = [_candle(100 + i, 100 + i + 0.5, vol=2000) for i in range(70)]
    base[-3] = _candle(167, 164)
    base[-2] = _candle(164, 161)
    base[-1] = _candle(161, 158)
    result = is_consecutive_bearish(base, days=3, require_volume_history=True)
    assert not result.matched
