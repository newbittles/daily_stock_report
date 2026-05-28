"""Entry point — Kiwoom OCX + Telegram bot + APScheduler.

실행 순서:
  1. QApplication 생성 (OCX 이벤트 처리용 — headless 불가, 로그인 팝업 필요)
  2. 메인 스레드에서 Kiwoom() 생성 + CommConnect(block=True) — QAxWidget은 메인 스레드 전용
  3. asyncio.run(main(kiwoom)) 으로 봇·스케줄러 기동
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from telegram import Bot

from src.alerts.monitor import WatchlistMonitor
from src.bot.router import build_application
from src.config.settings import get_settings
from src.datasource.kiwoom.adapter import KiwoomAdapter
from src.notify.telegram.adapter import TelegramNotifier
from src.scheduler.jobs import setup_scheduler
from src.storage.db import get_connection, init_db
from src.storage.repos import AlertHistoryRepo, AnalysisCacheRepo, SignalLogRepo, TradeHistoryRepo, WatchlistRepo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def main(kiwoom: Any) -> None:
    settings = get_settings()

    # ── Storage ──────────────────────────────────────────────────────────────
    conn = get_connection(settings.db_path)
    init_db(conn)
    watchlist_repo = WatchlistRepo(conn)
    alert_repo = AlertHistoryRepo(conn)
    signal_repo = SignalLogRepo(conn)
    cache_repo = AnalysisCacheRepo(conn)
    trade_repo = TradeHistoryRepo(conn)

    # ── Kiwoom OCX adapter (kiwoom 인스턴스는 메인 스레드에서 미리 생성됨) ──────
    datasource = KiwoomAdapter(
        account_no=settings.kiwoom_account_no,
        env=settings.kiwoom_env,
        kiwoom=kiwoom,
    )
    await datasource.connect()

    # ── Telegram ──────────────────────────────────────────────────────────────
    bot = Bot(token=settings.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)

    # ── Watchlist monitor ─────────────────────────────────────────────────────
    allowed_chat_ids = [str(cid) for cid in settings.allowed_chat_ids()]
    monitor = WatchlistMonitor(
        datasource=datasource,
        notifier=notifier,
        watchlist_repo=watchlist_repo,
        alert_repo=alert_repo,
        allowed_chat_ids=allowed_chat_ids,
    )

    # ── Scheduler ────────────────────────────────────────────────────────────
    scheduler = setup_scheduler(monitor)

    # ── Bot application ──────────────────────────────────────────────────────
    deps = {
        "datasource": datasource,
        "notifier": notifier,
        "watchlist_repo": watchlist_repo,
        "alert_repo": alert_repo,
        "signal_repo": signal_repo,
        "cache_repo": cache_repo,
        "trade_repo": trade_repo,
        "settings": settings,
    }
    app = build_application(
        token=settings.telegram_bot_token,
        settings=settings,
        deps=deps,
    )

    scheduler.start()
    logger.info("scheduler_started")

    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()  # type: ignore[union-attr]
        logger.info("bot_started env=%s", settings.kiwoom_env)
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("shutdown_requested")
    finally:
        scheduler.shutdown(wait=False)
        await datasource.close()
        if app.updater:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
        conn.close()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    from typing import Any
    from PyQt5.QtWidgets import QApplication
    from pykiwoom.kiwoom import Kiwoom

    # QApplication + Kiwoom은 반드시 메인 스레드에서 생성 (QAxWidget 제약)
    _qt_app = QApplication.instance() or QApplication(sys.argv)
    logger.info("kiwoom_login_start  — HTS 로그인 팝업이 나타납니다")
    _kiwoom = Kiwoom()
    _kiwoom.CommConnect(block=True)

    asyncio.run(main(_kiwoom))
