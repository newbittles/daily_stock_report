"""미국 리포트 공용 러너 — us_premarket / us_intraday 공통 파이프라인.

장전(프리장)·장중 리포트는 '오버레이(프리장 vs 장중 시세)'와 '추가 단계(프리장 급등 TOP5)'만
다르고 수집→분석→차트→발행→발송 흐름은 동일했다(중복). 2026-06-06 리팩토링으로 run_us_report
하나로 통합하고, 모드별 차별점(overlay, extra_steps)은 호출부에서 명시적으로 주입한다.

⚠️ 동작 불변: 기존 us_premarket.py / us_intraday.py와 호출 순서·예외처리·로그 라벨이 동일하다
(라벨은 "{mode}_<step>_failed" 형식으로 기존 문자열을 그대로 재현). 미국 마감(us_morning)은
별도 파이프라인(run_full)이라 여기 포함하지 않는다(차별점 — 명시 분리).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def run_us_report(
    mode: str,
    overlay: Callable[[MarketSnapshot], Awaitable[None]],
    *,
    extra_steps: Callable[[MarketSnapshot], None] | None = None,
    do_telegram: bool = True,
    do_publish: bool = True,
    force: bool = False,
) -> MarketSnapshot | None:
    """미국 장전/장중 리포트 공용 파이프라인. 주말(US 미개장)이면 None(스킵).

    mode: "us_premarket" | "us_intraday" (snap.mode + 로그/발행 키).
    overlay: 시세 오버레이 코루틴(_overlay_premarket | _overlay_intraday).
    extra_steps: 모드 특화 동기 후처리(예: 프리장 급등 TOP5). 없으면 생략.
    """
    if not force and datetime.now().weekday() >= 5:  # 토(5)·일(6) KST = US 미개장
        logger.info("%s_skip — 주말", mode)
        return None

    from src.market_report.pipeline import (
        _attach_kr_netbuy_to_picks,
        _collect_kr_us_netbuy,
        _collect_sector_leaders,
        _collect_us_screening,
        _render_candles,
        collect_us_snapshot,
    )

    snap = await collect_us_snapshot()       # 지수/섹터(직전 마감 — 맥락)
    snap.mode = mode                         # type: ignore[assignment]
    snap.generated_at = datetime.now()

    async def _step(coro: Awaitable[None], label: str) -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s_%s_failed error=%s", mode, label, exc)

    await _step(_collect_us_screening(snap, per_group=3), "screening")  # 하이브리드 ABCD(마감 일봉), 3개씩
    await _step(overlay(snap), "overlay")                               # 프리장/장중 시세 오버레이
    await _step(_collect_sector_leaders(snap), "sector_leaders")        # 주요종목 = 강세4+약세4 섹터 대장
    await _step(_attach_kr_netbuy_to_picks(snap), "kr_netbuy")          # 픽별 서학개미 순매수금액(전일+5일)
    await _step(_collect_kr_us_netbuy(snap), "kr_netflow")              # 한국인 자금흐름 매수TOP5+매도TOP3(#318)
    if extra_steps is not None:
        try:
            extra_steps(snap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s_top_failed error=%s", mode, exc)

    try:
        from src.market_report.analyzer import analyze
        snap = await analyze(snap)           # AI (미국 컨텍스트)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s_analyze_failed error=%s", mode, exc)
    try:
        from src.market_report.analyzer import summarize_us_stocks
        await summarize_us_stocks(snap)      # 종목별 AI 요약(🤖 버튼용, 사용자 #309)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s_us_summary_failed error=%s", mode, exc)
    try:
        from src.market_report.analyzer import translate_us_news
        await translate_us_news(snap)        # 뉴스 헤드라인 한국어 번역(사용자 #394)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s_news_translate_failed error=%s", mode, exc)

    await _step(_render_candles(snap), "candles")                       # 지수 차트 (us 캔들)

    logger.info("%s_ready top3=%d groups=%d", mode,
                len(snap.us_top3 or []), len(snap.us_screen_groups or []))

    try:
        from src.market_report.render import render_report
        render_report(snap)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s_render_failed error=%s", mode, exc)
    if do_publish:
        try:
            from src.market_report.publisher import publish
            publish(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s_publish_failed error=%s", mode, exc)
    if do_telegram:
        try:
            from src.market_report.telegram_notify import send_report
            await send_report(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s_telegram_failed error=%s", mode, exc)

    return snap
