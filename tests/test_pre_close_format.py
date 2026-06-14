"""한국장 마감전(pre_close) 텔레그램 정보 다이어트 — MB1·MB3·MB4 (사용자 2026-06-14).

MB1 제목 '한국장 마감전', MB3 수급 요약 제거, MB4 강세/약세 테마·E·급등초입 제거하고
추천 Top3 + 종가베팅 5선만(+보유종목 유지)."""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.telegram_notify import _format_pre_summary


def _idx(market: str, value: float, pct: float) -> IndexQuote:
    return IndexQuote(market=market, value=value, change=0.0, change_pct=pct,
                      volume=0, trade_value=0.0, timestamp=datetime.now())


def _pre_snap() -> MarketSnapshot:
    s = MarketSnapshot(mode="pre_close", generated_at=datetime(2026, 6, 14, 14, 50))
    s.kospi = _idx("KOSPI", 7800.0, 0.5)
    s.kosdaq = _idx("KOSDAQ", 1029.0, 0.3)
    s.summary = "오후장 외국인 순매수 지속."
    s.flows_summary = "외국인 3일 연속 순매수"   # MB3: 제거 대상
    s.market_flows_history = [
        {"date": "20260614", "kospi": {"personal": -100, "foreign": 1200, "institution": -300},
         "kosdaq": {"personal": 50, "foreign": -80, "institution": 30}},
    ]
    s.top3 = [{"ticker": "005930", "name": "삼성전자", "price": 70000, "change_pct": 1.5,
               "reason": "주도주 눌림목", "strategies": ["B"], "gap20": 2.0}]
    s.candidate_picks = [
        {"ticker": "000660", "name": "SK하이닉스", "price": 180000, "change_pct": 2.0,
         "strategies": ["B", "C"], "rationale": "20일선 눌림목 지지", "theme": "반도체"},
    ]
    return s


def test_pre_close_diet_mb1_mb3_mb4() -> None:
    s = _pre_snap()
    msg = _format_pre_summary(s)
    # MB1: 제목
    assert "🟡 *한국장 마감전*" in msg
    # MB3: 수급 요약(🔎) 제거
    assert "수급 요약" not in msg
    assert "3일 연속 순매수" not in msg
    # MB4: 추천 Top3 + 종가베팅 5선 표시
    assert "오늘의 추천 Top 3" in msg and "삼성전자" in msg
    assert "종가베팅 후보 5선" in msg and "SK하이닉스" in msg and "20일선 눌림목 지지" in msg
    # MB4: 강세/약세 테마 섹션 제거
    assert "강세/약세 테마" not in msg


def test_pre_close_no_candidates_section_omitted() -> None:
    """종가베팅 후보 없으면 섹션 생략(죽지 않음)."""
    s = _pre_snap()
    s.candidate_picks = []
    msg = _format_pre_summary(s)
    assert "종가베팅 후보 5선" not in msg
    assert "오늘의 추천 Top 3" in msg   # Top3는 그대로


def test_pre_close_holdings_owner_only() -> None:
    """보유종목은 오너판(private=True)에만, 공개판(private=False)에는 제외(2026-06-14 유저별 분리)."""
    s = _pre_snap()
    s.holdings_status = [{"ticker": "042660", "name": "한화오션", "price": 112000,
                          "profit_rate": 8.5, "state": "HOLD", "reason": "20MA 위 홀드"}]
    owner = _format_pre_summary(s, private=True)
    public = _format_pre_summary(s, private=False)
    assert "📋 *보유종목 상태*" in owner and "한화오션" in owner
    assert "📋 *보유종목 상태*" not in public and "한화오션" not in public
    # Top3·종가베팅은 둘 다 표시(공개 정보)
    assert "오늘의 추천 Top 3" in owner and "오늘의 추천 Top 3" in public
    # 웹 링크: 오너판은 -owner URL, 공개판은 일반 URL
    assert "-owner.html" in owner
    assert "-owner.html" not in public
