"""텔레그램 발송 검증 — 조건검색 결과를 실제로 전송하고 로그 확인.

실행: python scripts/send_screen_test.py [--ping]
  --ping : 조건검색 없이 간단한 테스트 메시지만 발송 (빠른 연결 확인)

발송 결과는 logs/telegram.log 에 기록됨.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Bot

from src.bot.messages import format_screening_result
from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.logging_setup import LOG_DIR, get_telegram_logger, setup_logging
from src.notify.telegram.adapter import TelegramNotifier
from src.screener.config import load_screener_config
from src.screener.pipeline import run_screening


async def run(ping_only: bool) -> None:
    setup_logging()
    tg_log = get_telegram_logger()

    settings = get_settings()
    chat_ids = settings.allowed_chat_ids()
    if not chat_ids:
        print("❌ TELEGRAM_ALLOWED_CHAT_IDS 미설정")
        return
    chat_id = str(chat_ids[0])

    bot = Bot(token=settings.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)

    print(f"발송 대상 chat_id: {chat_id}")
    tg_log.info("=== 발송 테스트 시작 chat_id=%s ping_only=%s ===", chat_id, ping_only)

    if ping_only:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = f"🔔 *연결 테스트*\n발송 시각: {now}\n봇이 정상 동작합니다."
        ok = await notifier.send(chat_id, text)
        print(f"발송 결과: {'✅ 성공' if ok else '❌ 실패'}")
    else:
        print("조건검색 실행 중... (핫종목 분석, 수 분 소요)")
        cfg = load_screener_config()
        cfg.hot_stocks_top = 40
        picks = await run_screening(notifier_datasource(settings), [], cfg)
        text = format_screening_result(picks, mode_label="조건검색 테스트")
        print(f"포착 {len(picks)}종목 → 텔레그램 발송 시도")
        ok = await notifier.send(chat_id, text)
        print(f"발송 결과: {'✅ 성공' if ok else '❌ 실패'}")

    tg_log.info("=== 발송 테스트 종료 ===")
    print(f"\n📄 로그 확인: {LOG_DIR / 'telegram.log'}")


def notifier_datasource(settings):
    return KisAdapter(
        settings.kis_app_key, settings.kis_app_secret,
        settings.kis_account_no, settings.kis_env,
    )


if __name__ == "__main__":
    ping = "--ping" in sys.argv
    asyncio.run(run(ping))
