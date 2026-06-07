"""#469 포맷 오프라인 검증 — 네트워크 없이 kr_premarket 텔레그램 메시지 렌더만 확인."""
import sys
from datetime import datetime

sys.path.insert(0, ".")

from src.market_report.models import IndexQuote, MarketSnapshot
from src.market_report.telegram_notify import _format_kr_morning_summary

snap = MarketSnapshot(mode="kr_premarket", generated_at=datetime(2026, 6, 8, 8, 5))
snap.kospi = IndexQuote("KOSPI", 8160.59, -478.82, -5.54, 0, 0.0, datetime.now())
snap.kosdaq = IndexQuote("KOSDAQ", 1002.44, -52.0, -4.93, 0, 0.0, datetime.now())
snap.index_pct_label = "전일"
snap.fx = {"name": "USD/KRW", "value": 1559.4, "change_pct": 1.72}
snap.overtime_gainers = [
    {"ticker": "085620", "name": "미래에셋생명", "overtime_pct": 17.8},
]
snap.prev_top3_date = "2026-06-05"
snap.prev_top3_status = [
    {"ticker": "009150", "name": "삼성전기", "rec_price": 1790000, "cur_price": 1586000,
     "return_pct": -11.4, "today_pct": -9.73},
    {"ticker": "055550", "name": "신한지주", "rec_price": 50000, "cur_price": 49000,
     "return_pct": -2.0, "today_pct": None},  # NXT 미체결 → '—'
]
snap.prev_candidates_date = "2026-06-05"
snap.prev_candidates_status = [
    {"ticker": "005930", "name": "삼성전자", "rec_price": 90000, "cur_price": 88000,
     "return_pct": -2.2, "today_pct": -1.1},
]

print(_format_kr_morning_summary(snap))
