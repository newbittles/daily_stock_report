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


def test_format_midday_no_web_link():
    """장중 리포트는 텔레그램 전용 — 웹 '전체 리포트 보기' 링크 없음."""
    msg = _format_midday_summary(_midday_snap())
    assert "전체 리포트 보기" not in msg
    assert "github.io" not in msg


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


def test_format_hot_stocks_renders():
    from src.market_report.telegram_notify import _format_hot_stocks
    hot = [{"ticker": "454910", "name": "두산로보틱스", "price": 170000, "change_pct": 12.0,
            "tv_change": 200, "streak": {"orgn": 2, "frgn": 1, "prsn": 0}, "theme": "로봇"}]
    txt = "\n".join(_format_hot_stocks(hot))
    assert "핫 종목" in txt and "거래대금 상위" in txt
    assert "두산로보틱스" in txt
    assert "거래대금 전일比 +200%" in txt
    assert "기관2일" in txt and "외인1일" in txt
    assert "개인" not in txt          # prsn=0 → 표시 안 함
    assert "테마: 로봇" in txt
