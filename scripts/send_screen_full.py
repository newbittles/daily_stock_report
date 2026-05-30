"""실전 /screen 흐름 검증 — 전략별 요약 + 종목별 차트를 텔레그램으로 발송.

cmd_screen과 동일한 로직을 CLI로 실행 (봇 상시 실행 없이 검증용).
실행: python scripts/send_screen_full.py
발송 로그: logs/telegram.log
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Bot

from src.bot.messages import format_pick_caption, format_screening_by_strategy
from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.logging_setup import LOG_DIR, get_telegram_logger, setup_logging
from src.market_report.chart import render_chart
from src.notify.telegram.adapter import TelegramNotifier
from src.screener.config import load_screener_config
from src.screener.pipeline import run_screening


async def run() -> None:
    setup_logging()
    tg_log = get_telegram_logger()

    s = get_settings()
    chat_id = str(s.allowed_chat_ids()[0])
    bot = Bot(token=s.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    cfg = load_screener_config()
    cfg.hot_stocks_top = 40
    enabled = cfg.enabled_strategies()
    print(f"전략 {len(enabled)}개:", [st.name for st in enabled])
    print("조건검색 실행 중... (핫종목 40개 분석, 수 분 소요)")
    tg_log.info("=== /screen 풀 발송 테스트 시작 ===")

    picks = await run_screening(adapter, [], cfg)
    print(f"포착 {len(picks)}종목")

    # 1. 전략별 요약
    summary = format_screening_by_strategy(picks, enabled)
    ok = await notifier.send(chat_id, summary)
    print(f"요약 발송: {'✅' if ok else '❌'}")

    # 2. 종목별 차트
    seen = set()
    sent_charts = 0
    for p in picks:
        if p.ticker in seen:
            continue
        seen.add(p.ticker)
        try:
            chart = await asyncio.to_thread(render_chart, p.ticker, p.name)
            if chart:
                cap = format_pick_caption(p, p.matches[0])
                if await notifier.send_photo(chat_id, str(chart), cap):
                    sent_charts += 1
                    print(f"  📊 {p.name} 차트 발송 ✅")
        except Exception as exc:
            print(f"  ❌ {p.name} 차트 실패: {exc}")

    print(f"\n차트 {sent_charts}개 발송 완료")
    tg_log.info("=== /screen 풀 발송 테스트 종료 (요약1 + 차트%d) ===", sent_charts)
    print(f"📄 로그: {LOG_DIR / 'telegram.log'}")


if __name__ == "__main__":
    asyncio.run(run())
