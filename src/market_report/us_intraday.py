"""미국장 장중 리포트 — 평일 23:50 (KST), 미국 정규장 개장 직후.

us_premarket과 동일 구조(직전 정규장 마감 일봉으로 ABCD 스크리닝) + '현재 장중' 시세/
등락률 오버레이. 강세/약세 섹터·빅테크·ABCD 추천(3개씩)을 장중 기준으로 보여준다.

설계(사용자 2026-06-05): 마감 전 리포트 대신 개장 직후 장중 리포트. 장중이라 값이 흔들리는
점(일봉 미확정·거래량 누적중)은 사용자가 감안하기로 함 → '🕒 장중 잠정' 라벨. 종목 표시는
실시간(장중) 등락률만(전일 등락률 생략). ABCD 추천 전략별 3개 제한.
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def run_us_intraday(
    *, do_telegram: bool = True, do_publish: bool = True, force: bool = False,
) -> MarketSnapshot | None:
    """미국장 장중 리포트 생성·웹발행·발송. 주말(US 미개장)이면 None(스킵)."""
    if not force and datetime.now().weekday() >= 5:  # 토(5)·일(6) KST = US 미개장
        logger.info("us_intraday_skip — 주말")
        return None

    from src.market_report.pipeline import (
        _attach_kr_netbuy_to_picks,
        _collect_sector_leaders,
        _collect_us_screening,
        _overlay_intraday,
        _render_candles,
        collect_us_snapshot,
    )

    snap = await collect_us_snapshot()       # 지수/섹터(직전 마감 — 맥락)
    snap.mode = "us_intraday"                # type: ignore[assignment]
    snap.generated_at = datetime.now()

    try:
        await _collect_us_screening(snap, per_group=3)  # 하이브리드 ABCD (직전 마감 일봉), 3개씩
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_intraday_screening_failed error=%s", exc)
    try:
        await _overlay_intraday(snap)        # 현재 장중 시세/등락률 오버레이
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_intraday_overlay_failed error=%s", exc)
    try:
        await _collect_sector_leaders(snap)  # 주요종목 = 강세4+약세4 섹터 대장
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_intraday_sector_leaders_failed error=%s", exc)
    try:
        await _attach_kr_netbuy_to_picks(snap)  # 픽별 서학개미 순매수금액(전일+5일)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_intraday_kr_netbuy_failed error=%s", exc)

    try:
        from src.market_report.analyzer import analyze
        snap = await analyze(snap)           # AI (미국 컨텍스트)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_intraday_analyze_failed error=%s", exc)

    try:
        await _render_candles(snap)          # 지수 차트 (us 캔들)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_intraday_candles_failed error=%s", exc)

    logger.info("us_intraday_ready top3=%d groups=%d",
                len(snap.us_top3 or []), len(snap.us_screen_groups or []))

    try:
        from src.market_report.render import render_report
        render_report(snap)
    except Exception as exc:  # noqa: BLE001
        logger.error("us_intraday_render_failed error=%s", exc)
    if do_publish:
        try:
            from src.market_report.publisher import publish
            publish(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("us_intraday_publish_failed error=%s", exc)
    if do_telegram:
        try:
            from src.market_report.telegram_notify import send_report
            await send_report(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("us_intraday_telegram_failed error=%s", exc)

    return snap


if __name__ == "__main__":
    import argparse
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="미국장 장중 리포트")
    ap.add_argument("--no-tg", action="store_true", help="텔레그램 발송 스킵")
    ap.add_argument("--no-publish", action="store_true", help="웹 발행 스킵")
    ap.add_argument("--force", action="store_true", help="주말 스킵 무시")
    args = ap.parse_args()
    snap = asyncio.run(run_us_intraday(
        do_telegram=not args.no_tg, do_publish=not args.no_publish, force=args.force))
    print("✅ 미국장 장중 리포트 완료" if snap else "주말 — 스킵")
