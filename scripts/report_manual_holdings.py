"""수동 보유종목 A/B/C 상태 리포트 (KIS 계좌 외부 보유 — 다른 증권사 등).

종목코드는 FinanceDataReader로 검증 완료(2026-05-31):
  현대모비스 012330 · 삼성에스디에스 018260 · 현대무벡스 319400

사용법: python scripts/report_manual_holdings.py [--tg]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.alerts.holdings_report import diagnose_holdings, format_holdings_report
from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter

# 수동 보유종목 (사용자 제공)
MANUAL_HOLDINGS = [
    {"ticker": "012330", "name": "현대모비스", "avg_price": 610000, "quantity": 10},
    {"ticker": "018260", "name": "삼성에스디에스", "avg_price": 271700, "quantity": 74},
    {"ticker": "319400", "name": "현대무벡스", "avg_price": 43840, "quantity": 60},
]


async def main() -> None:
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    rows = await diagnose_holdings(a, holdings=MANUAL_HOLDINGS)
    msg = format_holdings_report(rows)
    print(msg)

    if "--tg" in sys.argv:
        from telegram import Bot

        from src.notify.telegram.adapter import TelegramNotifier
        notifier = TelegramNotifier(bot=Bot(token=s.telegram_bot_token))
        chat_id = str(s.allowed_chat_ids()[0])
        ok = await notifier.send(chat_id, msg)
        print(f"\n텔레그램: {'✅' if ok else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
