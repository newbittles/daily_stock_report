"""Entry point — KIS REST + Telegram bot + APScheduler.

KIS Open API(REST) 기반이라 OCX/QApplication/32비트 불필요.
App Key/Secret만 .env에 있으면 헤드리스로 기동.

실행:
    python main.py
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from src.alerts.monitor import WatchlistMonitor
from src.bot.router import build_application
from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.notify.telegram.adapter import TelegramNotifier
from src.scheduler.jobs import setup_scheduler
from src.storage.db import get_connection, init_db
from src.storage.repos import (
    AlertHistoryRepo,
    AnalysisCacheRepo,
    SignalLogRepo,
    TradeHistoryRepo,
    WatchlistRepo,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()

    # ── Storage ──────────────────────────────────────────────────────────────
    conn = get_connection(settings.db_path)
    init_db(conn)
    watchlist_repo = WatchlistRepo(conn)
    alert_repo = AlertHistoryRepo(conn)
    signal_repo = SignalLogRepo(conn)
    cache_repo = AnalysisCacheRepo(conn)
    trade_repo = TradeHistoryRepo(conn)

    # ── KIS REST adapter ───────────────────────────────────────────────────────
    if not settings.kis_app_key or not settings.kis_app_secret:
        logger.error("KIS_APP_KEY/KIS_APP_SECRET 미설정 — .env를 확인하세요.")
        raise SystemExit(1)

    datasource = KisAdapter(
        app_key=settings.kis_app_key,
        app_secret=settings.kis_app_secret,
        account_no=settings.kis_account_no,
        env=settings.kis_env,
    )
    logger.info("kis_adapter_ready env=%s", settings.kis_env)

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
        logger.info("bot_started env=%s", settings.kis_env)
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
    asyncio.run(main())
