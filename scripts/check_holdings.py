"""보유종목 손절 점검 — KIS 계좌 보유종목이 20일선 이탈/근접인지 확인 후 텔레그램 발송.

수동 실행 또는 cron(장마감 전 14:50)으로 사용.
실행: python scripts/check_holdings.py [--no-tg]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Bot

from src.alerts.stoploss import check_holdings, format_stoploss_alert
from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.logging_setup import setup_logging
from src.notify.telegram.adapter import TelegramNotifier


async def main() -> None:
    setup_logging()
    no_tg = "--no-tg" in sys.argv

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print("보유종목 손절선(20일선) 점검 중...")
    alerts = await check_holdings(adapter)
    msg = format_stoploss_alert(alerts)
    print(msg)

    if not no_tg and alerts:
        bot = Bot(token=s.telegram_bot_token)
        notifier = TelegramNotifier(bot=bot)
        chat_id = str(s.allowed_chat_ids()[0])
        await notifier.send(chat_id, msg)
        print("\n텔레그램 발송 완료")


if __name__ == "__main__":
    asyncio.run(main())
