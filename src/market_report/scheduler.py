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
    from src.market_report.market_calendar import is_kr_market_open_today
    if not await is_kr_market_open_today():
        logger.info("holdings_job_skip — 휴장일")
        return
    try:
        from src.alerts.holdings_report import run_holdings_report
        rows = await run_holdings_report()
        logger.info("holdings_job_done count=%d", len(rows))
    except Exception as exc:
        logger.exception("holdings_job_failed error=%s", exc)


async def _us_premarket_job() -> None:
    """미국장 장전 리포트 (평일 19:00 — 미국 프리장 시세 + 마감기준 ABCD). 웹+텔레그램."""
    logger.info("us_premarket_job_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.us_premarket import run_us_premarket
        snap = await run_us_premarket()
        logger.info("us_premarket_job_done sent=%s", snap is not None)
    except Exception as exc:
        logger.exception("us_premarket_job_failed error=%s", exc)


async def _midday_job() -> None:
    """장중 리포트 (평일 12:00) — 지수·수급·강세테마·핫종목·전날 top3 현황. 텔레그램 전용."""
    logger.info("midday_job_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.midday import run_midday
        snap = await run_midday()
        logger.info("midday_job_done sent=%s", snap is not None)
    except Exception as exc:
        logger.exception("midday_job_failed error=%s", exc)


async def _dashboard_job() -> None:
    """마감 후 전략 스크린 대시보드 갱신 + GitHub Pages 게시."""
    logger.info("dashboard_job_start now=%s", datetime.now().isoformat())
    from src.market_report.market_calendar import is_kr_market_open_today
    if not await is_kr_market_open_today():
        logger.info("dashboard_job_skip — 휴장일")
        return
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
    # 미국장 아침 요약 (07:00 — 미국장 마감 후[서머 05:00/겨울 06:00], 국장 시작 전)
    # 06:30은 겨울철 마감 30분 후라 FDR/yfinance 일봉 미갱신(전일 데이터) 위험 → 07:00 채택.
    scheduler.add_job(
        _job, CronTrigger(day_of_week="tue-sat", hour=7, minute=0, timezone=KST),
        args=["us_morning"], id="report_us_morning", replace_existing=True,
        misfire_grace_time=900,
    )
    # 장중 리포트 (평일 11:40 — 오전장 흐름·전날 추천 top3 현황)
    scheduler.add_job(
        _midday_job, CronTrigger(day_of_week="mon-fri", hour=11, minute=40, timezone=KST),
        id="report_midday", replace_existing=True, misfire_grace_time=600,
    )
    # 미국장 장전(프리장) 리포트 (평일 19:00 — 미국 프리장 시간대)
    scheduler.add_job(
        _us_premarket_job, CronTrigger(day_of_week="mon-fri", hour=19, minute=0, timezone=KST),
        id="report_us_premarket", replace_existing=True, misfire_grace_time=900,
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
    parser.add_argument("--once", choices=["pre", "post", "us", "holdings", "dashboard",
                                           "midday", "uspre"],
                        help="등록된 잡 1회 즉시 실행 후 종료")
    args = parser.parse_args()

    if args.once == "holdings":
        asyncio.run(_holdings_job())
    elif args.once == "dashboard":
        asyncio.run(_dashboard_job())
    elif args.once == "midday":
        asyncio.run(_midday_job())
    elif args.once == "uspre":
        asyncio.run(_us_premarket_job())
    elif args.once:
        mode = {"pre": "pre_close", "post": "post_close", "us": "us_morning"}[args.once]
        asyncio.run(_job(mode))
    else:
        asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    sys.exit(main())
