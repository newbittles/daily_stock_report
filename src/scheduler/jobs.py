from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.alerts.monitor import WatchlistMonitor

logger = logging.getLogger(__name__)

# Watchlist check interval during market hours (minutes)
MONITOR_INTERVAL_MIN = 5


def setup_scheduler(monitor: WatchlistMonitor) -> AsyncIOScheduler:
    """Registers recurring jobs and returns the configured scheduler.

    Call scheduler.start() after this returns, then scheduler.shutdown() on exit.
    """
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        monitor.run_once,
        trigger=IntervalTrigger(minutes=MONITOR_INTERVAL_MIN),
        id="watchlist_monitor",
        name="Watchlist condition check",
        max_instances=1,      # prevent overlap if a run is slow
        replace_existing=True,
        misfire_grace_time=60,
    )

    logger.info("scheduler_registered jobs=%d", len(scheduler.get_jobs()))
    return scheduler
