"""순수 함수 테스트 — risk_exit (하드스톱·ATR 트레일링·R익절·변동성 사이징).

전부 외부 의존 0. 트레이더 표준 리스크관리를 MA 단계청산에 '추가 레이어'로 결합하기 위한 코어.
"""
from __future__ import annotations

from src.trading.risk_exit import (
    active_stop,
    atr_stop_mult,
    chandelier_stop,
    combine_exits,
    decide_risk_exit,
    hard_stop_price,
    position_size,
    r_multiple,
    should_buy_pyramid,
)


def test_atr_stop_mult_per_strategy():
    # A/B 타이트 = 1.5×ATR, C/D 와이드 = 2.5×ATR
    assert atr_stop_mult(["A"]) == 1.5
    assert atr_stop_mult(["B"]) == 1.5
    assert atr_stop_mult(["C"]) == 2.5
    assert atr_stop_mult(["D"]) == 2.5
    # 다중매칭 → 넓은 쪽 우선(C/D 있으면 와이드) — 기존 decide_exit 컨벤션과 동일
    assert atr_stop_mult(["A", "C"]) == 2.5
    # 전략정보 없음 → 기본 2.0
    assert atr_stop_mult(None) == 2.0
    assert atr_stop_mult([]) == 2.0


def test_hard_stop_price_closer_of_atr_and_pct():
    # entry 100, ATR 2, A(1.5×) → atr_stop 97, pct(-8%) 92 → 가까운(높은) 쪽 = 97
    assert hard_stop_price(100.0, 2.0, ["A"]) == 97.0
    # C(2.5×) → 100-5=95 (pct 92보다 가까움)
    assert hard_stop_price(100.0, 2.0, ["C"]) == 95.0
    # 변동성 큰 종목: ATR 10, C(2.5×)=75 인데 -8%캡 92가 더 가까움 → 92
    assert hard_stop_price(100.0, 10.0, ["C"]) == 92.0
    # ATR None → 퍼센트 손절만 (entry*(1-0.08))
    assert hard_stop_price(100.0, None, ["A"]) == 92.0


def test_chandelier_stop():
    # 보유 최고가 120, ATR 2, 3× → 120-6 = 114
    assert chandelier_stop(120.0, 2.0, mult=3.0) == 114.0
    assert chandelier_stop(120.0, None) is None  # ATR 없으면 산정 불가


def test_r_multiple():
    # entry 100, 초기손절 95 → 1R = 5원. 현재 105 → +1R, 110 → +2R
    assert r_multiple(100.0, 105.0, 95.0) == 1.0
    assert r_multiple(100.0, 110.0, 95.0) == 2.0
    assert r_multiple(100.0, 95.0, 95.0) == -1.0
    # 위험단위 0 이하(손절가 ≥ 진입가)는 정의 불가 → None
    assert r_multiple(100.0, 110.0, 100.0) is None
    assert r_multiple(100.0, 110.0, 105.0) is None


def test_position_size_volatility_based():
    # 계좌 1천만, 1트레이드 위험 1% = 10만원. 주당위험 5원 → 20,000주
    assert position_size(10_000_000, 0.01, 100.0, 95.0) == 20_000
    # 0.5% = 5만원 / 주당 5 → 10,000주
    assert position_size(10_000_000, 0.005, 100.0, 95.0) == 10_000
    # 손절가 ≥ 진입가(위험 0) → 0 (사이징 불가)
    assert position_size(10_000_000, 0.01, 100.0, 100.0) == 0
    # 음수/0 입력 방어
    assert position_size(0, 0.01, 100.0, 95.0) == 0


def test_active_stop_takes_highest_protection():
    # 초기손절 95, 아직 안 올라 chandelier(101-6=95)·본전 미적용 → 95
    assert active_stop(100.0, 95.0, 101.0, 2.0, partial_taken=False) == 95.0
    # 충분히 상승: chandelier(130-6=124)가 초기손절보다 높음 → 124 (트레일링 상향)
    assert active_stop(100.0, 95.0, 130.0, 2.0, partial_taken=False) == 124.0
    # 절반익절 후엔 최소 본전(entry)까지 손절 상향 → max(95, chandelier, 100)
    assert active_stop(100.0, 95.0, 101.0, 1.0, partial_taken=True) == 100.0
    # ATR None → chandelier 무시, 본전만 반영
    assert active_stop(100.0, 95.0, 130.0, None, partial_taken=True) == 100.0


def test_decide_risk_exit_hard_stop_hit():
    # 진입 직후 하드스톱 이탈 → 전량
    action, reason, new_stop = decide_risk_exit(
        entry=100.0, initial_stop=95.0, highest=100.0, atr=2.0, current=94.0
    )
    assert action == "SELL_ALL"
    assert "스톱" in reason


def test_decide_risk_exit_plus_1r_partial():
    # +1R 도달(현재 105, 1R=5) → 절반익절 + 손절 본전 이동
    action, reason, new_stop = decide_risk_exit(
        entry=100.0, initial_stop=95.0, highest=105.0, atr=2.0, current=105.0,
        partial_taken=False,
    )
    assert action == "SELL_HALF"
    assert new_stop == 100.0  # 본전으로 상향


def test_decide_risk_exit_chandelier_trailing():
    # 크게 상승 후(최고 130) 트레일링스톱(124) 이탈 → 전량
    action, reason, new_stop = decide_risk_exit(
        entry=100.0, initial_stop=95.0, highest=130.0, atr=2.0, current=123.0,
        partial_taken=True,
    )
    assert action == "SELL_ALL"


def test_decide_risk_exit_hold():
    # 손절 위 + 1R 미만 → 보유
    action, reason, new_stop = decide_risk_exit(
        entry=100.0, initial_stop=95.0, highest=104.0, atr=2.0, current=104.0,
        partial_taken=True,
    )
    assert action == "HOLD"


def test_combine_exits_takes_more_aggressive():
    # MA청산과 리스크청산 결합 — '먼저 닿는 것'(더 강한 매도) 우선
    assert combine_exits(("HOLD", "정배열"), ("SELL_ALL", "하드스톱"))[0] == "SELL_ALL"
    assert combine_exits(("SELL_HALF", "20MA"), ("SELL_ALL", "트레일링"))[0] == "SELL_ALL"
    assert combine_exits(("SELL_HALF", "20MA"), ("HOLD", ""))[0] == "SELL_HALF"
    assert combine_exits(("HOLD", ""), ("HOLD", ""))[0] == "HOLD"
    # 채택된 행동의 사유가 따라온다
    assert "하드스톱" in combine_exits(("HOLD", "정배열"), ("SELL_ALL", "하드스톱"))[1]
    # 동일 강도면 두 사유를 모두 보존
    a, r = combine_exits(("SELL_HALF", "20MA"), ("SELL_HALF", "조정시작"))
    assert a == "SELL_HALF" and "20MA" in r and "조정시작" in r


def test_should_buy_pyramid():
    # 미보유 → 매수
    assert should_buy_pyramid(held=False, last_entry_date=None, today="2026-06-15") is True
    # 보유 + 오늘 이미 진입 → 같은 날 중복매수 금지(크론 재실행 안전)
    assert should_buy_pyramid(held=True, last_entry_date="2026-06-15", today="2026-06-15") is False
    # 보유 + 과거 진입인데 오늘 재추천 → 추가매수(피라미딩)
    assert should_buy_pyramid(held=True, last_entry_date="2026-06-12", today="2026-06-15") is True
