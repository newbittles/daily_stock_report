"""과열(볼린저밴드) 판정 + Top3 강등 — 오프라인 검증(사용자 2026-06-05).

- 4시간봉 과열 = BB(20,2) 상단에서 음봉(거부) 순수 판정.
- Top3: 과열 종목은 강등(완전 제외 아님, 표시·이유 유지).
"""
from __future__ import annotations

from src.datasource.kr_4h import judge_4h_overheat
from src.market_report.top3 import select_top3


# 변동성 있는 20봉 종가(ma≈100, pstdev≈5 → BB상단≈110). 과거봉 o/h는 BB에 미사용.
_BASE = [95.0, 105.0] * 10


def test_judge_4h_overheat_breakout_bullish_true() -> None:
    """종가 > BB상단 = 돌파 → 양봉이어도 과열 True (삼성화재·신세계 케이스)."""
    closes = _BASE + [115.0]              # 종가 115 > BB상단(~110) = 돌파
    opens = [100.0] * 20 + [111.0]        # 양봉(115>111)
    highs = [100.0] * 20 + [116.0]
    assert judge_4h_overheat(opens, highs, closes) is True


def test_judge_4h_overheat_rejection_bearish_true() -> None:
    """상단 터치 + 음봉(close<open), 종가는 상단 아래여도 → 거부 과열 True."""
    closes = _BASE + [108.0]              # 종가 108 < BB상단(~110) (돌파 아님)
    opens = [100.0] * 20 + [114.0]
    highs = [100.0] * 20 + [115.0]        # high 115 ≥ 상단, 음봉(108<114)
    assert judge_4h_overheat(opens, highs, closes) is True


def test_judge_4h_overheat_below_upper_false() -> None:
    """상단 미도달이면 과열 아님(돌파X·거부X)."""
    closes = _BASE + [100.0]
    opens = [100.0] * 20 + [101.0]
    highs = [100.0] * 20 + [102.0]        # high 102 < BB상단(~110)
    assert judge_4h_overheat(opens, highs, closes) is False


def test_judge_4h_overheat_insufficient_data() -> None:
    assert judge_4h_overheat([1, 2], [1, 2], [1, 2]) is None


def _pick(ticker: str, name: str, **extra) -> dict:
    return {"ticker": ticker, "name": name, "price": 1000.0, "strategy": "C. 대세 정배열 추세추종",
            "change_pct": 1.0, "gap20": 5.0, "_liq": 5.0, "_nh": 0.0, **extra}


def test_b_reason_shows_high_drawdown() -> None:
    """B 시그널 설명란(Top3)에 고점대비 낙폭 표시(사용자 2026-06-05)."""
    picks = [{"ticker": "X", "name": "비종목", "price": 1000.0, "strategy": "B. 주도주 20일선 눌림목",
              "change_pct": 1.0, "_nh": -18.0, "high_dd": -21.7, "_liq": 5.0, "gap20": 3.0}]
    out = select_top3(picks)
    assert "고점대비 -21.7%" in out[0]["reason"]
    assert out[0]["high_dd"] == -21.7


def test_top3_demotes_daily_overheat() -> None:
    """일봉 과열(overheat) 종목은 동급 정상 종목보다 아래로 강등(완전 제외는 아님)."""
    picks = [_pick("AAA", "정상"), _pick("BBB", "과열", overheat=True)]
    out = select_top3(picks)
    tickers = [o["ticker"] for o in out]
    assert tickers[0] == "AAA"          # 정상이 위
    assert "BBB" in tickers             # 제외는 아님(표시 유지)
    bbb = next(o for o in out if o["ticker"] == "BBB")
    assert bbb["overheat"] is True and "과열" in bbb["reason"]


def test_gave_back_recent_gain() -> None:
    """최근 3일내 최근 10일 상승분 대부분 반납 = True(B 제외용, 삼성에스디에스형)."""
    from src.datasource.base import Candle
    from src.patterns.core import gave_back_recent_gain

    def _c(v: float) -> Candle:
        return Candle(date="1", open=v, high=v, low=v, close=v, volume=1)

    # 10일전 164800 → peak 362000(3일전) → 현재 252500 (상승분 56% 반납, 3일내)
    sds = [164800, 180000, 200000, 230000, 260000, 300000, 340000, 362000, 330000, 290000, 252500]
    assert gave_back_recent_gain([_c(x) for x in sds]) is True
    # 꾸준한 상승 → 반납 아님
    assert gave_back_recent_gain([_c(100 + i) for i in range(11)]) is False
    # 얕은 눌림(상승분 일부만 반납) → 제외 아님
    shallow = [100, 110, 120, 130, 140, 150, 160, 170, 168, 165, 162]  # 고점170, 현재162, 반납 ~13%
    assert gave_back_recent_gain([_c(x) for x in shallow]) is False


def test_b_momentum_relief_shallow_not_deep() -> None:
    """B 눌림목 당일하락 페널티 면제는 '얕은 눌림'(낙폭≤25%)만 — 깊은 낙폭(LG전자형)은 페널티 유지."""
    base = {"price": 1000.0, "strategy": "B. 주도주 20일선 눌림목", "change_pct": -5.0,
            "gap20": 3.0, "_liq": 5.0}
    # 얕은: _nh=-18 → 낙폭 21% (면제) / 깊은: _nh=-28 → 낙폭 31% (페널티 유지)
    picks = [{"ticker": "SHAL", "name": "얕은눌림", "_nh": -18.0, **base},
             {"ticker": "DEEP", "name": "깊은낙폭", "_nh": -28.0, **base}]
    out = select_top3(picks)
    assert out[0]["ticker"] == "SHAL"  # 면제로 점수↑ → 위
    shal = next(o for o in out if o["ticker"] == "SHAL")
    deep = next(o for o in out if o["ticker"] == "DEEP")
    assert shal["score"] > deep["score"]  # 차이 = 면제된 모멘텀 페널티(0.5*5=2.5)


def test_top3_demotes_4h_overheat() -> None:
    """4시간봉 과열(overheat_4h)도 동일하게 강등 + overheat 플래그 반영."""
    picks = [_pick("AAA", "정상"), _pick("CCC", "4H과열", overheat_4h=True)]
    out = select_top3(picks)
    assert out[0]["ticker"] == "AAA"
    ccc = next(o for o in out if o["ticker"] == "CCC")
    assert ccc["overheat"] is True and ccc["overheat_4h"] is True
    assert "4시간봉" in ccc["reason"]
