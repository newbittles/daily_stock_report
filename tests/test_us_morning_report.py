"""us_morning 리포트 — 종목 정보가 미국 종목만(한국 종목 아님)인지 검증."""
from __future__ import annotations

from datetime import datetime

from src.market_report.models import MarketSnapshot
from src.market_report.render import report_path
from src.market_report.telegram_notify import _format_us_morning_summary


def test_us_afterhours_routes_like_us_morning() -> None:
    """13시 미국 애프터장 리뷰(us_afterhours, 사용자 2026-06-10): 별도 파일(us-after) +
    미국 마감 구조 텔레그램 포맷(애프터장 타이틀), 한국지수 미표시."""
    snap = MarketSnapshot(mode="us_afterhours", generated_at=datetime(2026, 6, 10, 13, 0))
    snap.us_indices = [{"name": "나스닥", "price": 20000.0, "change_pct": 1.2}]
    snap.us_sectors = [{"name": "반도체", "change_pct": 2.1}]
    # 별도 출력 파일(us_morning 덮어쓰지 않음)
    assert report_path(snap).name == "2026-06-10-us-after.html"
    txt = _format_us_morning_summary(snap)
    assert "애프터장 리뷰" in txt
    assert "코스피" not in txt


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


def test_us_morning_telegram_is_overview_only() -> None:
    """텔레그램 = 시황(지수)+주도섹터까지만, 종목 상세는 웹 링크로(사용자 2026-06-04)."""
    msg = _format_us_morning_summary(_us_snap())
    # 시황·주도섹터는 포함
    assert "반도체" in msg                # 주도섹터
    assert "강세 섹터" in msg
    assert "리포트 열기" in msg            # 웹 링크
    # 종목 상세(Top3·스크리닝·시사점)는 텔레그램에서 제거 → 웹으로
    assert "미국 추천 Top 3" not in msg
    assert "NVDA" not in msg and "AVGO" not in msg
    assert "미국 종목 스크리닝" not in msg
    assert "한국장 시사점" not in msg


def test_us_premarket_telegram_top5() -> None:
    """프리장 급등 TOP5를 텔레그램에도 표시(us_premarket 한정, 사용자 2026-06-08 승인).

    overview-only 정책(2026-06-04)의 승인된 예외 — 마감(us_morning)에는 여전히 없음.
    종목명 옆 시장 라벨 병기(#471) — 맵을 명시 주입해 결정론화."""
    from src.datasource import market_map as mm
    mm._MAPS = {"kr": {}, "us": {"NVDA": "나스닥", "AVGO": "나스닥"}}
    try:
        snap = _us_snap()
        snap.mode = "us_premarket"
        snap.us_premarket_top = [
            {"symbol": "NVDA", "name": "NVIDIA", "change_pct": 4.2, "sector": "IT"},
            {"symbol": "AVGO", "name": "Broadcom", "change_pct": 2.1, "sector": "IT"},
        ]
        msg = _format_us_morning_summary(snap)
        assert "프리장 급등 TOP5" in msg
        assert "NVIDIA(NVDA·나스닥) +4.2%" in msg
        # 마감 리포트(us_morning)에는 TOP5 섹션 없음(overview-only 유지)
        snap2 = _us_snap()
        snap2.us_premarket_top = snap.us_premarket_top
        msg2 = _format_us_morning_summary(snap2)
        assert "프리장 급등 TOP5" not in msg2
    finally:
        mm._MAPS = None


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
    assert snap.us_theme_leaders == []  # #162 BRKB(Insurance)는 관심테마(양자 등) 아님 → 비어있음


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
    # 등락률은 프리장, 가격은 전일마감가 유지(사용자), 프리장가는 premkt_price 참고
    assert nv["change_pct"] == 2.5 and nv["close_pct"] == 1.0 and nv["premkt"]
    assert nv["price"] == 450.0 and nv["premkt_price"] == 460.0
    ap = snap.us_screen_groups[0]["picks"][0]
    assert ap["change_pct"] == 1.0 and ap["premkt"] and ap["price"] == 310.0
    zz = snap.us_screen_groups[0]["picks"][1]
    assert zz["change_pct"] == 3.0 and zz["premkt"] is False  # 미체결 → 마감값 유지


def test_telegram_split_keeps_under_limit() -> None:
    """긴 메시지(섹터대장+관심테마로 4096 초과)는 섹션 경계로 분할되어 한도 이하."""
    from src.market_report.telegram_notify import _TG_LIMIT, _split_for_telegram
    assert _split_for_telegram("짧음") == ["짧음"]
    big = "\n\n".join(f"*섹션 {i}*\n" + "가" * 200 for i in range(40))
    assert len(big) > 4096
    parts = _split_for_telegram(big)
    assert len(parts) > 1
    assert all(len(p) <= _TG_LIMIT for p in parts)


def test_us_premarket_telegram_header() -> None:
    """장전 리포트 헤더 — '장전(프리장)' + 프리장 기준 안내."""
    from src.market_report.telegram_notify import _format_us_morning_summary
    snap = MarketSnapshot(mode="us_premarket", generated_at=datetime(2026, 6, 4, 19, 0))
    snap.us_indices = [{"name": "S&P500", "price": 6800.0, "change_pct": 1.0}]
    msg = _format_us_morning_summary(snap)
    assert "장전" in msg and "프리장 기준" in msg
    assert "us-pre.html" in msg  # 웹 링크
