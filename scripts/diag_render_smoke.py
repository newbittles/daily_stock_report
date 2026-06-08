"""report.html 템플릿 렌더 스모크 — #471 시장 라벨 + #469 today_pct=None 가드 검증(파일 안 씀)."""
import sys
from datetime import datetime

sys.path.insert(0, ".")

from src.datasource import market_map as mm
from src.market_report.models import MarketSnapshot
from src.market_report.render import _env

mm._MAPS = {  # 오프라인 — 가짜 맵 주입
    "kr": {"000660": "코스피", "247540": "코스닥", "009150": "코스피"},
    "us": {"NVDA": "나스닥", "KO": "NYSE"},
}

snap = MarketSnapshot(mode="kr_premarket", generated_at=datetime(2026, 6, 8, 8, 5))
snap.top3 = [{"ticker": "000660", "name": "SK하이닉스", "price": 100000, "change_pct": 1.0,
              "reason": "테스트", "endstage": False, "theme": "", "marcap_str": "",
              "ai_summary": "", "supply_str": "", "is_leading_theme": False,
              "gap20": 1.0, "overheat": False, "strategies": [], "cross_signal": None,
              "vol_x": 0.0, "theme_kind": "theme"}]
snap.prev_top3_status = [
    {"ticker": "009150", "name": "삼성전기", "return_pct": -11.4, "today_pct": -9.7},
]
snap.prev_candidates_status = [
    {"ticker": "247540", "name": "에코프로비엠", "return_pct": -2.0, "today_pct": None},  # None 가드
]
snap.e_picks = [
    {"ticker": "", "symbol": "NVDA", "name": "엔비디아", "price": 120.0, "change_pct": -3.0,
     "rsi": 28.0, "market_bottom": False, "market_rsi": None, "fg_score": None, "reason": ""},
]
snap.top_gainers = []

env = _env()
html = env.get_template("report.html").render(title="스모크", snap=snap)
for needle in ["코스피", "코스닥", "NXT 미체결"]:
    assert needle in html, f"MISSING(kr_premarket): {needle}"

snap.mode = "us_morning"  # e_picks(US) 섹션은 us 모드에서 노출
html2 = env.get_template("report.html").render(title="스모크", snap=snap)
assert "나스닥" in html2, "MISSING(us_morning): 나스닥"
print("render smoke OK — 라벨·None가드 출력 확인")
