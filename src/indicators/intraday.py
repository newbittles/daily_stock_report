"""장중 분봉 흐름 분석 — 1분봉 리샘플 + 당일 궤적(급락 후 반등 등) 판정 (사용자 #473/#474).

domain 순수성(CLAUDE.md §5): stdlib + dataclass만, 네트워크/DB/SDK import 금지.
입력은 값(분봉 OHLCV·전일종가), 출력은 값(흐름 dataclass·한국어 문구).

핵심: "장초 -10%까지 밀렸다 양봉 전환, 현재 -3%"처럼 **저점/고점 대비 현재 위치**를
수치로 잡아 추세 라벨(V반등·약세지속·고점후하락·강세·보합)과 한국어 한 줄을 만든다.
추세 판정은 결정론(환각 방지) — AI는 이 수치를 받아 종합 코멘트만 한다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Bar:
    """분봉 1개 — hhmm은 봉 시작 시각 'HHMM'(예: '0915'). 과거→현재 정렬 전제."""
    hhmm: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class IntradayFlow:
    """당일 분봉 궤적 요약 — 전부 전일종가 대비 %. 추세 라벨 + 회복/낙폭 폭(%p)."""
    cur_pct: float        # 현재가(마지막 봉 종가) 전일종가 대비 %
    low_pct: float        # 당일 저점 전일종가 대비 %
    high_pct: float       # 당일 고점 전일종가 대비 %
    recovery_pp: float    # 저점 대비 현재 회복 폭(%p) = cur_pct - low_pct (≥0)
    drawdown_pp: float    # 고점 대비 현재 낙폭 폭(%p) = high_pct - cur_pct (≥0)
    low_hhmm: str         # 저점이 찍힌 봉 시각
    high_hhmm: str        # 고점이 찍힌 봉 시각
    last_dir: str         # 마지막 봉 'up'(양봉)/'down'(음봉)/'flat'
    trend: str            # 최근 3봉 종가 추세 'up'/'down'/'flat'
    shape: str            # V_REBOUND / WEAK / PEAK_FADE / STRONG / FLAT
    n_bars: int


def _hhmm_to_min(hhmm: str) -> int:
    """'HHMM' → 자정 기준 분. 파싱 실패 시 -1."""
    s = str(hhmm or "").strip()
    if len(s) < 4 or not s[:4].isdigit():
        return -1
    return int(s[:2]) * 60 + int(s[2:4])


def _min_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}{m % 60:02d}"


def resample(bars_1m: list[Bar], minutes: int) -> list[Bar]:
    """1분봉(과거→현재) → minutes분봉. 버킷=자정 기준 floor(분/minutes).

    open=버킷 첫 봉 시가, high=max, low=min, close=마지막 봉 종가, volume=합.
    미완성 버킷(예: 11:40 시점의 11:00봉)도 현재까지 값으로 포함. 빈 입력→[].
    """
    if minutes <= 0 or not bars_1m:
        return list(bars_1m)
    buckets: dict[int, list[Bar]] = {}
    order: list[int] = []
    for b in bars_1m:
        m = _hhmm_to_min(b.hhmm)
        if m < 0:
            continue
        key = (m // minutes) * minutes
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(b)
    out: list[Bar] = []
    for key in sorted(order):
        grp = buckets[key]
        out.append(Bar(
            hhmm=_min_to_hhmm(key),
            open=grp[0].open,
            high=max(x.high for x in grp),
            low=min(x.low for x in grp),
            close=grp[-1].close,
            volume=sum(x.volume for x in grp),
        ))
    return out


def _trend(closes: list[float]) -> str:
    """최근 3봉 종가 방향. 마지막이 직전대비 ±0.3%↑ up/down, 아니면 flat."""
    if len(closes) < 2:
        return "flat"
    recent = closes[-3:]
    first, last = recent[0], recent[-1]
    if first <= 0:
        return "flat"
    chg = (last / first - 1) * 100
    if chg >= 0.3:
        return "up"
    if chg <= -0.3:
        return "down"
    return "flat"


def analyze_flow(bars: list[Bar], prev_close: float) -> IntradayFlow | None:
    """리샘플된 분봉 + 전일종가 → 당일 궤적 요약. 데이터/전일종가 없으면 None.

    shape 판정(우선순위):
      STRONG    현재 고점 근처 + 양(+)강세 (장중 꾸준히 강함)
      V_REBOUND 저점이 의미있게 깊고(≤-2%) 저점 대비 충분히 회복(≥1.5%p) + 저점이 과거
      PEAK_FADE 고점이 의미있게 높고(≥2%) 고점 대비 충분히 밀림(≥1.5%p) + 고점이 과거
      WEAK      저점 근처에서 약세(현재 음 + 회복 1.5%p 미만)
      FLAT      그 외(보합권)
    """
    if not bars or prev_close <= 0:
        return None
    closes = [b.close for b in bars]
    cur = closes[-1]
    low_bar = min(bars, key=lambda b: b.low)
    high_bar = max(bars, key=lambda b: b.high)

    def _pct(p: float) -> float:
        return (p / prev_close - 1) * 100

    cur_pct = _pct(cur)
    low_pct = _pct(low_bar.low)
    high_pct = _pct(high_bar.high)
    recovery_pp = round(cur_pct - low_pct, 2)
    drawdown_pp = round(high_pct - cur_pct, 2)

    last = bars[-1]
    last_dir = ("up" if last.close > last.open else
                "down" if last.close < last.open else "flat")
    trend = _trend(closes)

    low_idx = bars.index(low_bar)
    high_idx = bars.index(high_bar)
    n = len(bars)
    low_is_past = low_idx <= n - 2     # 저점이 마지막 봉이 아님(이미 반등 국면)
    high_is_past = high_idx <= n - 2

    if high_pct >= 1.0 and drawdown_pp <= 1.0 and cur_pct >= 1.5:
        shape = "STRONG"
    elif low_pct <= -2.0 and recovery_pp >= 1.5 and low_is_past:
        shape = "V_REBOUND"
    elif high_pct >= 2.0 and drawdown_pp >= 1.5 and high_is_past:
        shape = "PEAK_FADE"
    elif cur_pct < 0 and recovery_pp < 1.5:
        shape = "WEAK"
    else:
        shape = "FLAT"

    return IntradayFlow(
        cur_pct=round(cur_pct, 2), low_pct=round(low_pct, 2), high_pct=round(high_pct, 2),
        recovery_pp=recovery_pp, drawdown_pp=drawdown_pp,
        low_hhmm=low_bar.hhmm, high_hhmm=high_bar.hhmm,
        last_dir=last_dir, trend=trend, shape=shape, n_bars=n,
    )


def _hm(hhmm: str) -> str:
    """'0915' → '09:15' (표시용). 형식 이상하면 원본."""
    s = str(hhmm or "")
    return f"{s[:2]}:{s[2:4]}" if len(s) >= 4 and s[:4].isdigit() else s


def describe_flow(flow: IntradayFlow | None) -> str:
    """흐름 → 한국어 한 줄 추세 문구(결정론). 사용자 예시 형식과 동일. None→''."""
    if flow is None:
        return ""
    cur = f"{flow.cur_pct:+.1f}%"
    if flow.shape == "V_REBOUND":
        tail = " 양봉" if flow.last_dir == "up" else ""
        return (f"장중 {flow.low_pct:.1f}%({_hm(flow.low_hhmm)})까지 밀렸다 반등{tail}, "
                f"현재 {cur}(저점대비 +{flow.recovery_pp:.1f}%p)")
    if flow.shape == "PEAK_FADE":
        return (f"장중 +{flow.high_pct:.1f}%({_hm(flow.high_hhmm)})까지 올랐다 밀림, "
                f"현재 {cur}(고점대비 -{flow.drawdown_pp:.1f}%p)")
    if flow.shape == "STRONG":
        return f"장중 꾸준한 강세, 현재 {cur}(고점 +{flow.high_pct:.1f}%)"
    if flow.shape == "WEAK":
        return f"장중 약세 지속, 현재 {cur}(저점 {flow.low_pct:.1f}%)"
    return f"장중 보합권, 현재 {cur}"
