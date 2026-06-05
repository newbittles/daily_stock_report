"""미국장 장중 리포트 — 평일 23:50 (KST), 미국 정규장 개장 직후.

us_premarket과 동일 구조(직전 정규장 마감 일봉으로 ABCD 스크리닝) + '현재 장중' 시세/
등락률 오버레이. 강세/약세 섹터·빅테크·ABCD 추천(3개씩)을 장중 기준으로 보여준다.

설계(사용자 2026-06-05): 마감 전 리포트 대신 개장 직후 장중 리포트. 장중이라 값이 흔들리는
점(일봉 미확정·거래량 누적중)은 사용자가 감안하기로 함 → '🕒 장중 잠정' 라벨. 종목 표시는
실시간(장중) 등락률만(전일 등락률 생략). ABCD 추천 전략별 3개 제한.
"""
from __future__ import annotations

import logging

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def run_us_intraday(
    *, do_telegram: bool = True, do_publish: bool = True, force: bool = False,
) -> MarketSnapshot | None:
    """미국장 장중 리포트 생성·웹발행·발송. 주말(US 미개장)이면 None(스킵).

    공용 러너(run_us_report)에 장중 차별점만 주입: 현재 장중 시세 오버레이(프리장 TOP5 없음).
    """
    from src.market_report.pipeline import _overlay_intraday
    from src.market_report.us_report_runner import run_us_report

    return await run_us_report(
        "us_intraday", _overlay_intraday,
        do_telegram=do_telegram, do_publish=do_publish, force=force,
    )


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
