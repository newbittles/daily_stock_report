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


async def _warm_us_cache_job() -> None:
    """미국 ohlcv 캐시 선제 워밍 (#499) — 리포트 시각 전 1회 다운로드로 14분 지연 제거.

    화~토 06:00(06:30/07:00/19:00 대비) + 월 18:30(월 19:00 대비). 발송·웹 없음."""
    logger.info("warm_us_cache_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.pipeline import warm_us_cache
        await warm_us_cache()
        logger.info("warm_us_cache_done")
    except Exception as exc:  # noqa: BLE001
        logger.warning("warm_us_cache_failed error=%s", exc)


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


def _us_is_dst() -> bool:
    """현재 미국 동부가 서머타임(DST)인가 — 미국 개장 시각이 KST로 22:30(섬머)/23:30(일반)."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo
    try:
        return _dt.now(ZoneInfo("America/New_York")).dst() != _td(0)
    except Exception:  # noqa: BLE001
        return False


async def _us_intraday_job(summer: bool | None = None) -> None:
    """미국장 장중 리포트 (개장 직후). DST 맞춰 22:40(섬머)/23:40(일반) 중 1회만(사용자 2026-06-05).

    summer 지정 시 현재 미국 DST와 일치할 때만 실행 → 두 잡 중 정확히 하나만 발행.
    """
    if summer is not None and summer != _us_is_dst():
        logger.info("us_intraday_skip — DST 불일치(summer=%s, dst=%s)", summer, _us_is_dst())
        return
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


async def _kr_morning_job(mode: str) -> None:
    """한국장 프리(08:05)/장초(09:15) 리포트 (사용자 #404)."""
    logger.info("%s_job_start now=%s", mode, datetime.now().isoformat())
    try:
        from src.market_report.kr_morning import run_kr_morning
        snap = await run_kr_morning(mode)
        logger.info("%s_job_done sent=%s", mode, snap is not None)
    except Exception as exc:
        logger.exception("%s_job_failed error=%s", mode, exc)


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


async def _coin_job() -> None:
    """코인 시세 리포트 (매일 17:00, 주말 포함 — 코인은 무휴장이라 휴장스킵 없음)."""
    logger.info("coin_job_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.coin_report import run_coin_report
        res = await run_coin_report()
        logger.info("coin_job_done result=%s", res)
    except Exception as exc:
        logger.exception("coin_job_failed error=%s", exc)


async def _report_audit_job() -> None:
    """리포트 일관성 점검 (평일 14:00 KST, 잡 없는 빈 시간) — 드리프트 발견 시 텔레그램 '확인 요청' 알림.

    KR/US 리포트 간 '있어야 할 섹션 누락'을 매트릭스로 점검(의도된 시점별 차이는 오탐 제외).
    자동 코드수정 X — 알림만(사용자 2026-06-10)."""
    logger.info("report_audit_job_start now=%s", datetime.now().isoformat())
    try:
        from src.market_report.report_audit import run_report_audit
        findings = await run_report_audit(always_notify=True)
        logger.info("report_audit_job_done findings=%d", len(findings))
    except Exception as exc:
        logger.exception("report_audit_job_failed error=%s", exc)


def build_scheduler() -> AsyncIOScheduler:
    """평일 14:40 / 16:30 트리거 등록."""
    scheduler = AsyncIOScheduler(timezone=KST)

    scheduler.add_job(
        _job, CronTrigger(day_of_week="mon-fri", hour=14, minute=50, timezone=KST),
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
    # 미국 ohlcv 캐시 워밍 — 리포트 14분 지연 제거(#499). 화~토 06:00(아침 리포트+당일 19:00),
    # 월 18:30(월 19:00 us_premarket; 화~금 19:00은 06:00 워밍이 같은 날 캐시로 커버).
    scheduler.add_job(
        _warm_us_cache_job, CronTrigger(day_of_week="tue-sat", hour=6, minute=0, timezone=KST),
        id="warm_us_cache_am", replace_existing=True, misfire_grace_time=1200,
    )
    scheduler.add_job(
        _warm_us_cache_job, CronTrigger(day_of_week="mon", hour=18, minute=30, timezone=KST),
        id="warm_us_cache_pm", replace_existing=True, misfire_grace_time=1200,
    )

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
    # 한국장 프리(08:05, NXT 시초) + 장초(09:15, 정규장 시초) 리포트 (사용자 #404)
    scheduler.add_job(
        _kr_morning_job, CronTrigger(day_of_week="mon-fri", hour=8, minute=5, timezone=KST),
        args=["kr_premarket"], id="report_kr_premarket", replace_existing=True, misfire_grace_time=600,
    )
    scheduler.add_job(
        _kr_morning_job, CronTrigger(day_of_week="mon-fri", hour=9, minute=15, timezone=KST),
        args=["kr_open"], id="report_kr_open", replace_existing=True, misfire_grace_time=600,
    )
    # 미국장 장전(프리장) 리포트 (평일 19:00 — 이른 프리장)
    scheduler.add_job(
        _us_premarket_job, CronTrigger(day_of_week="mon-fri", hour=19, minute=0, timezone=KST),
        id="report_us_premarket", replace_existing=True, misfire_grace_time=900,
    )
    # 미국장 장전(프리장) 리포트 2차 (평일 21:50 — 개장 임박, 더 정확한 프리장 시세, 사용자 2026-06-05)
    scheduler.add_job(
        _us_premarket_job, CronTrigger(day_of_week="mon-fri", hour=21, minute=50, timezone=KST),
        id="report_us_premarket_late", replace_existing=True, misfire_grace_time=900,
    )
    # 미국장 장중 리포트 — 개장 직후(변동 큼), DST 맞춰 1회: 섬머 22:40 / 일반 23:40 (사용자 2026-06-05)
    scheduler.add_job(
        _us_intraday_job, CronTrigger(day_of_week="mon-fri", hour=22, minute=40, timezone=KST),
        args=[True], id="report_us_intraday_dst", replace_existing=True, misfire_grace_time=900,
    )
    scheduler.add_job(
        _us_intraday_job, CronTrigger(day_of_week="mon-fri", hour=23, minute=40, timezone=KST),
        args=[False], id="report_us_intraday_std", replace_existing=True, misfire_grace_time=900,
    )
    # 미국 애프터장 리뷰 — 평일 13:00 KST (한국 오후, 미국 마감+애프터 종목변동 체크, 사용자 2026-06-10).
    # 구조는 미국 마감(us_morning)과 동일, 별도 파일(us-after). us 블록 자체 신선도 스킵 보유.
    scheduler.add_job(
        _job, CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=KST),
        args=["us_afterhours"], id="report_us_afterhours", replace_existing=True, misfire_grace_time=900,
    )
    # 코인 시세 리포트 — 매일 17:00, 주말 포함 (day_of_week 미지정 = '*', 사용자 2026-06-07)
    scheduler.add_job(
        _coin_job, CronTrigger(hour=17, minute=0, timezone=KST),
        id="report_coin", replace_existing=True, misfire_grace_time=900,
    )
    # 코인 시세 리포트 2차 — 매일 08:30, 주말 포함 (아침 추가 발송, 사용자 2026-06-09)
    scheduler.add_job(
        _coin_job, CronTrigger(hour=8, minute=30, timezone=KST),
        id="report_coin_am", replace_existing=True, misfire_grace_time=900,
    )
    # 리포트 일관성 점검 — 평일 14:00 KST (잡 없는 빈 시간·새벽 회피, 사용자 2026-06-10).
    # KR/US 리포트 섹션 드리프트 점검 → 발견 시 텔레그램 '확인 요청' 알림(자동수정 X).
    scheduler.add_job(
        _report_audit_job, CronTrigger(day_of_week="mon-fri", hour=14, minute=0, timezone=KST),
        id="report_audit", replace_existing=True, misfire_grace_time=1800,
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
                                           "midday", "uspre", "usmid", "usafter", "coin", "audit"],
                        help="등록된 잡 1회 즉시 실행 후 종료")
    args = parser.parse_args()

    if args.once == "audit":
        asyncio.run(_report_audit_job())
    elif args.once == "coin":
        asyncio.run(_coin_job())
    elif args.once == "holdings":
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
        mode = {"pre": "pre_close", "post": "post_close", "us": "us_morning",
                "usafter": "us_afterhours"}[args.once]
        asyncio.run(_job(mode))
    else:
        asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    sys.exit(main())
