"""장중 분봉 흐름 분석 (#473/#474) — 리샘플·궤적 판정·한국어 문구 (순수·결정론)."""
from __future__ import annotations

from src.indicators.intraday import (
    Bar, analyze_flow, describe_flow, resample,
)


def _bar(hhmm: str, o: float, h: float, low: float, c: float, v: float = 1.0) -> Bar:
    return Bar(hhmm=hhmm, open=o, high=h, low=low, close=c, volume=v)


# ─── resample ────────────────────────────────────────────────────────────────


def test_resample_15min_aggregates_buckets() -> None:
    bars = [
        _bar("0900", 100, 105, 99, 102, 10),
        _bar("0905", 102, 108, 101, 107, 20),
        _bar("0914", 107, 110, 106, 109, 30),  # 09:00~09:14 → 09:00 버킷
        _bar("0915", 109, 112, 108, 111, 5),   # 09:15 버킷
    ]
    out = resample(bars, 15)
    assert len(out) == 2
    b0 = out[0]
    assert b0.hhmm == "0900"
    assert b0.open == 100          # 첫 봉 시가
    assert b0.high == 110          # 버킷 내 최고
    assert b0.low == 99            # 버킷 내 최저
    assert b0.close == 109         # 마지막 봉 종가
    assert b0.volume == 60         # 합
    assert out[1].hhmm == "0915"


def test_resample_60min_bucket_boundaries() -> None:
    bars = [_bar("0900", 100, 100, 100, 100), _bar("0959", 100, 101, 99, 100),
            _bar("1000", 100, 100, 100, 100), _bar("1130", 100, 100, 100, 100)]
    out = resample(bars, 60)
    assert [b.hhmm for b in out] == ["0900", "1000", "1100"]  # 09·10·11시 버킷


def test_resample_empty() -> None:
    assert resample([], 15) == []


# ─── analyze_flow: shape 판정 ────────────────────────────────────────────────


def test_v_rebound_matches_user_example() -> None:
    """하이닉스 예시: 장초 -10% 급락 후 양봉 전환, 현재 -3%."""
    prev = 100.0
    bars = [
        _bar("0900", 98, 98, 90, 91),   # 저점 90 = -10%
        _bar("0930", 91, 93, 90, 92),
        _bar("1100", 95, 97, 94, 97),   # 마지막 양봉, 종가 97 = -3%
    ]
    f = analyze_flow(bars, prev)
    assert f is not None
    assert f.shape == "V_REBOUND"
    assert f.low_pct == -10.0
    assert f.cur_pct == -3.0
    assert f.recovery_pp == 7.0
    assert f.last_dir == "up"
    assert f.low_hhmm == "0900"
    txt = describe_flow(f)
    assert "-10.0%" in txt and "반등" in txt and "-3.0%" in txt and "+7.0%p" in txt


def test_weak_persists_low() -> None:
    prev = 100.0
    bars = [_bar("0900", 99, 99, 96, 97), _bar("0930", 97, 97, 94, 95),
            _bar("1100", 95, 95, 93, 94)]  # 저점 근처 계속, 현재 -6%
    f = analyze_flow(bars, prev)
    assert f.shape == "WEAK"
    assert f.cur_pct == -6.0
    assert "약세 지속" in describe_flow(f)


def test_peak_fade() -> None:
    prev = 100.0
    bars = [_bar("0900", 101, 108, 101, 107),  # 고점 108 = +8%
            _bar("0930", 107, 108, 104, 105),
            _bar("1100", 105, 105, 102, 103)]  # 현재 +3%, 고점대비 -5%p
    f = analyze_flow(bars, prev)
    assert f.shape == "PEAK_FADE"
    assert f.high_pct == 8.0
    assert f.drawdown_pp == 5.0
    assert "올랐다 밀림" in describe_flow(f)


def test_strong_uptrend() -> None:
    prev = 100.0
    bars = [_bar("0900", 100, 101, 100, 101), _bar("0930", 101, 103, 101, 103),
            _bar("1100", 103, 106, 103, 106)]  # 현재 +6%, 고점 근처
    f = analyze_flow(bars, prev)
    assert f.shape == "STRONG"
    assert "강세" in describe_flow(f)


def test_flat_range() -> None:
    prev = 100.0
    bars = [_bar("0900", 100, 100.5, 99.5, 100), _bar("0930", 100, 100.5, 99.6, 100.2)]
    f = analyze_flow(bars, prev)
    assert f.shape == "FLAT"
    assert "보합" in describe_flow(f)


def test_analyze_none_on_empty_or_no_prevclose() -> None:
    assert analyze_flow([], 100.0) is None
    assert analyze_flow([_bar("0900", 1, 1, 1, 1)], 0) is None
    assert describe_flow(None) == ""


def test_last_dir_down_no_yangbong_tail() -> None:
    """반등이지만 마지막 봉이 음봉이면 '양봉' 표기 안 함."""
    prev = 100.0
    bars = [_bar("0900", 97, 97, 90, 91), _bar("1100", 98, 98, 96, 96.5)]  # 마지막 음봉
    f = analyze_flow(bars, prev)
    assert f.shape == "V_REBOUND"
    assert f.last_dir == "down"
    assert "양봉" not in describe_flow(f)
