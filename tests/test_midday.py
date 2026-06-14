"""장중 리포트(midday) — 전날 top3 상태 계산 + 텔레그램 메시지 포맷."""
from __future__ import annotations

import json
from datetime import datetime

from src.datasource.base import RankedStock
from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.telegram_notify import _format_midday_summary
from src.market_report.top3_status import compute_status, find_prev_top3


def _idx(market: str, value: float, pct: float) -> IndexQuote:
    return IndexQuote(market=market, value=value, change=0.0, change_pct=pct,
                      volume=0, trade_value=0.0, timestamp=datetime.now())


# ─── find_prev_top3 ──────────────────────────────────────────────────────
def test_find_prev_top3_picks_latest_before_today(tmp_path):
    for d, name in [("2026-06-01", "A"), ("2026-06-03", "B"), ("2026-06-04", "C")]:
        (tmp_path / f"top3_{d}_pre.json").write_text(
            json.dumps({"date": d, "mode": "pre_close",
                        "picks": [{"ticker": "000", "name": name, "price": 100}]}),
            encoding="utf-8")
    res = find_prev_top3("2026-06-04", base_dir=tmp_path)
    assert res is not None
    date, picks = res
    assert date == "2026-06-03"          # 오늘(06-04) 직전, 06-04 파일은 제외
    assert picks[0]["name"] == "B"


def test_find_prev_top3_none_when_no_earlier(tmp_path):
    (tmp_path / "top3_2026-06-04_pre.json").write_text(
        json.dumps({"date": "2026-06-04", "picks": [{"ticker": "0", "name": "X", "price": 1}]}),
        encoding="utf-8")
    assert find_prev_top3("2026-06-04", base_dir=tmp_path) is None  # 직전 거래일 없음


def test_find_prev_top3_empty_dir(tmp_path):
    assert find_prev_top3("2026-06-04", base_dir=tmp_path) is None


# ─── compute_status (추천가 대비 + 오늘 등락 둘 다) ──────────────────────────
def test_compute_status_gain():
    st = compute_status({"ticker": "454910", "name": "두산로보틱스", "price": 100.0},
                        cur_price=106.0, today_pct=3.1)
    assert st["return_pct"] == 6.0      # (106-100)/100*100
    assert st["today_pct"] == 3.1
    assert st["name"] == "두산로보틱스"


def test_compute_status_loss_and_zero_rec():
    st = compute_status({"ticker": "x", "name": "Y", "price": 200.0}, 190.0, -1.5)
    assert st["return_pct"] == -5.0
    # 추천가 0이면 0% (0division 가드)
    z = compute_status({"ticker": "x", "name": "Y", "price": 0}, 190.0, 2.0)
    assert z["return_pct"] == 0.0


# ─── _format_midday_summary ──────────────────────────────────────────────
def _midday_snap() -> MarketSnapshot:
    snap = MarketSnapshot(mode="midday", generated_at=datetime(2026, 6, 4, 12, 0))
    snap.kospi = _idx("KOSPI", 2700.5, 0.5)
    snap.kosdaq = _idx("KOSDAQ", 850.2, -0.3)
    snap.market_flows_history = [
        {"date": "20260604", "kospi": {"personal": -100, "foreign": 1200, "institution": -300},
         "kosdaq": {"personal": 50, "foreign": -80, "institution": 30}},
        {"date": "20260603", "kospi": {"personal": 200, "foreign": -300, "institution": 100},
         "kosdaq": {"personal": 10, "foreign": 20, "institution": -5}},
    ]
    snap.summary = "오전장 외국인 순매수로 코스피 강세."
    snap.top_themes = []
    snap.top_gainers = [
        RankedStock(rank=1, ticker="454910", name="두산로보틱스", price=170000,
                    change_pct=12.3, volume=1000),
    ]
    snap.prev_top3_status = [
        {"ticker": "454910", "name": "두산로보틱스", "rec_price": 166700, "cur_price": 177000,
         "return_pct": 6.2, "today_pct": 3.1},
        {"ticker": "032830", "name": "삼성생명", "rec_price": 480000, "cur_price": 475200,
         "return_pct": -1.0, "today_pct": -0.5},
    ]
    snap.prev_top3_date = "2026-06-03"
    return snap


def test_format_midday_has_all_sections():
    msg = _format_midday_summary(_midday_snap())
    assert "장중 리포트" in msg
    assert "코스피 2,700.5" in msg and "코스닥 850.2" in msg     # 지수
    assert "투자자 수급" in msg and "외인" in msg                 # 수급
    assert "(-300)" in msg or "(+200)" in msg                    # 전일대비 병기
    assert "오전장" in msg                                        # AI 코멘트
    assert "핫 종목" in msg and "두산로보틱스" in msg             # 핫 종목
    assert "전날 추천 Top3 현황" in msg
    assert "추천가대비 +6.2%" in msg and "오늘 +3.1%" in msg      # 둘 다 표기
    assert "추천가대비 -1.0%" in msg and "오늘 -0.5%" in msg


def test_format_midday_info_diet_km1_km3():
    """#4 장중 정보 다이어트(2026-06-14): KM1 제목 '한국장 장중 리포트',
    KM3 투자자수급 개인·외인·기관 각 줄 분리."""
    msg = _format_midday_summary(_midday_snap())
    # KM1: 제목
    assert "🟢 *한국장 장중 리포트*" in msg
    # KM3: 코스피/코스닥 라벨 + 개인·외인·기관 각 줄
    assert "📈 *코스피*" in msg and "📈 *코스닥*" in msg
    assert "개인 -100(+200)" in msg
    assert "외인 +1,200(-300)" in msg
    assert "기관 -300(+100)" in msg


def test_format_midday_info_diet_km2_overnight():
    """KM2: 미국 야간 = 선물·기타 M7 빼고 테슬라·마이크론·SOXL·EWY만(#3과 동일)."""
    snap = _midday_snap()
    snap.us_overnight = {
        "futures": [{"name": "나스닥 선물", "change_pct": 0.5}],
        "m7": [{"symbol": "TSLA", "name": "테슬라", "change_pct": 2.1,
                "session_pct": None, "session_label": ""},
               {"symbol": "AAPL", "name": "애플", "change_pct": -0.4,
                "session_pct": None, "session_label": ""}],
        "etf": [{"symbol": "SOXL", "name": "SOXL(반도체 3X)", "change_pct": 3.4,
                 "session_pct": None, "session_label": ""}],
        "extra": [{"symbol": "MU", "name": "마이크론", "change_pct": 1.5,
                   "session_pct": None, "session_label": ""}],
    }
    msg = _format_midday_summary(snap)
    assert "나스닥 선물" not in msg and "애플" not in msg
    assert "테슬라" in msg and "마이크론" in msg and "SOXL" in msg


def test_format_midday_has_web_link():
    """장중 리포트도 웹 발행(마감전/후 포맷) — '전체 리포트 보기' 링크 포함."""
    msg = _format_midday_summary(_midday_snap())
    assert "전체 리포트 보기" in msg
    assert "midday.html" in msg


def test_format_midday_mobile_linebreaks():
    """모바일 가독성 — 지수가 한 줄에 몰리지 않고 줄바꿈된다."""
    msg = _format_midday_summary(_midday_snap())
    # 코스피와 코스닥은 서로 다른 줄
    kospi_line = next(ln for ln in msg.split("\n") if "코스피" in ln)
    assert "코스닥" not in kospi_line


# ─── 핫종목 (거래대금 상위 + 시총필터 + 전일대비·순매수연속일·테마) ──────────────
async def test_collect_hot_stocks_filter_and_streak(monkeypatch):
    """시총 5000억↑만, 거래대금순, 거래대금 전일대비·순매수 연속일·테마 채움."""
    from src.datasource.base import Candle
    from src.market_report import pipeline as P
    from src.market_report.models import StockRank

    snap = MarketSnapshot(mode="midday", generated_at=datetime(2026, 6, 4, 11, 40))
    snap.top_volume = [
        StockRank(1, "454910", "두산로보틱스", 170000, 0, 12.0, 1000, trade_value=5000),
        StockRank(2, "000001", "잡주", 500, 0, 29.0, 9999, trade_value=8000),   # 시총 미달
        StockRank(3, "005930", "삼성전자", 80000, 0, 1.0, 500, trade_value=3000),
    ]
    monkeypatch.setattr("src.datasource.market_cap.get_market_cap_map",
                        lambda: {"454910": 6e12, "000001": 1e11, "005930": 400e12})

    async def fake_sectors(codes, max_fetch=40):
        return {"454910": "로봇", "005930": "반도체"}
    monkeypatch.setattr("src.market_report.scrapers.sector.get_stock_sectors", fake_sectors)

    class FakeAdapter:
        async def get_ohlcv(self, ticker, days=3):
            return [Candle("20260603", 100, 100, 100, 100, 100),
                    Candle("20260604", 100, 100, 100, 100, 300)]  # 거래대금 +200%

        async def get_stock_investor_daily(self, ticker, days=10):
            return [{"orgn": 10, "frgn": 5, "prsn": -2}, {"orgn": 3, "frgn": -1, "prsn": 1}]

    hot = await P.collect_hot_stocks(snap, FakeAdapter(), top=5, min_marcap_won=5e11)
    assert [h["ticker"] for h in hot] == ["454910", "005930"]  # 잡주(시총미달) 제외, 거래대금순
    h0 = hot[0]
    assert h0["theme"] == "로봇"
    assert h0["streak"] == {"orgn": 2, "frgn": 1, "prsn": 0}   # 연속 순매수일
    assert h0["tv_change"] == 200                              # 거래대금 전일대비 +200%
    assert h0["tv_today"] == 100 * 300                         # 오늘 거래대금 금액(원)


def test_format_hot_stocks_renders():
    from src.market_report.telegram_notify import _format_hot_stocks
    hot = [{"ticker": "454910", "name": "두산로보틱스", "price": 170000, "change_pct": 12.0,
            "tv_today": 1.5e11, "tv_change": 200,
            "streak": {"orgn": 2, "frgn": 1, "prsn": 0}, "theme": "로봇"}]
    txt = "\n".join(_format_hot_stocks(hot))
    assert "핫 종목" in txt and "상승률 상위" in txt
    assert "두산로보틱스" in txt
    assert "거래대금 1,500억" in txt              # 금액 표시
    assert "(전일대비:+200%)" in txt              # 괄호 전일대비
    assert "수급: 기관2일·외인1일 순매수" in txt   # 아래 줄 수급현황
    assert "개인" not in txt                      # prsn=0 → 표시 안 함
    assert "테마: 로봇" in txt


# ─── 장중 분봉 추세 통합 (#473/#474) ─────────────────────────────────────────
def test_format_midday_shows_intraday_flow_and_new_sections():
    """전일Top3·종가베팅·보유에 장중 분봉 추세 줄 + 신규 섹션 표시."""
    snap = _midday_snap()
    snap.prev_top3_status[0]["flow_desc"] = "장중 -10.4%(09:00)까지 밀렸다 반등 양봉, 현재 -2.0%(저점대비 +8.4%p)"
    snap.prev_top3_status[0]["flow_shape"] = "V_REBOUND"
    snap.prev_candidates_status = [
        {"ticker": "005930", "name": "삼성전자", "rec_price": 80000, "cur_price": 78000,
         "return_pct": -2.5, "today_pct": -1.1,
         "flow_desc": "장중 약세 지속, 현재 -1.1%(저점 -2.0%)", "flow_shape": "WEAK"},
    ]
    snap.prev_candidates_date = "2026-06-03"
    snap.holdings_status = [
        {"ticker": "042660", "name": "한화오션", "price": 112000, "profit_rate": 8.5,
         "cross_signal": None,
         "flow_desc": "장중 +5.0%까지 올랐다 밀림, 현재 +1.2%(고점대비 -3.8%p)",
         "flow_shape": "PEAK_FADE"},
    ]
    msg = _format_midday_summary(snap, private=True)   # 보유종목은 오너 전용(2026-06-14)
    assert "밀렸다 반등 양봉" in msg                       # 전일Top3 흐름 줄
    assert "🎯 *전일 종가베팅 후보 현황*" in msg           # 신규 섹션
    assert "약세 지속" in msg
    assert "📋 *보유종목 장중 추세*" in msg                # 신규 섹션(오너)
    assert "한화오션" in msg and "올랐다 밀림" in msg
    # 공개판(private=False)에는 보유종목 미노출
    assert "📋 *보유종목 장중 추세*" not in _format_midday_summary(snap, private=False)


def test_format_midday_flow_omitted_when_absent():
    """flow_desc 없으면 추세 줄 생략(기존 동작 유지)."""
    msg = _format_midday_summary(_midday_snap())
    assert "📊 장중" not in msg  # 흐름 미주입 → 줄 없음
