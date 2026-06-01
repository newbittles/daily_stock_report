"""순수 지표 계산 함수 — numpy/pandas 없이 표준 라이브러리만 사용.

모든 함수는 list[float] 종가(또는 OHLC)를 받아 list[float | None]를 반환.
계산 불가 구간(데이터 부족)은 None으로 채워 입력과 길이를 맞춘다.

외부 의존 금지 (CLAUDE.md §5 domain 순수성).
"""
from __future__ import annotations


def moving_average(values: list[float], period: int) -> list[float | None]:
    """단순이동평균(SMA). 길이 < period 구간은 None."""
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = []
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        result.append(acc / period if i >= period - 1 else None)
    return result


def average_true_range(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    """ATR(평균진폭) — 최근 period일 True Range 평균. 변동성 기반 손절폭 산정용.

    TR = max(고-저, |고-전일종가|, |저-전일종가|). 데이터 부족 시 None.
    급등주는 ATR이 커 손절폭이 넓고, 안정주는 좁다 → 종목별 적정 손절 자동.
    """
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    recent = trs[-period:]
    return sum(recent) / len(recent) if recent else None


def ema(values: list[float], period: int) -> list[float | None]:
    """지수이동평균(EMA). 첫 유효값은 SMA로 시드."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return [None] * len(values)
    result: list[float | None] = [None] * len(values)
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        result[i] = prev
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """RSI (Wilder smoothing). 0~100."""
    n = len(values)
    result: list[float | None] = [None] * n
    if n <= period:
        return result

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    avg_gain = gains / period
    avg_loss = losses / period

    def _rsi_val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    result[period] = _rsi_val(avg_gain, avg_loss)
    for i in range(period + 1, n):
        ch = values[i] - values[i - 1]
        gain = max(ch, 0)
        loss = max(-ch, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = _rsi_val(avg_gain, avg_loss)
    return result


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD. (macd_line, signal_line, histogram) 반환."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    # signal = macd_line의 EMA (None 구간 제외하고 계산)
    valid = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: list[float | None] = [None] * len(values)
    if len(valid) >= signal:
        seq = [v for _, v in valid]
        sig_ema = ema(seq, signal)
        for (orig_idx, _), s in zip(valid, sig_ema):
            signal_line[orig_idx] = s
    hist: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def bollinger_bands(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """볼린저밴드. (upper, mid, lower) 반환."""
    n = len(values)
    upper: list[float | None] = [None] * n
    mid: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        mid[i] = m
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return upper, mid, lower


def cci(
    highs: list[float], lows: list[float], closes: list[float], period: int = 20
) -> list[float | None]:
    """CCI (Commodity Channel Index)."""
    n = len(closes)
    result: list[float | None] = [None] * n
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]
    for i in range(period - 1, n):
        window = tp[i - period + 1 : i + 1]
        sma_tp = sum(window) / period
        mean_dev = sum(abs(x - sma_tp) for x in window) / period
        if mean_dev == 0:
            result[i] = 0.0
        else:
            result[i] = (tp[i] - sma_tp) / (0.015 * mean_dev)
    return result


def ichimoku(
    highs: list[float], lows: list[float], closes: list[float],
    tenkan_period: int = 9, kijun_period: int = 26, senkou_b_period: int = 52,
) -> dict[str, list[float | None]]:
    """일목균형표. 전환선/기준선/선행스팬A/선행스팬B/후행스팬.

    선행스팬은 kijun_period만큼 미래로 시프트하지 않고 '현재 정렬'로 반환
    (스크리너 판정 편의 — 구름 위/아래 판단에 같은 인덱스 비교).
    """
    n = len(closes)

    def _mid(period: int) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(period - 1, n):
            hh = max(highs[i - period + 1 : i + 1])
            ll = min(lows[i - period + 1 : i + 1])
            out[i] = (hh + ll) / 2
        return out

    tenkan = _mid(tenkan_period)
    kijun = _mid(kijun_period)
    senkou_a: list[float | None] = [
        ((t + k) / 2) if (t is not None and k is not None) else None
        for t, k in zip(tenkan, kijun)
    ]
    senkou_b = _mid(senkou_b_period)
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
    }
