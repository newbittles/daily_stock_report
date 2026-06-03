"""장중 리포트(평일 12:00) — 텔레그램 전용.

내용: 오늘 지수 / 투자자 수급(전일대비) / 강세테마 / 핫 종목 / 전날 추천 Top3 현황.
웹·차트·publish 없음(가벼운 장중 알림). 데이터는 collect_snapshot("midday") 재사용 +
전날 top3는 top3_status로 현재가 조회.

design 결정(2026-06-04 사용자): Q1=추천가대비+오늘등락 둘 다, Q2=텔레그램만(모바일 가독성).
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def run_midday(*, do_telegram: bool = True, force: bool = False) -> MarketSnapshot | None:
    """장중 리포트 생성·발송. 휴장일이면 None(스킵)."""
    from src.market_report.market_calendar import is_kr_market_open_today

    if not force and not await is_kr_market_open_today():
        logger.info("midday_skip — 휴장일")
        return None

    from src.market_report.pipeline import collect_snapshot

    snap = await collect_snapshot("midday")

    # 전날 추천 Top3 현황 (추천가 대비 + 오늘 등락)
    try:
        from src.config.settings import get_settings
        from src.datasource.kis.adapter import KisAdapter
        from src.market_report.top3_status import (
            fetch_prev_top3_status,
            find_prev_top3,
        )

        today = snap.generated_at.strftime("%Y-%m-%d")
        prev = find_prev_top3(today)
        if prev:
            date, picks = prev
            s = get_settings()
            adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
            snap.prev_top3_status = await fetch_prev_top3_status(picks, adapter)
            snap.prev_top3_date = date
            logger.info("midday_prev_top3 date=%s count=%d", date, len(snap.prev_top3_status))
        else:
            logger.info("midday_prev_top3_none — 직전 거래일 top3 파일 없음")
    except Exception as exc:  # noqa: BLE001
        logger.warning("midday_prev_top3_failed error=%s", exc)

    # 강세 테마는 collect_snapshot의 snap.top_themes(naver, 빠름)로 충분.
    # 주도테마(judal) 역인덱스는 정오 최초 크롤이 무거워 midday에선 생략(가벼운 알림 유지).

    # AI 장중 코멘트 (실패 시 결정론 폴백)
    try:
        from src.market_report.analyzer import summarize_midday
        snap.summary = await summarize_midday(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("midday_summary_failed error=%s", exc)

    logger.info("midday_ready kospi=%s themes=%d hot=%d prev_top3=%d",
                snap.kospi.value if snap.kospi else "fail",
                len(snap.top_themes), len(snap.top_gainers or []),
                len(snap.prev_top3_status))

    if do_telegram:
        try:
            from src.market_report.telegram_notify import send_report
            await send_report(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("midday_telegram_failed error=%s", exc)

    return snap


if __name__ == "__main__":
    import argparse
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="장중 리포트(정오)")
    ap.add_argument("--no-tg", action="store_true", help="텔레그램 발송 스킵")
    ap.add_argument("--force", action="store_true", help="휴장일 스킵 무시")
    args = ap.parse_args()
    snap = asyncio.run(run_midday(do_telegram=not args.no_tg, force=args.force))
    print("✅ 장중 리포트 완료" if snap else "휴장일 — 스킵")
