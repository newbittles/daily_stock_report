"""us_morning 리포트 — 종목 정보가 미국 종목만(한국 종목 아님)인지 검증."""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.telegram_notify import _format_us_morning_summary


def _us_snap() -> MarketSnapshot:
    snap = MarketSnapshot(mode="us_morning", generated_at=datetime(2026, 6, 4, 7, 30))
    snap.us_indices = [{"name": "S&P500", "price": 5300.0, "change_pct": 1.2},
                       {"name": "나스닥", "price": 17000.0, "change_pct": 1.5}]
    snap.us_sectors = [{"name": "반도체", "change_pct": 2.1}]
    snap.theme_commentary = "미국 반도체 강세 → 한국 반도체 주목."
    snap.us_top3 = [
        {"symbol": "NVDA", "name": "NVIDIA", "price": 1200.5, "change_pct": 3.2,
         "sector": "Information Technology", "reason": "60일 신고가", "cross_signal": "PULLBACK"},
    ]
    snap.us_screen_groups = [
        {"label": "📈 C 추세추종", "initial": "C", "picks": [
            {"symbol": "AVGO", "name": "Broadcom", "price": 1500.0, "change_pct": 2.1,
             "sector": "IT", "reason": "추세", "cross_signal": None}]},
    ]
    return snap


def test_us_morning_shows_us_stocks() -> None:
    msg = _format_us_morning_summary(_us_snap())
    assert "미국 추천 Top 3" in msg
    assert "NVDA" in msg and "NVIDIA" in msg
    assert "$1,200.5" in msg  # 달러 표기
    assert "미국 종목 스크리닝" in msg
    assert "AVGO" in msg
    # 한국장 연결성 코멘트는 유지
    assert "한국장 시사점" in msg


def test_us_morning_no_korean_stock_links() -> None:
    """미국 종목만 — 한국 네이버 종목 링크가 없어야 함."""
    msg = _format_us_morning_summary(_us_snap())
    assert "finance.naver.com/item" not in msg
    assert "시초 매수" not in msg  # 구 한국 시초 Top3 문구 제거됨


async def test_collect_us_screening_adds_yf_symbol(monkeypatch) -> None:
    """BRKB(FDR) 픽 → us_top3/그룹 dict에 야후링크용 yf_symbol='BRK-B' 부착."""
    from src.datasource.base import Candle
    from src.market_report import pipeline as P
    from src.screener.engine import ScreenMatch
    from src.screener.us_pipeline import USStockPick

    cs = [Candle("20260602", 450, 455, 448, 452.0, 3_500_000)]
    pick = USStockPick(
        symbol="BRKB", name="Berkshire Hathaway", price=452.0, change_pct=1.2,
        sector="Financials", industry="Insurance",
        matches=[ScreenMatch(matched=True, strategy_name="C. 추세추종",
                             opinion="추세", reasons=["거래대금 16억 (OK)", "정배열"])],
        candles=cs, cross_signal=None,
    )

    # 네트워크 호출 전부 모킹 (combined 유니버스·환율·시총)
    async def fake_run(universe=None):
        return [pick]
    monkeypatch.setattr("src.screener.us_pipeline.run_us_screening", fake_run)

    async def fake_universe(nasdaq_top=300):
        return []
    monkeypatch.setattr("src.datasource.us.universe.get_hybrid_universe", fake_universe)

    async def fake_rate():
        return 1500.0
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_usd_krw", fake_rate)

    async def fake_mc(syms):
        return {s: 1e12 for s in syms}   # 1조 USD × 1500 = 1500조원 (2조 하한 통과)
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_market_caps", fake_mc)

    snap = MarketSnapshot(mode="us_morning", generated_at=datetime(2026, 6, 4, 7, 30))
    await P._collect_us_screening(snap)

    assert snap.us_top3[0]["symbol"] == "BRKB"          # 티커(FDR)는 그대로 병기
    assert snap.us_top3[0]["name"] == "버크셔해서웨이"     # 한국어 종목명으로 표기
    assert snap.us_top3[0]["yf_symbol"] == "BRK-B"       # 야후 링크용 정규화
    assert snap.us_screen_groups[0]["picks"][0]["yf_symbol"] == "BRK-B"
    assert "억" not in snap.us_top3[0]["reason"]          # 원화 '억' reason 회피
    assert snap.us_top3[0]["strategies"] == ["C"]         # #4 전략 표시
    assert snap.us_top3[0]["marcap_str"]                  # #2 시총(원화) 채워짐
    assert snap.us_top3[0]["turnover_str"]                # #2 거래대금(원화)


async def test_correction_badge_only_for_c(monkeypatch) -> None:
    """⚠️조정시작은 C(추세추종)에만 — A(수렴후상승)에선 배지 제거(#5)."""
    from src.datasource.base import Candle
    from src.market_report import pipeline as P
    from src.screener.engine import ScreenMatch
    from src.screener.us_pipeline import USStockPick

    cs = [Candle("20260602", 100, 101, 99, 100.0, 1_000_000)]
    a_pick = USStockPick(symbol="AAA", name="A Co", price=100.0, change_pct=1.0,
                         sector="Tech", industry="Tech",
                         matches=[ScreenMatch(matched=True, strategy_name="A. 수렴", opinion="x", reasons=["정배열"])],
                         candles=cs, cross_signal="CORRECTION")
    c_pick = USStockPick(symbol="CCC", name="C Co", price=100.0, change_pct=1.0,
                         sector="Tech", industry="Tech",
                         matches=[ScreenMatch(matched=True, strategy_name="C. 추세추종", opinion="x", reasons=["신고가"])],
                         candles=cs, cross_signal="CORRECTION")

    async def fake_run(universe=None):
        return [a_pick, c_pick]
    monkeypatch.setattr("src.screener.us_pipeline.run_us_screening", fake_run)

    monkeypatch.setattr("src.datasource.us.universe.get_hybrid_universe", lambda nasdaq_top=300: _coro([]))
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_usd_krw", lambda: _coro(1500.0))
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_market_caps",
                        lambda syms: _coro({s: 1e12 for s in syms}))

    snap = MarketSnapshot(mode="us_morning", generated_at=datetime(2026, 6, 4, 7, 30))
    await P._collect_us_screening(snap)

    groups = {g["initial"]: g for g in snap.us_screen_groups}
    a_dict = groups["A"]["picks"][0]
    c_dict = groups["C"]["picks"][0]
    assert a_dict["cross_signal"] is None          # A → 조정시작 제거
    assert c_dict["cross_signal"] == "CORRECTION"  # C → 유지


async def _coro(v):
    return v


# ─── 미국장 장전(프리장) 리포트 ────────────────────────────────────────────
async def test_overlay_premarket(monkeypatch) -> None:
    """프리장 오버레이 — change_pct를 프리장 등락률로, close_pct에 마감 보존."""
    from src.market_report import pipeline as P

    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime(2026, 6, 4, 19, 0))
    snap.us_top3 = [{"symbol": "NVDA", "change_pct": 1.0, "price": 450.0}]
    snap.us_screen_groups = [{"initial": "C", "picks": [
        {"symbol": "AAPL", "change_pct": -1.0, "price": 310.0},
        {"symbol": "ZZZZ", "change_pct": 3.0, "price": 50.0},  # 프리장 미체결
    ]}]

    async def fake_pm(syms):
        return {"NVDA": {"price": 460.0, "change_pct": 2.5},
                "AAPL": {"price": 313.0, "change_pct": 1.0}}
    monkeypatch.setattr("src.datasource.us.fdr_source.fetch_us_premarket", fake_pm)

    await P._overlay_premarket(snap)
    nv = snap.us_top3[0]
    assert nv["change_pct"] == 2.5 and nv["close_pct"] == 1.0 and nv["price"] == 460.0 and nv["premkt"]
    ap = snap.us_screen_groups[0]["picks"][0]
    assert ap["change_pct"] == 1.0 and ap["premkt"]
    zz = snap.us_screen_groups[0]["picks"][1]
    assert zz["change_pct"] == 3.0 and zz["premkt"] is False  # 미체결 → 마감값 유지


def test_us_premarket_telegram_header() -> None:
    """장전 리포트 헤더 — '장전(프리장)' + 프리장 기준 안내."""
    from src.market_report.telegram_notify import _format_us_morning_summary
    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime(2026, 6, 4, 19, 0))
    snap.us_indices = [{"name": "S&P500", "price": 6800.0, "change_pct": 1.0}]
    msg = _format_us_morning_summary(snap)
    assert "장전" in msg and "프리장 기준" in msg
    assert "us-pre.html" in msg  # 웹 링크
