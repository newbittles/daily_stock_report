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


def _us_data_fresh_sync() -> bool:
    """방금 마감된 미국 세션이 데이터 피드에 반영됐는지(yfinance ^GSPC 최신 일봉 == ET 세션일).

    06:30 조기발행 게이트용. 겨울 마감(06:00 KST) 직후엔 일봉 미갱신이 흔해 False가 나올 수 있고,
    그러면 06:30은 스킵하고 07:00 안전망이 발행한다(사용자 2026-06-05). 오류 시 보수적으로 False."""
    try:
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from zoneinfo import ZoneInfo

        import yfinance as yf

        df = yf.Ticker("^GSPC").history(period="7d")
        if df is None or df.empty:
            return False
        last = df.index[-1].date()
        et = _dt.now(ZoneInfo("America/New_York")).date()
        # ET 기준 직전 거래일(주말이면 금요일)이 마지막 일봉으로 들어왔으면 신선
        expected = et
        while expected.weekday() >= 5:  # 토/일 → 금
            expected -= _td(days=1)
        return last >= expected
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_data_fresh_check_failed error=%s", exc)
        return False


async def _us_morning_job(require_fresh: bool) -> None:
    """미국장 마감 리포트 — 06:30(require_fresh) 조기 + 07:00 안전망. 중복발행 방지(사용자 2026-06-05).

    require_fresh=True(06:30): 오늘 미국 세션 데이터가 확보된 경우에만 발행(아니면 스킵→07:00).
    require_fresh=False(07:00): 이미 오늘 발행됐으면 스킵, 아니면 발행."""
    from src.market_report.models import MarketSnapshot
    from src.market_report.render import report_path

    probe = MarketSnapshot(mode="us_morning", generated_at=datetime.now())
    if report_path(probe).exists():
        logger.info("us_morning_job_skip — 오늘 이미 발행됨(require_fresh=%s)", require_fresh)
        return
    if require_fresh and not await asyncio.to_thread(_us_data_fresh_sync):
        logger.info("us_morning_job_defer — 마감데이터 미확보, 07:00 안전망으로 이월")
        return
    await _job("us_morning")


async def _us_intraday_job() -> None:
    """미국장 장중 리포트 (평일 23:50 — 개장 직후 장중 시세 + 마감기준 ABCD 3개). 웹+텔레그램."""
    logger.info("us_intraday_job_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.us_intraday import run_us_intraday
        snap = await run_us_intraday()
        logger.info("us_intraday_job_done sent=%s", snap is not None)
    except Exception as exc:
        logger.exception("us_intraday_job_failed error=%s", exc)


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
    scheduler.add_job(  # 마감 후 리포트 16:00 (사용자 2026-06-05, 내일부터). 장마감 15:30 + 30분.
        _job, CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=KST),
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
    # 미국장 아침 요약 — 06:30 조기(데이터 확보 시) + 07:00 안전망(중복발행 방지, 사용자 2026-06-05).
    # 06:30은 겨울철 마감 30분 후라 일봉 미갱신 위험 → _us_morning_job이 신선도 확인 후 발행/이월.
    scheduler.add_job(
        _us_morning_job, CronTrigger(day_of_week="tue-sat", hour=6, minute=30, timezone=KST),
        args=[True], id="report_us_morning_early", replace_existing=True, misfire_grace_time=900,
    )
    scheduler.add_job(
        _us_morning_job, CronTrigger(day_of_week="tue-sat", hour=7, minute=0, timezone=KST),
        args=[False], id="report_us_morning", replace_existing=True, misfire_grace_time=900,
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
    # 미국장 장중 리포트 (평일 23:50 — 미국 개장 직후, 장중 시세 + 마감기준 ABCD 3개)
    scheduler.add_job(
        _us_intraday_job, CronTrigger(day_of_week="mon-fri", hour=23, minute=50, timezone=KST),
        id="report_us_intraday", replace_existing=True, misfire_grace_time=900,
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
                                           "midday", "uspre", "usmid"],
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
    elif args.once == "usmid":
        asyncio.run(_us_intraday_job())
    elif args.once:
        mode = {"pre": "pre_close", "post": "post_close", "us": "us_morning"}[args.once]
        asyncio.run(_job(mode))
    else:
        asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    sys.exit(main())
