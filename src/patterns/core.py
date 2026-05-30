"""순수 패턴 판정 함수.

입력: Candle 리스트 (src.datasource.base.Candle) — 과거→최신 순.
주의: domain 순수성 위해 Candle을 값으로만 사용 (속성 접근), 외부 호출 없음.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.datasource.base import Candle
from src.indicators.core import bollinger_bands, ichimoku, moving_average, rsi


@dataclass
class PatternResult:
    """패턴 판정 결과 + 근거 수치."""
    matched: bool
    reason: str = ""
    metrics: dict[str, float] = field(default_factory=dict)


def _closes(candles: list[Candle]) -> list[float]:
    return [c.close for c in candles]


def _highs(candles: list[Candle]) -> list[float]:
    return [c.high for c in candles]


def _lows(candles: list[Candle]) -> list[float]:
    return [c.low for c in candles]


def _volumes(candles: list[Candle]) -> list[int]:
    return [c.volume for c in candles]


def is_ma_alignment(
    candles: list[Candle], periods: tuple[int, ...] = (5, 20, 60)
) -> PatternResult:
    """정배열 판정 — MA가 period 오름차순으로 위→아래 정렬 (MA5 > MA20 > MA60).

    상승추세 확인용. 최신 봉 기준.
    """
    closes = _closes(candles)
    if len(closes) < max(periods):
        return PatternResult(False, "데이터 부족")

    ma_vals: dict[int, float] = {}
    for p in periods:
        series = moving_average(closes, p)
        last = series[-1]
        if last is None:
            return PatternResult(False, f"MA{p} 계산 불가")
        ma_vals[p] = last

    ordered = [ma_vals[p] for p in periods]
    aligned = all(ordered[i] > ordered[i + 1] for i in range(len(ordered) - 1))
    metrics = {f"ma{p}": round(v, 1) for p, v in ma_vals.items()}

    if aligned:
        labels = " > ".join(f"MA{p}" for p in periods)
        return PatternResult(True, f"정배열 ({labels})", metrics)
    return PatternResult(False, "정배열 아님", metrics)


def is_pullback(
    candles: list[Candle], ma_period: int = 20, tolerance: float = 0.03,
    rsi_max: float = 55.0,
) -> PatternResult:
    """눌림목 판정 — 상승추세 중 MA(기본 20)선 근접 + RSI 과열 아님.

    조건:
      1. 직전 일정 기간 상승추세 (MA20 > MA60)
      2. 현재가가 MA20 근처 (±tolerance 이내)
      3. RSI <= rsi_max (과매수 아님 → 매수 여지)
    """
    closes = _closes(candles)
    if len(closes) < 60:
        return PatternResult(False, "데이터 부족 (60봉 필요)")

    ma20 = moving_average(closes, ma_period)[-1]
    ma60 = moving_average(closes, 60)[-1]
    rsi_val = rsi(closes, 14)[-1]
    price = closes[-1]

    if ma20 is None or ma60 is None or rsi_val is None:
        return PatternResult(False, "지표 계산 불가")

    uptrend = ma20 > ma60
    near_ma = abs(price - ma20) / ma20 <= tolerance
    not_overbought = rsi_val <= rsi_max

    metrics = {
        "price": round(price, 1),
        "ma20": round(ma20, 1),
        "ma60": round(ma60, 1),
        "rsi": round(rsi_val, 1),
        "ma20_gap_pct": round((price - ma20) / ma20 * 100, 2),
    }

    if uptrend and near_ma and not_overbought:
        return PatternResult(
            True,
            f"눌림목 (MA20 {metrics['ma20_gap_pct']:+.1f}%, RSI {rsi_val:.0f})",
            metrics,
        )
    fails = []
    if not uptrend:
        fails.append("추세약함(MA20<MA60)")
    if not near_ma:
        fails.append(f"MA20 이격{metrics['ma20_gap_pct']:+.1f}%")
    if not not_overbought:
        fails.append(f"RSI과열{rsi_val:.0f}")
    return PatternResult(False, " / ".join(fails), metrics)


def is_breakout(
    candles: list[Candle], lookback: int = 20, vol_mult: float = 1.5,
) -> PatternResult:
    """돌파 판정 — 최근 lookback 고가 갱신 + 거래량 증가.

    조건:
      1. 현재 종가가 직전 lookback 봉 최고가 돌파
      2. 당일 거래량 >= 직전 거래량 평균 * vol_mult
    """
    if len(candles) < lookback + 1:
        return PatternResult(False, "데이터 부족")

    closes = _closes(candles)
    highs = _highs(candles)
    volumes = _volumes(candles)

    prev_high = max(highs[-lookback - 1 : -1])  # 직전 lookback (당일 제외)
    price = closes[-1]
    avg_vol = sum(volumes[-lookback - 1 : -1]) / lookback
    cur_vol = volumes[-1]

    metrics = {
        "price": round(price, 1),
        "prev_high": round(prev_high, 1),
        "vol_ratio": round(cur_vol / avg_vol, 2) if avg_vol else 0.0,
    }

    broke = price > prev_high
    vol_ok = avg_vol > 0 and cur_vol >= avg_vol * vol_mult

    if broke and vol_ok:
        return PatternResult(
            True,
            f"돌파 ({lookback}일 고가 갱신, 거래량 {metrics['vol_ratio']:.1f}배)",
            metrics,
        )
    fails = []
    if not broke:
        fails.append("고가미달")
    if not vol_ok:
        fails.append(f"거래량부족({metrics['vol_ratio']:.1f}배)")
    return PatternResult(False, " / ".join(fails), metrics)


def is_volume_surge(candles: list[Candle], lookback: int = 5, mult: float = 2.0) -> PatternResult:
    """거래량 급증 — 당일 거래량이 직전 평균의 mult배 이상."""
    if len(candles) < lookback + 1:
        return PatternResult(False, "데이터 부족")
    volumes = _volumes(candles)
    avg_vol = sum(volumes[-lookback - 1 : -1]) / lookback
    cur_vol = volumes[-1]
    ratio = cur_vol / avg_vol if avg_vol else 0.0
    metrics = {"vol_ratio": round(ratio, 2), "cur_vol": cur_vol}
    if avg_vol > 0 and ratio >= mult:
        return PatternResult(True, f"거래량 급증 ({ratio:.1f}배)", metrics)
    return PatternResult(False, f"거래량 평범 ({ratio:.1f}배)", metrics)


def is_above_ichimoku_cloud(candles: list[Candle]) -> PatternResult:
    """일목구름 위 — 현재가가 선행스팬A·B(구름) 위에 위치 (양운 상승)."""
    if len(candles) < 52:
        return PatternResult(False, "데이터 부족 (52봉 필요)")
    highs, lows, closes = _highs(candles), _lows(candles), _closes(candles)
    cloud = ichimoku(highs, lows, closes)
    span_a = cloud["senkou_a"][-1]
    span_b = cloud["senkou_b"][-1]
    price = closes[-1]
    if span_a is None or span_b is None:
        return PatternResult(False, "구름 계산 불가")

    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)
    metrics = {
        "price": round(price, 1),
        "cloud_top": round(cloud_top, 1),
        "cloud_bottom": round(cloud_bottom, 1),
    }
    if price > cloud_top:
        return PatternResult(True, "일목구름 위 (강세)", metrics)
    if price < cloud_bottom:
        return PatternResult(False, "일목구름 아래 (약세)", metrics)
    return PatternResult(False, "구름 내부 (중립)", metrics)


def is_bollinger_breakout(
    candles: list[Candle], period: int = 20, num_std: float = 2.0
) -> PatternResult:
    """볼린저밴드 상단 돌파."""
    if len(candles) < period:
        return PatternResult(False, "데이터 부족")
    closes = _closes(candles)
    upper, mid, _lower = bollinger_bands(closes, period, num_std)
    price = closes[-1]
    if upper[-1] is None:
        return PatternResult(False, "밴드 계산 불가")
    metrics = {"price": round(price, 1), "upper": round(upper[-1], 1)}
    if price > upper[-1]:
        return PatternResult(True, "볼린저 상단 돌파", metrics)
    return PatternResult(False, "밴드 내부", metrics)
