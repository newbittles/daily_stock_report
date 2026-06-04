"""미국장 장전(프리장) 리포트 — 평일 저녁 19:00 (KST).

미국 프리장 시간대(한국 17:00~23:30)에 발송. 직전 정규장 마감 일봉으로 ABCD 스크리닝
(us_morning과 동일 하이브리드 유니버스+필터) → 프리장 시세/등락률 오버레이 →
프리장에서 강한 종목·테마·추천주. 웹 발행(-us-pre.html) + 텔레그램.

design 결정(2026-06-04 사용자): 발송 저녁 7시, Q2=마감ABCD+프리장오버레이.
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def run_us_premarket(
    *, do_telegram: bool = True, do_publish: bool = True, force: bool = False,
) -> MarketSnapshot | None:
    """미국장 장전 리포트 생성·웹발행·발송. 주말이면 None(스킵)."""
    if not force and datetime.now().weekday() >= 5:  # 토(5)·일(6)
        logger.info("us_premarket_skip — 주말")
        return None

    from src.market_report.pipeline import (
        _collect_us_screening,
        _overlay_premarket,
        _render_candles,
        collect_us_snapshot,
    )

    snap = await collect_us_snapshot()       # 지수/섹터(직전 마감 — 맥락)
    snap.mode = "us_premarket"               # type: ignore[assignment]
    snap.generated_at = datetime.now()

    try:
        await _collect_us_screening(snap)    # 하이브리드 ABCD (마감 일봉)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_premarket_screening_failed error=%s", exc)
    try:
        await _overlay_premarket(snap)       # 프리장 시세/등락률 오버레이
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_premarket_overlay_failed error=%s", exc)

    try:
        from src.market_report.analyzer import analyze
        snap = await analyze(snap)           # AI (미국 컨텍스트)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_premarket_analyze_failed error=%s", exc)

    try:
        await _render_candles(snap)          # 지수 차트 (us 캔들)
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_premarket_candles_failed error=%s", exc)

    logger.info("us_premarket_ready top3=%d groups=%d",
                len(snap.us_top3 or []), len(snap.us_screen_groups or []))

    try:
        from src.market_report.render import render_report
        render_report(snap)
    except Exception as exc:  # noqa: BLE001
        logger.error("us_premarket_render_failed error=%s", exc)
    if do_publish:
        try:
            from src.market_report.publisher import publish
            publish(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("us_premarket_publish_failed error=%s", exc)
    if do_telegram:
        try:
            from src.market_report.telegram_notify import send_report
            await send_report(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("us_premarket_telegram_failed error=%s", exc)

    return snap


if __name__ == "__main__":
    import argparse
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="미국장 장전 리포트")
    ap.add_argument("--no-tg", action="store_true", help="텔레그램 발송 스킵")
    ap.add_argument("--no-publish", action="store_true", help="웹 발행 스킵")
    ap.add_argument("--force", action="store_true", help="주말 스킵 무시")
    args = ap.parse_args()
    snap = asyncio.run(run_us_premarket(
        do_telegram=not args.no_tg, do_publish=not args.no_publish, force=args.force))
    print("✅ 미국장 장전 리포트 완료" if snap else "주말 — 스킵")
