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


def is_ma20_pullback(
    candles: list[Candle], ma_period: int = 20,
    surge_lookback: int = 10, surge_pct: float = 15.0,
    max_surge_pct: float | None = None,
    max_gap: float = 0.45,
    require_below_ma5: bool = True,
    min_pullback_pct: float = 2.0,
    max_pullback_pct: float | None = None,
    require_ma20_rising: bool = True,
    require_new_high: bool = True,
    new_high_lookback: int = 60,
) -> PatternResult:
    """20일선 눌림목 (사용자 전략) — 가격 급등 후 20일선 위에서 '단기 조정 중'인 주도주.

    급등 정의 = 가격 기준 (거래량 무관 — 대형주도 포착):
      0. 최근 surge_lookback일 내 단기저점→고점 +surge_pct% 이상 상승 (한번의 급등)
      1. 20일선 우상향 (추세 살아있음 — 박스권/하락추세 제외)
      2. 종가 >= 20일선 (핵심 — 손절선)
      3. 20일선 이격 max_gap 이내 (과열 추격 방지)
      4. 단기 눌림 진입: 종가 <= 5일선 (오르는 날 제외)
      5. 단기 고점 대비 min_pullback_pct 이상 하락 (실제 눌림 확인)

    정배열·RSI·MACD·일목·거래량은 제외 (사용자 요청: 이평선+가격+캔들).
    매수 후 관심종목 등록 → 20일선 이탈 시 손절 (단타 아님).
    """
    closes = _closes(candles)
    if len(closes) < max(60, surge_lookback + 5):
        return PatternResult(False, "데이터 부족")

    ma20_series = moving_average(closes, ma_period)
    ma20 = ma20_series[-1]
    ma5 = moving_average(closes, 5)[-1]
    if ma20 is None or ma5 is None:
        return PatternResult(False, "이평선 계산 불가")

    price = closes[-1]
    gap = (price - ma20) / ma20
    metrics = {"price": round(price, 1), "ma20": round(ma20, 1), "ma20_gap_pct": round(gap * 100, 2)}

    # 0. 가격 급등 이력 — 최근 surge_lookback일 내 저점→고점 상승률
    highs = _highs(candles)
    lows = _lows(candles)
    window_lo = min(lows[-surge_lookback:])
    window_hi = max(highs[-surge_lookback:])
    surge_rate = (window_hi - window_lo) / window_lo * 100 if window_lo > 0 else 0.0
    metrics["surge_pct"] = round(surge_rate, 1)
    if surge_rate < surge_pct:
        return PatternResult(False, f"급등 없음 ({surge_lookback}일 +{surge_rate:.0f}%)", metrics)
    # 극단적 급등 상한 — 너무 가파른 급등(+N%↑)은 진입 보류 (과열 꼭지 회피)
    if max_surge_pct is not None and surge_rate > max_surge_pct:
        return PatternResult(False, f"극단 급등 ({surge_lookback}일 +{surge_rate:.0f}%, 진입보류)", metrics)

    # 0-b. 신고가 경신 — 급등 고점이 직전 장기 고점을 넘었는가 (추세주 vs 박스권)
    #      박스권은 급등해도 이전 고점 못 넘음 → 제외. 추세주는 계단식 신고가 경신.
    if require_new_high and len(highs) >= new_high_lookback + surge_lookback:
        prior_high = max(highs[-(new_high_lookback + surge_lookback):-surge_lookback])
        metrics["prior_high"] = round(prior_high, 1)
        if window_hi < prior_high:
            short = (prior_high - window_hi) / prior_high * 100
            return PatternResult(False, f"신고가 미달 (직전고점 -{short:.1f}%, 박스권)", metrics)

    # 1. 60일선 우상향 (중기 추세 살아있음 — 박스권/하락추세 제외)
    #    급등은 20일선을 일시적으로 끌어올리므로 20일선 기울기로는 박스권을 못 거름.
    #    60일선(중기)이 우상향이어야 '진짜 추세주'.
    if require_ma20_rising:
        ma60_series = moving_average(closes, 60)
        ma60_now, ma60_10ago = ma60_series[-1], ma60_series[-11] if len(ma60_series) >= 11 else None
        if ma60_now is not None and ma60_10ago is not None:
            ma60_slope = (ma60_now - ma60_10ago) / ma60_10ago * 100
            metrics["ma60_slope_pct"] = round(ma60_slope, 2)
            if ma60_now <= ma60_10ago:
                return PatternResult(False, f"60일선 하락/횡보 ({ma60_slope:+.1f}%)", metrics)

    # 2. 종가 >= 20일선 (핵심 손절선)
    if price < ma20:
        return PatternResult(False, f"20일선 이탈 ({gap*100:+.1f}%)", metrics)

    # 2. 과열 상단 제한
    if gap > max_gap:
        return PatternResult(False, f"20일선 이격 과대 (+{gap*100:.0f}%)", metrics)

    # 3. 단기 눌림 진입 — 종가 <= 5일선 (상승 중인 날 제외)
    ma5_gap = (price - ma5) / ma5 * 100
    metrics["ma5_gap_pct"] = round(ma5_gap, 2)
    if require_below_ma5 and price > ma5:
        return PatternResult(False, f"단기 상승 중 (5일선 +{ma5_gap:.1f}%, 눌림 아님)", metrics)

    # 4. 단기 고점(최근 5일) 대비 하락 — 실제 눌림 확인
    hi5 = max(highs[-5:])
    pullback = (hi5 - price) / hi5 * 100
    metrics["pullback_pct"] = round(pullback, 2)
    if pullback < min_pullback_pct:
        return PatternResult(False, f"눌림 부족 (5일고점대비 -{pullback:.1f}%)", metrics)
    # 눌림 깊이 상한 — 너무 깊으면 추세 약화 (5/13 과열 후 깊은 눌림 회피)
    if max_pullback_pct is not None and pullback > max_pullback_pct:
        return PatternResult(False, f"눌림 과대 (5일고점대비 -{pullback:.1f}%, 추세약화)", metrics)

    slope_txt = f", 60선기울기 {metrics.get('ma60_slope_pct', 0):+.1f}%" if "ma60_slope_pct" in metrics else ""
    reasons = [
        f"급등 ({surge_lookback}일 +{surge_rate:.0f}%)",
        f"20일선 위 (+{gap*100:.1f}%{slope_txt})",
        f"단기눌림 (5일선 {ma5_gap:+.1f}%, 5일고점 -{pullback:.1f}%)",
    ]
    return PatternResult(True, " / ".join(reasons), metrics)


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


def is_convergence_breakout(
    candles: list[Candle],
    conv_max: float = 6.0,
    gap120_min: float = 2.0, gap120_max: float | None = None,
    require_long_align: bool = False,
    strict_align: bool = True,
    require_new_high: bool = False, new_high_lookback: int = 60, new_high_tol: float = 0.03,
    require_ma120_rising: bool = False,
    vol_conv_lookback: int = 5, breakout_vol_mult: float = 1.5,
    enable_vol_breakout: bool = False,
    reject_macd_falling: bool = True,
) -> PatternResult:
    """A 전략 — 이평선 수렴(박스권) 후 신고가 돌파 대세상승 시작.

    사용자 역산(8개 사례) + 박스권/추세주 구분 보강:
      1. 단기 조건:
         - strict_align=True : 5 > 10 > 20 정배열 (타이트)
         - strict_align=False: 종가가 5/10/20 위 (수렴 후 상승 전환)
      2. 단기 이평 수렴: 5/10/20 이격 <= conv_max% (박스권 = 모여있음)
      3. 종가 > 120일선 (이격 >= gap120_min%, 장기 상승추세 위)
         ※ gap120_max=None: 이격 상한 없음 (이미 오른 추세주 안 거름 — 대덕전자 1월)
      4. 신고가 경신: 종가가 최근 new_high_lookback일 고가의 -tol 이내
         ※ 박스권 반등(신고가 못넘음) 제외 — NAVER/카카오 오감지 차단
      5. (옵션) 장기 정배열 60 > 120

    A1: strict_align=True,  require_long_align=False
    A2: strict_align=True,  require_long_align=True
    A3: strict_align=False, require_long_align=False (수렴+상승전환+신고가, 권장)
    MACD·주봉정배열은 제외 (사례 지지 약함).
    """
    closes = _closes(candles)
    if len(closes) < 135:
        return PatternResult(False, "데이터 부족 (120일선 필요)")

    ma120_series = moving_average(closes, 120)
    ma5 = moving_average(closes, 5)[-1]
    ma10 = moving_average(closes, 10)[-1]
    ma20 = moving_average(closes, 20)[-1]
    ma60 = moving_average(closes, 60)[-1]
    ma120 = ma120_series[-1]
    if None in (ma5, ma10, ma20, ma60, ma120):
        return PatternResult(False, "이평선 계산 불가")

    price = closes[-1]
    metrics = {"price": round(price, 1)}

    # 1. 단기 조건
    if strict_align:
        if not (ma5 > ma10 > ma20):
            return PatternResult(False, "단기 정배열 아님 (5>10>20)", metrics)
        short_txt = "단기정배열 5>10>20"
    else:
        if not (price > ma5 and price > ma10 and price > ma20):
            return PatternResult(False, "종가가 단기 이평 아래 (상승전환 전)", metrics)
        short_txt = "수렴대 위로 상승전환"

    # 2. 단기 이평 수렴 (5/10/20 이격)
    #    당일 수렴 OR (직전 수렴 이력 + 당일 거래량 돌파) — 수렴 깨진 강한 돌파 구제
    conv = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ma20 * 100
    metrics["conv_pct"] = round(conv, 2)
    if conv > conv_max:
        if not enable_vol_breakout:
            # OR경로 비활성(기본) — 데이터상 포착 동일하고 노이즈만 늘려 제거
            return PatternResult(False, f"수렴 안됨 (이격 {conv:.1f}%)", metrics)
        # 수렴 깨짐 — 직전 수렴 이력 + 당일 거래량 급증이면 '돌파'로 구제 (옵션)
        ma5_s = moving_average(closes, 5)
        ma10_s = moving_average(closes, 10)
        ma20_s = moving_average(closes, 20)
        recent_converged = False
        for k in range(max(0, len(closes) - vol_conv_lookback), len(closes)):
            if None in (ma5_s[k], ma10_s[k], ma20_s[k]):
                continue
            conv_k = (max(ma5_s[k], ma10_s[k], ma20_s[k]) - min(ma5_s[k], ma10_s[k], ma20_s[k])) / ma20_s[k] * 100
            if conv_k <= conv_max:
                recent_converged = True
                break
        vols = _volumes(candles)
        vol_ratio = vols[-1] / (sum(vols[-6:-1]) / 5) if len(vols) >= 6 else 0
        metrics["vol_ratio"] = round(vol_ratio, 2)
        if not (recent_converged and vol_ratio >= breakout_vol_mult):
            return PatternResult(False, f"수렴 안됨 (이격 {conv:.1f}%, 거래량 {vol_ratio:.1f}x)", metrics)
        # 구제 통과 — 돌파로 인정
        metrics["breakout"] = 1

    # 3. 종가 > 120일선 (이격 하한만 — 상한 없음: 추세주 안 거름)
    gap120 = (price - ma120) / ma120 * 100
    metrics["gap120_pct"] = round(gap120, 2)
    if gap120 < gap120_min:
        return PatternResult(False, f"120선 이격 부족 ({gap120:+.1f}%)", metrics)
    if gap120_max is not None and gap120 > gap120_max:
        return PatternResult(False, f"120선 이격 과대 ({gap120:+.1f}%)", metrics)

    # 4-a. 120선 우상향 — 박스권/하락추세 제외 (NAVER 횡보 차단, SK네트웍스 우상향 유지)
    if require_ma120_rising and ma120_series[-11] is not None:
        ma120_10ago = ma120_series[-11]
        slope = (ma120 - ma120_10ago) / ma120_10ago * 100
        metrics["ma120_slope_pct"] = round(slope, 2)
        if ma120 <= ma120_10ago:
            return PatternResult(False, f"120선 하락/횡보 ({slope:+.1f}%, 박스권)", metrics)

    # 4-b. 신고가 경신 (옵션, 기본 비활성 — 조정후 재상승 초입 놓침 방지)
    if require_new_high:
        highs = _highs(candles)
        hi = max(highs[-new_high_lookback:])
        metrics["from_high_pct"] = round((price - hi) / hi * 100, 2)
        if price < hi * (1 - new_high_tol):
            short = (hi - price) / hi * 100
            return PatternResult(False, f"신고가 미달 (60일고점 -{short:.1f}%, 박스권)", metrics)

    # 5. (옵션) 장기 정배열
    if require_long_align and not (ma60 > ma120):
        ma60_120 = (ma60 - ma120) / ma120 * 100
        return PatternResult(False, f"장기 정배열 아님 (60<120, {ma60_120:+.1f}%)", metrics)

    # 6. MACD — 약한 필수(하락 중이면 제외) + 상태 알림(metrics)
    macd_line, macd_sig, _hist = macd(closes)
    ml, ms = macd_line[-1], macd_sig[-1]
    macd_rising = ml is not None and macd_line[-2] is not None and ml > macd_line[-2]
    macd_above_zero = ml is not None and ml > 0
    macd_above_sig = ml is not None and ms is not None and ml > ms
    macd_gc = False
    macd_zero_cross = False
    for k in range(max(1, len(closes) - 5), len(closes)):
        a0, a1 = macd_line[k - 1], macd_line[k]
        b0, b1 = macd_sig[k - 1], macd_sig[k]
        if None not in (a0, a1, b0, b1) and a0 <= b0 and a1 > b1:
            macd_gc = True
        if a0 is not None and a1 is not None and a0 <= 0 < a1:
            macd_zero_cross = True
    metrics["macd_rising"] = 1 if macd_rising else 0
    metrics["macd_above_zero"] = 1 if macd_above_zero else 0
    metrics["macd_above_sig"] = 1 if macd_above_sig else 0
    metrics["macd_gc"] = 1 if macd_gc else 0
    metrics["macd_zero_cross"] = 1 if macd_zero_cross else 0

    # 약한 필수: MACD가 명백히 하락 중이면 제외 (사례 14/15가 상승 중)
    if reject_macd_falling and ml is not None and not macd_rising:
        return PatternResult(False, "MACD 하락 중 (추세 약화)", metrics)

    # MACD 상태 알림 텍스트 (강세 신호 강조)
    macd_signals = []
    if macd_zero_cross:
        macd_signals.append("0선돌파")
    if macd_gc:
        macd_signals.append("GC")
    if macd_above_zero:
        macd_signals.append("0선위")
    if macd_rising:
        macd_signals.append("상승")
    macd_txt = f" · MACD[{','.join(macd_signals)}]" if macd_signals else ""

    align_txt = " + 장기(60>120)" if require_long_align and ma60 > ma120 else ""
    nh_txt = " + 신고가" if require_new_high else ""
    rising_txt = " + 120선우상향" if require_ma120_rising else ""
    reasons = [
        f"{short_txt} (수렴 {conv:.1f}%)",
        f"120선 위 (+{gap120:.1f}%){rising_txt}{nh_txt}{align_txt}{macd_txt}",
    ]
    return PatternResult(True, " / ".join(reasons), metrics)


def is_trend_follow(
    candles: list[Candle],
    nh_lookback: int = 60, nh_tol: float = 0.03,
    div_lookback: int = 40, div_min_sep: int = 5, div_rsi_margin: float = 5.0,
    rollover_peak_min: float = 50.0, rollover_ratio: float = 0.55,
    surge_skip_day: float = 8.0, surge_skip_break: float = 5.0,
) -> PatternResult:
    """C 전략 — 대세 정배열 주도주 추세 추종 (늦게 발견해도 진입).

    검증(삼성전기/SK하이닉스/디엔디/LG이노텍 등): 이미 한참 오른 종목도
    어느 날 진입하든 60일선만 안 깨면 +수십~수백%. '늦었다'는 두려움이 틀림.

    진입:
      1. 일봉 정배열 5 > 10 > 20 > 60 (대세상승 진행 중)
      2. 종가가 최근 nh_lookback일 신고가의 -nh_tol 이내 (신고가 근처)
    손절(운영): 60일선 종가 2일연속 이탈 → 알림만, 매도는 본인 판단.

    끝물 경고(진입 막지 않음 — 플래그만): '많이 올랐다'(절대 상승률)는 대세주의
    정상 상태라 끝물이 아니다. 진짜 끝물 = '올랐는데 동력이 꺾였다'(모멘텀 소진):
      ① RSI 약세 다이버전스: 가격은 직전 스윙고점보다 높은데 RSI는 div_rsi_margin
         이상 낮음 → 상승 동력 소진.
      ② 이격 정점 통과: 60선 이격이 자기 최근(div_lookback일) 최대(>= rollover_peak_min)
         대비 rollover_ratio 이하로 후퇴 → 포물선 가속이 끝나고 꺾이기 시작.
    둘 중 하나라도 충족 시 ⚠️끝물주의. (절대 상승률 기준 폐기 — 전 종목 오탐 원인)
    """
    closes = _closes(candles)
    if len(closes) < 130:
        return PatternResult(False, "데이터 부족 (정배열 60일선 필요)")

    ma5 = moving_average(closes, 5)[-1]
    ma10 = moving_average(closes, 10)[-1]
    ma20 = moving_average(closes, 20)[-1]
    ma60_series = moving_average(closes, 60)
    ma60 = ma60_series[-1]
    if None in (ma5, ma10, ma20, ma60):
        return PatternResult(False, "이평선 계산 불가")

    price = closes[-1]
    metrics = {"price": round(price, 1), "ma60": round(ma60, 1)}

    # 1. 정배열 5>10>20>60
    if not (ma5 > ma10 > ma20 > ma60):
        return PatternResult(False, "정배열 아님 (5>10>20>60)", metrics)

    # 2. 신고가 근처
    highs = _highs(candles)
    hi = max(highs[-nh_lookback:])
    from_high = (price - hi) / hi * 100
    metrics["from_high_pct"] = round(from_high, 2)
    if price < hi * (1 - nh_tol):
        return PatternResult(False, f"신고가 미달 ({nh_lookback}일고점 {from_high:.1f}%)", metrics)

    gap60 = (price - ma60) / ma60 * 100
    metrics["gap60_pct"] = round(gap60, 2)
    rise120 = (price - closes[-121]) / closes[-121] * 100 if len(closes) >= 121 else 0.0
    metrics["rise120_pct"] = round(rise120, 1)

    # ── 끝물 경고 (모멘텀 소진 기반 — 진입은 막지 않음) ──────────────────────
    warns = []
    n = len(closes)

    # ① RSI 약세 다이버전스: 직전 스윙고점 대비 가격↑ 인데 RSI↓
    rsi_vals = rsi(closes, 14)
    lo, hi_idx = max(0, n - div_lookback), n - div_min_sep
    if hi_idx > lo and rsi_vals[-1] is not None:
        pj = max(range(lo, hi_idx), key=lambda i: closes[i])  # 직전 스윙 고점
        rp, rc = rsi_vals[pj], rsi_vals[-1]
        # 급등 신고가는 다이버전스 무시 (상한가/급등 돌파는 강세 — RSI(14) 평활지연 오판 방지)
        day_chg = (price - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 and closes[-2] else 0.0
        break_pct = (price - closes[pj]) / closes[pj] * 100 if closes[pj] else 0.0
        surged = day_chg >= surge_skip_day or break_pct >= surge_skip_break
        if rp is not None and price > closes[pj] and rc < rp - div_rsi_margin and not surged:
            warns.append(f"RSI다이버전스({rp:.0f}→{rc:.0f})")

    # ② 이격 정점 통과: 60선 이격이 자기 최근 최대 대비 후퇴 (가속 종료)
    gap_hist = [
        (closes[i] - ma60_series[i]) / ma60_series[i] * 100
        for i in range(max(0, n - div_lookback), n)
        if ma60_series[i] is not None
    ]
    if gap_hist:
        peak_gap = max(gap_hist)
        metrics["peak_gap60"] = round(peak_gap, 1)
        if peak_gap >= rollover_peak_min and gap60 <= peak_gap * rollover_ratio:
            warns.append(f"이격정점통과({peak_gap:.0f}%→{gap60:.0f}%)")

    metrics["endstage"] = 1 if warns else 0

    reason = f"대세 정배열 + 신고가 ({from_high:+.1f}%, 60선+{gap60:.0f}%)"
    if warns:
        reason += f" · ⚠️끝물주의({','.join(warns)})"
    return PatternResult(True, reason, metrics)


def is_downtrend_reversal(
    candles: list[Candle],
    downtrend_lookback: int = 20, use_ichimoku: bool = True, cloud_shift: int = 26,
) -> PatternResult:
    """D 전략 — 추세 반전 (하락추세→상승전환, 추세선 돌파의 객관적 대용).

    사용자 사례(NAVER 24/9/23, LG엔솔·LG전자 25/6~7, SK네트웍스·LG씨엔에스·에코프로) 역산.
    '고점-고점 하락추세선 돌파'는 긋는 위치가 주관적이라, 검증 결과 가장 잘 맞은
    **일목 구름(양운) 상향 돌파**를 주 신호로, 이평선·MACD로 노이즈를 거른다.
    (NAVER 0일·LG엔솔 +1·LG전자 +2 정확, SK넷·LGCNS +5일, 에코프로 +20일=가짜반등 필터)

    진입:
      1. 하락 이력(전환 초입): 20선 < 60선(중기 역배열) OR 최근 downtrend_lookback일 내
         구름 아래(use_ichimoku) / 5선<20선 경험 → '하락하던' 종목만 (이미 상승추세 제외)
      2. 5선 > 20선 (단기 정배열 회복)
      3. MACD 히스토그램 > 0 (양전환)
      4. (주신호) use_ichimoku=True: 종가가 일목 구름(26봉 시프트) 상단 위로 돌파.
         노이즈 우려 시 use_ichimoku=False → 종가 > 20일선으로 대체(이평선 기준).
    손절(운영): use_ichimoku면 종가 2일연속 구름 하단 이탈 / 아니면 20일선 2일이탈.
    """
    closes = _closes(candles)
    if len(closes) < 90:
        return PatternResult(False, "데이터 부족 (일목 구름 90봉 필요)")

    highs, lows = _highs(candles), _lows(candles)
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    if None in (ma5[-1], ma20[-1], ma60[-1]):
        return PatternResult(False, "이평선 계산 불가")
    _macd_line, _sig, hist = macd(closes)
    price = closes[-1]
    metrics = {"price": round(price, 1)}

    # 2. 단기 정배열 회복 (5 > 20)
    if not (ma5[-1] > ma20[-1]):
        return PatternResult(False, "5선 < 20선 (단기정배열 미회복)", metrics)

    # 3. MACD 양전환
    if hist[-1] is None or hist[-1] <= 0:
        return PatternResult(False, "MACD 음 (양전환 전)", metrics)

    # 4. 주신호: 일목 구름 상향 돌파 (26봉 시프트) — 또는 이평선 대체
    cloud_top = cloud_bot = None
    pos_label = "20일선 위"
    if use_ichimoku:
        cl = ichimoku(highs, lows, closes)
        j = len(closes) - 1 - cloud_shift
        if j >= 0 and cl["senkou_a"][j] is not None and cl["senkou_b"][j] is not None:
            cloud_top = max(cl["senkou_a"][j], cl["senkou_b"][j])
            cloud_bot = min(cl["senkou_a"][j], cl["senkou_b"][j])
            metrics["cloud_top"] = round(cloud_top, 1)
            metrics["cloud_bot"] = round(cloud_bot, 1)
            if price <= cloud_top:
                return PatternResult(False, f"일목구름 미돌파 (구름상단 {cloud_top:,.0f})", metrics)
            pos_label = "일목구름 위"
        elif price <= ma20[-1]:  # 구름 계산 불가 → 이평선 대체
            return PatternResult(False, "20일선 미돌파", metrics)
    elif price <= ma20[-1]:
        return PatternResult(False, "20일선 미돌파", metrics)

    # 1. 하락 이력 (전환 초입) — 이미 상승추세인 종목 제외
    had_downtrend = ma20[-1] < ma60[-1]  # 중기 역배열 = 초입
    if not had_downtrend:
        for k in range(2, min(downtrend_lookback, len(closes) - 1) + 1):
            below_cloud = (use_ichimoku and cloud_bot is not None
                           and closes[-k] < cloud_bot)
            short_dead = (ma5[-k] is not None and ma20[-k] is not None
                          and ma5[-k] < ma20[-k])
            if below_cloud or short_dead:
                had_downtrend = True
                break
    if not had_downtrend:
        return PatternResult(False, "하락전환 이력 없음 (이미 상승추세)", metrics)

    align = "20<60(초입)" if ma20[-1] < ma60[-1] else "20>60"
    rv = rsi(closes, 14)[-1]
    if rv is not None:
        metrics["rsi"] = round(rv, 1)
    reason = f"추세 반전 ({pos_label}, 5>20, MACD+, {align})"
    return PatternResult(True, reason, metrics)


def is_leader_oversold_bounce(
    candles: list[Candle],
    align_lookback: int = 40, oversold_within: int = 6,
    rsi_oversold: float = 43.0, support_ma: int = 60,
    deep_tol: float = 0.13, upper_tol: float = 0.18,
) -> PatternResult:
    """D 전략 — 주도주 과매도 반등 (C와 독립, 일시 충격 바닥 포착).

    C(신고가 추세추종)와 정반대 상태(신고가 -10%+·RSI 과매도)를 노린다.
    검증(SK하이닉스/삼성전자 3/31 바닥): 정배열 주도주가 시장충격으로 과매도까지
    밀렸다가 장기추세선(120선) 위에서 지지받고 반등 확인되는 첫날 진입.

    진입:
      1. 장기추세 생존: 종가 > 120선 (대세 안 깨짐)
      2. 주도주 이력: 최근 align_lookback일 내 정배열(5>10>20>60) 경험
      3. 과매도 후 반등 확인: 최근 oversold_within일 내 RSI <= rsi_oversold 찍고
         → 당일 RSI 상승전환 + 당일 양봉(종가>시가) (바닥 확인 캔들)
      4. 지지선권: 종가가 support_ma선의 -deep_tol ~ +upper_tol 범위 (깊은 눌림 지지)
    손절(운영): 반등 저점(최근 oversold_within일 최저 저가) 종가 이탈 또는 120선 이탈.
    """
    closes = _closes(candles)
    if len(closes) < 130:
        return PatternResult(False, "데이터 부족 (120선 필요)")

    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    ma120 = moving_average(closes, 120)
    rsi_v = rsi(closes, 14)
    if None in (ma5[-1], ma10[-1], ma20[-1], ma60[-1], ma120[-1], rsi_v[-1]):
        return PatternResult(False, "지표 계산 불가")

    price = closes[-1]
    metrics = {"price": round(price, 1), "ma120": round(ma120[-1], 1),
               "rsi": round(rsi_v[-1], 1)}

    # 1. 장기추세 생존 (종가 > 120선)
    if price <= ma120[-1]:
        gap120 = (price - ma120[-1]) / ma120[-1] * 100
        return PatternResult(False, f"120선 이탈 (대세붕괴, {gap120:+.0f}%)", metrics)

    # 2. 주도주 이력 (최근 align_lookback일 내 정배열 경험)
    had_align = False
    for i in range(max(0, len(closes) - align_lookback), len(closes)):
        if None in (ma5[i], ma10[i], ma20[i], ma60[i]):
            continue
        if ma5[i] > ma10[i] > ma20[i] > ma60[i]:
            had_align = True
            break
    if not had_align:
        return PatternResult(False, f"주도주 아님 (최근 {align_lookback}일 정배열 이력 없음)", metrics)

    # 3. 과매도 후 반등 확인
    recent_rsi = [r for r in rsi_v[-oversold_within:] if r is not None]
    min_rsi = min(recent_rsi) if recent_rsi else 100.0
    metrics["min_rsi"] = round(min_rsi, 1)
    if min_rsi > rsi_oversold:
        return PatternResult(False, f"과매도 미달 (최근RSI저점 {min_rsi:.0f} > {rsi_oversold:.0f})", metrics)
    rsi_turn = rsi_v[-1] > rsi_v[-2] if rsi_v[-2] is not None else False
    bullish = candles[-1].close > candles[-1].open
    if not (rsi_turn and bullish):
        return PatternResult(False, f"반등 확인 미흡 (RSI전환{'O' if rsi_turn else 'X'}/양봉{'O' if bullish else 'X'})", metrics)

    # 4. 지지선권 (support_ma선 -deep_tol ~ +upper_tol)
    sup = moving_average(closes, support_ma)[-1]
    gap_sup = (price - sup) / sup * 100
    metrics[f"gap{support_ma}_pct"] = round(gap_sup, 2)
    if not (-deep_tol * 100 <= gap_sup <= upper_tol * 100):
        return PatternResult(False, f"{support_ma}선권 이탈 ({gap_sup:+.0f}%)", metrics)

    reason = (f"주도주 과매도 반등 (RSI {min_rsi:.0f}→{rsi_v[-1]:.0f} 전환, "
              f"{support_ma}선{gap_sup:+.0f}%, 120선 위)")
    return PatternResult(True, reason, metrics)


def diagnose_holding(
    candles: list[Candle], stop_streak: int = 2,
    pullback_gap_max: float = 0.0,
) -> PatternResult:
    """보유종목 상태 진단 — A/B/C 전략 종합 (홀딩/손절/추가매수).

    내가 어떤 전략으로 샀는지 몰라도, 현재 차트 상태로 종합 판정한다.
    우선순위(위험 우선): 추세붕괴 > 60선손절 > 20선단기손절 > 추가매수 > 홀딩.

    metrics["state"] 코드:
      "BREAKDOWN" 🔴 120선 이탈 (대세 붕괴 — 손절 검토)
      "STOP60"    🔴 60선 stop_streak일 연속 이탈 (C 추세 손절)
      "STOP20"    ⚠️ 20선 stop_streak일 연속 이탈 (A/B 단기 손절)
      "ADD"       🟢 정배열 유지 + 20선 위 5선 아래 눌림 (B 추가매수 후보)
      "HOLD"      ✅ 정배열 유지 (추세 양호 — 보유 지속, 끝물이면 병기)
      "NEUTRAL"   ➖ 정배열 아니나 손절선 위 (관망)
    """
    closes = _closes(candles)
    if len(closes) < 25:
        return PatternResult(False, "데이터 부족", {"state": "UNKNOWN"})

    ma5 = moving_average(closes, 5)[-1]
    ma20_s = moving_average(closes, 20)
    ma60_s = moving_average(closes, 60)
    ma120_s = moving_average(closes, 120)
    ma20 = ma20_s[-1]
    ma60 = ma60_s[-1]
    ma120 = ma120_s[-1]
    price = closes[-1]
    metrics: dict[str, float] = {"price": round(price, 1)}
    if ma20:
        metrics["gap20_pct"] = round((price - ma20) / ma20 * 100, 1)
    if ma60:
        metrics["gap60_pct"] = round((price - ma60) / ma60 * 100, 1)

    def _below_streak(ma_series: list[float | None], n: int) -> bool:
        if len(closes) < n:
            return False
        for k in range(1, n + 1):
            m = ma_series[-k]
            if m is None or closes[-k] >= m:
                return False
        return True

    # 1. 120선 이탈 → 추세 붕괴
    if ma120 is not None and price < ma120:
        metrics["state"] = "BREAKDOWN"
        return PatternResult(False, f"120선 이탈 (대세 붕괴, {(price-ma120)/ma120*100:+.1f}%)", metrics)

    # 2. 60선 2일연속 이탈 → C 추세 손절
    if ma60 is not None and _below_streak(ma60_s, stop_streak):
        metrics["state"] = "STOP60"
        return PatternResult(False, f"60선 {stop_streak}일연속 이탈 (추세 손절)", metrics)

    # 3. 20선 2일연속 이탈 → A/B 단기 손절
    if ma20 is not None and _below_streak(ma20_s, stop_streak):
        metrics["state"] = "STOP20"
        return PatternResult(False, f"20선 {stop_streak}일연속 이탈 (단기 손절)", metrics)

    aligned = None not in (ma5, ma20, ma60) and ma5 > ma20 > ma60

    # 4. 정배열 + 20선 위 + 5선 아래 눌림 → B 추가매수 후보
    if aligned and ma5 is not None and price > ma20 and price <= ma5 * (1 + pullback_gap_max):
        metrics["state"] = "ADD"
        return PatternResult(True, f"20선 위 단기 눌림 (추가매수 후보, 20선{metrics.get('gap20_pct', 0):+.0f}%)", metrics)

    # 5. 정배열 유지 → 홀딩 (끝물이면 병기)
    if aligned:
        tf = is_trend_follow(candles)
        endstage = tf.matched and tf.metrics.get("endstage")
        metrics["state"] = "HOLD"
        if endstage:
            metrics["endstage"] = 1
            return PatternResult(True, f"추세 양호 · 홀딩 ({tf.reason.split('·')[-1].strip()})", metrics)
        return PatternResult(True, f"추세 양호 · 홀딩 (정배열, 20선{metrics.get('gap20_pct', 0):+.0f}%)", metrics)

    # 6. 그 외 (손절선 위지만 정배열 아님) → 관망
    metrics["state"] = "NEUTRAL"
    return PatternResult(True, f"관망 (정배열 아님, 손절선 위)", metrics)


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
