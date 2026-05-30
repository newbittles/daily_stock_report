"""순수 패턴 판정 함수.

입력: Candle 리스트 (src.datasource.base.Candle) — 과거→최신 순.
주의: domain 순수성 위해 Candle을 값으로만 사용 (속성 접근), 외부 호출 없음.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.datasource.base import Candle
from src.indicators.core import bollinger_bands, ichimoku, macd, moving_average, rsi


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


def _iso_week_key(date_str: str) -> str:
    """YYYYMMDD → ISO 연도-주차 키 (주봉 그룹핑용). 순수 — datetime만 사용."""
    import datetime
    try:
        d = datetime.date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        iso = d.isocalendar()
        return f"{iso[0]}-{iso[1]:02d}"
    except (ValueError, IndexError):
        return date_str


def resample_weekly(candles: list[Candle]) -> list[Candle]:
    """일봉 → 주봉 변환 (순수). 같은 ISO 주차끼리 OHLCV 집계.

    open=주 첫날 시가, high=주 최고, low=주 최저, close=주 마지막 종가, volume=주 합계.
    """
    if not candles:
        return []
    groups: dict[str, list[Candle]] = {}
    order: list[str] = []
    for c in candles:
        key = _iso_week_key(c.date)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    weekly: list[Candle] = []
    for key in order:
        week = groups[key]
        weekly.append(Candle(
            date=week[-1].date,
            open=week[0].open,
            high=max(c.high for c in week),
            low=min(c.low for c in week),
            close=week[-1].close,
            volume=sum(c.volume for c in week),
        ))
    return weekly


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


def is_consecutive_bearish(
    candles: list[Candle], days: int = 3,
    require_alignment: bool = True,
    volume_surge_lookback: int = 10, volume_surge_mult: float = 2.0,
    require_volume_history: bool = True,
) -> PatternResult:
    """종가 눌림목 — 음봉 N연속 + (거래량 급증 이력) + (정배열).

    사용자 전략: 주도주/정배열 종목이 거래량 실린 뒤 N일 음봉 조정 →
    그 마지막 날 종가 매수, 다음날 반등 노림.

    조건:
      1. 최근 days봉 모두 음봉 (종가 < 시가)
      2. (옵션) 정배열 5 > 20 > 60 — 추세 살아있음
      3. (옵션) 하락 시작 전 volume_surge_lookback일 내 거래량 급증 이력
    """
    if len(candles) < max(60, days + volume_surge_lookback + 5):
        return PatternResult(False, "데이터 부족")

    # 1. 최근 days봉 음봉 연속
    recent = candles[-days:]
    all_bearish = all(c.close < c.open for c in recent)
    if not all_bearish:
        return PatternResult(False, f"{days}일 음봉 연속 아님")

    metrics: dict[str, float] = {}
    # 하락폭 (days 시작 전 종가 대비 현재)
    base_close = candles[-days - 1].close
    cur_close = candles[-1].close
    decline_pct = (cur_close - base_close) / base_close * 100 if base_close else 0.0
    metrics["decline_pct"] = round(decline_pct, 2)
    metrics["days"] = days

    reasons = [f"음봉 {days}연속 ({decline_pct:+.1f}%)"]

    # 2. 정배열
    if require_alignment:
        align = is_ma_alignment(candles[:-0] if False else candles, (5, 20, 60))
        # 주의: 음봉 조정 중이라 5>20>60이 깨졌을 수 있음 → 20>60만 확인(추세 유지)
        closes = _closes(candles)
        ma20 = moving_average(closes, 20)[-1]
        ma60 = moving_average(closes, 60)[-1]
        if ma20 is None or ma60 is None or not (ma20 > ma60):
            return PatternResult(False, "추세 약함 (MA20<MA60)", metrics)
        reasons.append("상승추세 (MA20>MA60)")

    # 3. 거래량 급증 이력 (하락 구간 직전)
    if require_volume_history:
        # 하락 시작 직전 봉들에서 거래량 급증 찾기
        pre_decline = candles[: -days]  # 하락 전 구간
        if len(pre_decline) < volume_surge_lookback + 5:
            return PatternResult(False, "거래량 이력 데이터 부족", metrics)
        window = pre_decline[-volume_surge_lookback:]
        vols = [c.volume for c in pre_decline]
        surge_found = False
        max_ratio = 0.0
        for i in range(len(pre_decline) - volume_surge_lookback, len(pre_decline)):
            if i < 5:
                continue
            avg5 = sum(vols[i - 5 : i]) / 5
            if avg5 > 0:
                ratio = vols[i] / avg5
                max_ratio = max(max_ratio, ratio)
                if ratio >= volume_surge_mult:
                    surge_found = True
        metrics["max_vol_ratio"] = round(max_ratio, 1)
        if not surge_found:
            return PatternResult(False, f"거래량 급증 이력 없음 (최대 {max_ratio:.1f}배)", metrics)
        reasons.append(f"직전 거래량 급증 {max_ratio:.1f}배")

    return PatternResult(True, " / ".join(reasons), metrics)


def is_macd_golden_cross(
    candles: list[Candle], within: int = 3, require_above_zero: bool = True,
    fast: int = 12, slow: int = 26, signal: int = 9,
) -> PatternResult:
    """MACD 골든크로스 — 최근 within봉 이내 MACD선이 시그널선 상향 돌파.

    require_above_zero=True면 0선 위 교차만 인정 (강한 신호).
    """
    closes = _closes(candles)
    if len(closes) < slow + signal + within:
        return PatternResult(False, "데이터 부족")

    macd_line, sig_line, _hist = macd(closes, fast, slow, signal)

    # 최근 within봉 구간에서 GC 탐색
    gc_found = False
    gc_idx = -1
    n = len(closes)
    for i in range(n - within, n):
        if i < 1:
            continue
        m0, m1 = macd_line[i - 1], macd_line[i]
        s0, s1 = sig_line[i - 1], sig_line[i]
        if None in (m0, m1, s0, s1):
            continue
        if m0 <= s0 and m1 > s1:  # 상향 돌파
            if require_above_zero and m1 <= 0:
                continue
            gc_found = True
            gc_idx = i
            break

    cur_macd = macd_line[-1] if macd_line[-1] is not None else 0.0
    metrics = {"macd": round(cur_macd, 3)}
    if sig_line[-1] is not None:
        metrics["signal"] = round(sig_line[-1], 3)

    if gc_found:
        zero_note = " (0선 위)" if cur_macd > 0 else ""
        ago = n - 1 - gc_idx
        when = "당일" if ago == 0 else f"{ago}봉 전"
        return PatternResult(True, f"MACD 골든크로스{zero_note} {when}", metrics)
    return PatternResult(False, "MACD GC 없음", metrics)


def is_weekly_ma_alignment(
    candles: list[Candle], periods: tuple[int, ...] = (20, 60),
) -> PatternResult:
    """주봉 정배열 — 일봉을 주봉으로 변환 후 MA 정배열 판정.

    멀티 타임프레임: 상위 추세(주봉) 확인용.
    """
    weekly = resample_weekly(candles)
    if len(weekly) < max(periods):
        return PatternResult(False, f"주봉 데이터 부족 ({len(weekly)}주)")

    result = is_ma_alignment(weekly, periods)
    if result.matched:
        labels = " > ".join(f"{p}주" for p in periods)
        return PatternResult(True, f"주봉 정배열 ({labels})", result.metrics)
    return PatternResult(False, "주봉 정배열 아님", result.metrics)


def is_near_high(
    candles: list[Candle], lookback: int = 250, tolerance: float = 0.03,
) -> PatternResult:
    """신고가 근접 — 현재가가 최근 lookback봉 최고가의 -tolerance 이내.

    52주 신고가: lookback=250 (약 1년 거래일).
    """
    if len(candles) < 2:
        return PatternResult(False, "데이터 부족")
    highs = _highs(candles)
    window = highs[-lookback:] if len(highs) > lookback else highs
    period_high = max(window)
    price = candles[-1].close
    if period_high <= 0:
        return PatternResult(False, "고가 데이터 오류")

    gap = (period_high - price) / period_high  # 신고가 대비 하락률
    metrics = {
        "price": round(price, 1),
        "period_high": round(period_high, 1),
        "gap_pct": round(gap * 100, 2),
    }
    weeks = lookback // 5
    if gap <= tolerance:
        return PatternResult(True, f"{weeks}주 신고가 근접 (-{gap*100:.1f}%)", metrics)
    return PatternResult(False, f"신고가 이격 -{gap*100:.1f}%", metrics)


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
