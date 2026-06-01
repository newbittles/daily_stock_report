"""일일 리포트 스케줄러 — APScheduler cron 잡.

평일(월~금) 한국시간 기준:
  - 14:40 → 마감 전 리포트 (종가베팅 후보)
  - 16:30 → 마감 후 리포트 (시장 정리)

실행:
  python -m src.market_report.scheduler            # foreground 데몬
  python -m src.market_report.scheduler --once pre # 1회 즉시 실행
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.market_report.pipeline import run_full

logger = logging.getLogger(__name__)

KST = "Asia/Seoul"


async def _job(mode: str) -> None:
    logger.info("scheduled_job_start mode=%s now=%s", mode, datetime.now().isoformat())
    try:
        await run_full(mode)  # type: ignore[arg-type]
        logger.info("scheduled_job_done mode=%s", mode)
    except Exception as exc:
        logger.exception("scheduled_job_failed mode=%s error=%s", mode, exc)


async def _holdings_job() -> None:
    """마감 후 보유종목 A/B/C 상태 리포트 (홀딩/손절/추가매수)."""
    logger.info("holdings_job_start now=%s", datetime.now().isoformat())
    try:
        from src.alerts.holdings_report import run_holdings_report
        rows = await run_holdings_report()
        logger.info("holdings_job_done count=%d", len(rows))
    except Exception as exc:
        logger.exception("holdings_job_failed error=%s", exc)


async def _dashboard_job() -> None:
    """마감 후 전략 스크린 대시보드 갱신 + GitHub Pages 게시."""
    logger.info("dashboard_job_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.screen_dashboard import run_dashboard_job
        path = await run_dashboard_job(days_back=12, do_publish=True)
        logger.info("dashboard_job_done path=%s", path)
    except Exception as exc:
        logger.exception("dashboard_job_failed error=%s", exc)


def build_scheduler() -> AsyncIOScheduler:
    """평일 14:40 / 16:30 트리거 등록."""
    scheduler = AsyncIOScheduler(timezone=KST)

    scheduler.add_job(
        _job, CronTrigger(day_of_week="mon-fri", hour=14, minute=40, timezone=KST),
        args=["pre_close"], id="report_pre", replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _job, CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=KST),
        args=["post_close"], id="report_post", replace_existing=True,
        misfire_grace_time=600,
    )
    # 마감 후 보유종목 상태 리포트 (시장 리포트 직후 16:35 — 종가 확정 데이터)
    scheduler.add_job(
        _holdings_job, CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=KST),
        id="holdings_report", replace_existing=True, misfire_grace_time=600,
    )
    # 마감 후 전략 스크린 대시보드 갱신 + 게시 (16:40)
    scheduler.add_job(
        _dashboard_job, CronTrigger(day_of_week="mon-fri", hour=16, minute=40, timezone=KST),
        id="screen_dashboard", replace_existing=True, misfire_grace_time=900,
    )
    return scheduler


async def run_forever() -> None:
    scheduler = build_scheduler()
    scheduler.start()

    logger.info("scheduler_started — 평일 14:40 (마감 전) + 16:30 (마감 후)")
    for job in scheduler.get_jobs():
        logger.info("  job=%s next_run=%s", job.id, job.next_run_time)

    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        logger.info("shutdown_signal")
        stop.set()

    try:
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
    except (ValueError, AttributeError):
        pass  # Windows에서 일부 시그널 미지원

    await stop.wait()
    scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Daily report scheduler")
    parser.add_argument("--once", choices=["pre", "post", "holdings", "dashboard"],
                        help="등록된 잡 1회 즉시 실행 후 종료")
    args = parser.parse_args()

    if args.once == "holdings":
        asyncio.run(_holdings_job())
    elif args.once == "dashboard":
        asyncio.run(_dashboard_job())
    elif args.once:
        mode = "pre_close" if args.once == "pre" else "post_close"
        asyncio.run(_job(mode))
    else:
        asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    sys.exit(main())
