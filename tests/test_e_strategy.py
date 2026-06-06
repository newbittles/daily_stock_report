"""E전략(과매도 반등 후보) — 일봉 순수판정 + 4H RSI + 텔레그램 섹션(사용자 2026-06-05).

E = 최근 주도주(신고가 경신)였다가 일봉 RSI≤30 (+ 4시간봉 RSI≤30은 pipeline 결합).
"""
from __future__ import annotations

from datetime import datetime

from src.datasource.base import Candle
from src.datasource.kr_4h import judge_4h_rsi_oversold
from src.market_report.models import MarketSnapshot
from src.market_report.telegram_notify import _format_e_picks
from src.patterns.core import oversold_leader


def _c(close: float, high: float | None = None, open_: float | None = None, vol: int = 1000) -> Candle:
    o = open_ if open_ is not None else close
    h = high if high is not None else max(close, o)
    return Candle(date="20260101", open=o, high=h, low=min(close, o), close=close, volume=vol)


def _capitulation(cap_vol: int = 5000, reb_vol: int = 4500, rebound: bool = True) -> list[Candle]:
    """투매 바닥 시나리오: 40일 상승 → 14일 급락(음봉) → 투매 음봉(대량) → 반등 양봉(대량)."""
    c = [_c(90 + i * 0.3, vol=1000) for i in range(40)]
    px = 102.0
    for _ in range(14):
        px *= 0.96
        c.append(_c(px, open_=px / 0.96, vol=1500))      # 음봉 급락
    cap = px * 0.93
    c.append(_c(cap, open_=px, vol=cap_vol))             # 투매(깊은 음봉·대량)
    if rebound:
        c.append(_c(cap * 1.12, open_=cap, vol=reb_vol))  # 반등 양봉(대량)
    else:
        c.append(_c(cap * 0.97, open_=cap, vol=reb_vol))  # 추가 음봉(반등 미확인)
    return c


def test_oversold_capitulation_true() -> None:
    """투매 바닥(RSI≤30 + 50선이격≤-12% + 거래량≥2x + 반등 양봉) → E 매칭."""
    r = oversold_leader(_capitulation())
    assert r.matched is True
    assert r.metrics["rsi"] <= 30 and r.metrics["ma50_gap"] <= -12 and r.metrics["vol_x"] >= 2


def test_oversold_false_no_capitulation_volume() -> None:
    """거래량 폭증(투매) 없으면 미매칭 — 가짜 바닥 회피."""
    assert oversold_leader(_capitulation(cap_vol=1200, reb_vol=1200)).matched is False


def test_oversold_false_no_rebound_candle() -> None:
    """반등 양봉(턴) 없으면 미매칭 — 떨어지는 칼날 회피."""
    assert oversold_leader(_capitulation(rebound=False)).matched is False


def test_oversold_false_when_rsi_high() -> None:
    """계속 상승(RSI 높음)이면 과매도 아님 → E 미매칭."""
    assert oversold_leader([_c(100 + i, vol=1000) for i in range(140)]).matched is False


def test_judge_4h_rsi_oversold() -> None:
    falling = [100.0 - i for i in range(30)]   # 지속 하락 → RSI 낮음
    rising = [100.0 + i for i in range(30)]
    assert judge_4h_rsi_oversold(falling) is True
    assert judge_4h_rsi_oversold(rising) is False
    assert judge_4h_rsi_oversold([1, 2, 3]) is None


def test_format_e_picks_kr_and_us() -> None:
    snap = MarketSnapshot(mode="post_close", generated_at=datetime(2026, 6, 5, 16, 30))
    snap.e_picks = [{"ticker": "009150", "name": "삼성전기", "price": 120000,
                     "change_pct": 1.2, "rsi": 28, "reason": "과매도 반등후보"}]
    out = "\n".join(_format_e_picks(snap))
    assert "E 투매 바닥 반등" in out and "삼성전기" in out and "RSI28" in out

    snap.e_picks = [{"symbol": "MU", "name": "마이크론", "price": 95.5,
                     "change_pct": -0.5, "rsi": 25, "reason": "x"}]
    out = "\n".join(_format_e_picks(snap))
    assert "마이크론(MU)" in out and "$95.50" in out


def test_is_surge_start() -> None:
    """급등 초입 = 20일 신고가 돌파 + 거래량 급증 + 당일 강세 + 과이격 전."""
    from src.datasource.base import Candle
    from src.patterns.core import is_surge_start

    def _c(close, vol, high=None, openp=None):
        return Candle(date="1", open=openp or close, high=high or close, low=close, close=close, volume=vol)

    base = [_c(100.0, 1000) for _ in range(40)]               # 40일 횡보(저거래량)
    surge = base + [_c(108.0, 3000, high=109)]                # 돌파일: +8%, 거래량 3배, 신고가
    assert is_surge_start(surge).matched is True
    # 돌파 아님(신고가 미달)
    assert is_surge_start(base + [_c(99.0, 3000)]).matched is False
    # 거래량 부족
    assert is_surge_start(base + [_c(108.0, 1100, high=109)]).matched is False


def test_format_surge_picks() -> None:
    from src.market_report.models import MarketSnapshot
    from src.market_report.telegram_notify import _format_surge_picks
    snap = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 5, 14, 40))
    snap.surge_picks = [{"ticker": "064400", "name": "LG씨엔에스", "price": 94600,
                         "change_pct": 14.1, "reason": "급등초입"}]
    out = "\n".join(_format_surge_picks(snap))
    assert "급등 초입" in out and "LG씨엔에스" in out and "+14.1%" in out


def test_pick_detail_line() -> None:
    """E/급등초입 보조줄 — 시총·거래량·거래대금·테마·서학개미(US) 표기(사용자 2026-06-05)."""
    from src.market_report.telegram_notify import _pick_detail_line
    kr = _pick_detail_line({"marcap_str": "420조", "volume": 12500000, "turnover_str": "8750억", "theme": "반도체"})
    assert "시총 420조" in kr and "거래량 1250만주" in kr and "거래대금 8750억" in kr and "테마 반도체" in kr
    assert "한국인" not in kr  # KR은 서학개미 없음
    us = _pick_detail_line({"marcap_str": "3000조", "theme": "반도체", "kr_netbuy_prev_eok": 120})
    assert "🇰🇷한국인 전일 +120억" in us


def test_tag_market_bottom_with_fear_greed() -> None:
    """F&G≤25(극단공포)면 지수 RSI가 높아도 시장 동반 바닥(강)으로 인정(사용자 #331)."""
    from src.market_report.pipeline import _tag_market_bottom
    p1 = [{"symbol": "X"}]
    _tag_market_bottom(p1, market_rsi=50.0, fg_score=20.0)  # RSI 정상이나 F&G 극단공포
    assert p1[0]["market_bottom"] is True and p1[0]["fg_score"] == 20
    p2 = [{"symbol": "Y"}]
    _tag_market_bottom(p2, market_rsi=50.0, fg_score=40.0)  # 둘 다 정상
    assert p2[0]["market_bottom"] is False
    p3 = [{"symbol": "Z"}]
    _tag_market_bottom(p3, market_rsi=30.0, fg_score=40.0)  # 지수 RSI만 바닥
    assert p3[0]["market_bottom"] is True
