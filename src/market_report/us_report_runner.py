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

# 지수 표시명 → 선물 심볼(프리장, #497) / 실시간 지수 심볼(장중, #511)
_INDEX_FUTURES = {"S&P500": "ES=F", "나스닥": "NQ=F"}
_INDEX_REALTIME = {"S&P500": "^GSPC", "나스닥": "^IXIC"}


async def _apply_index_futures(snap: MarketSnapshot) -> None:
    """프리장(us_premarket): us_indices의 S&P500·나스닥을 선물 실시간 등락률로 교체(#497).

    미국 정규장 휴장(프리장) 시간대라 선물(NQ=F·ES=F)이 분위기 지표. 이름에 '선물' 표기.
    선물 미수신 항목은 전날 종가 유지. snap.us_overnight에도 보관(M7 등 재사용)."""
    from src.datasource.us.overnight import fetch_us_overnight

    ov = await fetch_us_overnight()
    snap.us_overnight = ov
    futs = {f["symbol"]: f for f in ov.get("futures", [])}
    for q in snap.us_indices:
        fsym = _INDEX_FUTURES.get(q.get("name", ""))
        f = futs.get(fsym) if fsym else None
        if f:
            q["name"] = f"{q['name']} 선물"
            q["price"] = f["price"]
            q["change_pct"] = f["change_pct"]
            q["is_futures"] = True
    logger.info("index_futures_applied futures=%d", len(futs))


async def _apply_index_realtime(snap: MarketSnapshot) -> None:
    """장중(us_intraday): us_indices의 S&P500·나스닥을 실시간 '지수'로 교체(#511).

    미국 정규장 진행 중이라 선물이 아니라 실제 지수(^GSPC·^IXIC) 실시간값이 맞다.
    FDR 전날 마감 종가 대신 yfinance fast_info 실시간으로 덮어씀(이름은 지수 그대로)."""
    import asyncio

    from src.datasource.us.overnight import _fetch_one

    for q in snap.us_indices:
        sym = _INDEX_REALTIME.get(q.get("name", ""))
        if not sym:
            continue
        try:
            r = await asyncio.to_thread(_fetch_one, sym)
        except Exception as exc:  # noqa: BLE001
            logger.warning("index_realtime_failed sym=%s error=%s", sym, exc)
            continue
        if r:
            q["price"] = r["price"]
            q["change_pct"] = r["change_pct"]
            q["is_realtime"] = True
    logger.info("index_realtime_applied")


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
        _render_picks_charts,
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

    # 지수 실시간화 — FDR 전날 마감 종가를 그대로 쓰면 안 됨.
    # 프리장(미국 휴장)=선물(#497), 장중(정규장 진행)=실제 지수 실시간(#511).
    if mode == "us_premarket":
        await _step(_apply_index_futures(snap), "index_futures")
    elif mode == "us_intraday":
        await _step(_apply_index_realtime(snap), "index_realtime")

    await _step(_collect_us_screening(snap, per_group=3), "screening")  # 하이브리드 ABCD(마감 일봉), 3개씩
    await _step(overlay(snap), "overlay")                               # 프리장/장중 시세 오버레이
    if snap.us_top3:                                                    # Top3에 종가베팅 동일 차트(사용자 2026-06-18)
        await _step(_render_picks_charts(
            snap.us_top3, snap.generated_at.strftime("%Y-%m-%d"), ticker_key="symbol"), "top3_chart")
    await _step(_collect_sector_leaders(snap), "sector_leaders")        # 주요종목 = 강세4+약세4 섹터 대장
    await _step(_attach_kr_netbuy_to_picks(snap), "kr_netbuy")          # 픽별 서학개미 순매수금액(전일+5일)
    await _step(_collect_kr_us_netbuy(snap), "kr_netflow")              # 한국인 자금흐름 매수TOP5+매도TOP3(#318)
    # 보유종목: US 리포트엔 '미국' 보유종목을 라이브 시세로 표시(#971/item③ — KR종목 노출 버그 수정).
    from src.market_report.us_holdings import attach_us_holdings
    await _step(attach_us_holdings(snap), "us_holdings")                # 📋 미국 보유종목(오너 전용 게이트)
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
