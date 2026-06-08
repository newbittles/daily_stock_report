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


async def run_midday(
    *, do_telegram: bool = True, do_publish: bool = True, force: bool = False,
) -> MarketSnapshot | None:
    """장중 리포트 생성·웹발행·발송. 휴장일이면 None(스킵)."""
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
        from src.market_report.pipeline import collect_hot_stocks
        from src.market_report.strategy_section import collect_holdings_status
        from src.market_report.top3_status import (
            fetch_prev_top3_status,
            find_prev_candidates,
            find_prev_top3,
        )

        s = get_settings()
        adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

        # 핫종목 (거래대금 상위 + 시총 3000억 필터 + 거래대금 전일대비·순매수 연속일·소속테마)
        try:
            snap.hot_stocks = await collect_hot_stocks(snap, adapter, top=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("midday_hot_stocks_failed error=%s", exc)

        # 전날 추천 Top3 현황
        today = snap.generated_at.strftime("%Y-%m-%d")
        prev = find_prev_top3(today)
        if prev:
            date, picks = prev
            snap.prev_top3_status = await fetch_prev_top3_status(picks, adapter)
            snap.prev_top3_date = date
            logger.info("midday_prev_top3 date=%s count=%d", date, len(snap.prev_top3_status))
        else:
            logger.info("midday_prev_top3_none — 직전 거래일 top3 파일 없음")

        # 전일 종가베팅 후보 5선 현황 (#474)
        pc = find_prev_candidates(today)
        if pc:
            cdate, cpicks = pc
            snap.prev_candidates_status = await fetch_prev_top3_status(cpicks, adapter)
            snap.prev_candidates_date = cdate

        # 보유종목 상태 (#474, holdings.yaml/계좌)
        try:
            snap.holdings_status = await collect_holdings_status(adapter)
        except Exception as exc:  # noqa: BLE001
            logger.warning("midday_holdings_failed error=%s", exc)

        # 장중 분봉 흐름 주입 — 전일Top3·종가베팅·보유·핫종목 각 줄에 추세 한 줄(#473/#474)
        try:
            from src.datasource.intraday_flow import inject_flows
            for rows in (snap.prev_top3_status, snap.prev_candidates_status,
                         snap.holdings_status, snap.hot_stocks):
                if rows:
                    await inject_flows(adapter, rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("midday_intraday_flow_failed error=%s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("midday_kis_failed error=%s", exc)

    # 미국 야간 시세(나스닥 선물 + M7) — 한국 리포트 최상단(#476)
    try:
        from src.datasource.us.overnight import fetch_us_overnight
        snap.us_overnight = await fetch_us_overnight()
    except Exception as exc:  # noqa: BLE001
        logger.warning("midday_overnight_failed error=%s", exc)

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
                len(snap.top_themes), len(snap.hot_stocks or []),
                len(snap.prev_top3_status))

    # 지수 차트 (마감전/후 포맷과 동일하게)
    try:
        from src.market_report.pipeline import _render_candles
        await _render_candles(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("midday_candles_failed error=%s", exc)

    # 웹 렌더 + GitHub Pages 발행
    try:
        from src.market_report.render import render_report
        render_report(snap)
    except Exception as exc:  # noqa: BLE001
        logger.error("midday_render_failed error=%s", exc)
    if do_publish:
        try:
            from src.market_report.publisher import publish
            publish(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("midday_publish_failed error=%s", exc)

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
    ap.add_argument("--no-publish", action="store_true", help="웹 발행(git push) 스킵")
    ap.add_argument("--force", action="store_true", help="휴장일 스킵 무시")
    args = ap.parse_args()
    snap = asyncio.run(run_midday(
        do_telegram=not args.no_tg, do_publish=not args.no_publish, force=args.force))
    print("✅ 장중 리포트 완료" if snap else "휴장일 — 스킵")
