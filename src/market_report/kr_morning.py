"""한국장 프리(08:05)·장초(09:15) 리포트 — 사용자 #404.

- kr_premarket(08:05): NXT 프리장(08:00~) 상승률 상위 + 전일 종가베팅·Top3 NXT 시초등락 + AI 시장분위기.
- kr_open(09:15): 정규장 시초 상승률 상위 + 전일 종가베팅·Top3 정규장 시초등락 + AI 장초요약.
공통 흐름은 run_kr_morning(mode)로 통합. midday 패턴 재사용(가벼운 스냅샷 + 전일 picks 현황).
'없는 정보는 생략'(사용자) — NXT/시초 데이터 없으면 해당 섹션만 빠짐.
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


def last_session_pct(closes: list[tuple[str, float]], today: str) -> tuple[float, float] | None:
    """(날짜 YYYY-MM-DD, 종가) 시계열 → (직전 거래일 종가, 그 날 등락률%). 순수·결정론.

    today 이전 데이터만 사용(강제 재실행으로 당일 봉이 섞여도 안전). 2개 미만이면 None."""
    rows = sorted((d, c) for d, c in closes if d < today and c)
    if len(rows) < 2:
        return None
    prev, last = rows[-2][1], rows[-1][1]
    return last, (last / prev - 1) * 100


async def _fill_prev_session_index(snap: MarketSnapshot, today: str) -> None:
    """프리장(08:0x) 지수 보정 — 네이버는 개장 전 '전일종가 + 0.00%' 고정(#469 실측).

    FDR 일봉 마지막 2종가로 '전일 등락률'을 계산해 대체하고 라벨을 '전일'로 표기."""
    import dataclasses

    def _closes(sym: str) -> list[tuple[str, float]]:
        import FinanceDataReader as fdr
        df = fdr.DataReader(sym).tail(5)
        return [(idx.strftime("%Y-%m-%d"), float(c)) for idx, c in df["Close"].dropna().items()]

    import asyncio
    for attr, sym in (("kospi", "KS11"), ("kosdaq", "KQ11")):
        idx = getattr(snap, attr)
        if idx is None:
            continue
        try:
            r = last_session_pct(await asyncio.to_thread(_closes, sym), today)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prev_session_index_failed sym=%s error=%s", sym, exc)
            continue
        if r:
            setattr(snap, attr, dataclasses.replace(idx, value=r[0], change_pct=r[1]))
            snap.index_pct_label = "전일"


async def run_kr_morning(
    mode: str, *, do_telegram: bool = True, do_publish: bool = True, force: bool = False,
) -> MarketSnapshot | None:
    """한국장 프리/장초 리포트. 휴장일이면 None(스킵)."""
    from src.market_report.market_calendar import is_kr_market_open_today

    if not force and not await is_kr_market_open_today():
        logger.info("%s_skip — 휴장일", mode)
        return None

    from src.market_report.pipeline import collect_snapshot

    snap = await collect_snapshot("midday")  # 지수·수급·테마·정규장 gainers 베이스(가벼움)
    snap.mode = mode  # type: ignore[assignment]
    snap.generated_at = datetime.now()

    try:
        from src.config.settings import get_settings
        from src.datasource.kis.adapter import KisAdapter
        from src.market_report.top3_status import (
            fetch_prev_top3_status, find_prev_candidates, find_prev_top3,
        )
        s = get_settings()
        adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
        today = snap.generated_at.strftime("%Y-%m-%d")

        # 프리(08:05): NXT 프리장 상승률 상위 — 정규장 gainers는 아직 의미없어 NXT로 대체
        use_nxt = mode == "kr_premarket"
        if use_nxt:
            try:
                snap.overtime_gainers = await adapter.get_nxt_overtime_gainers(top=7)
            except Exception as exc:  # noqa: BLE001
                logger.warning("kr_premarket_nxt_failed error=%s", exc)
            snap.top_gainers = []  # 08:05 정규장 시초 없음 → 정규장 gainers 숨김
            # 지수도 개장 전엔 0.00% 고정 → 전일 등락률로 대체 표기(#469)
            await _fill_prev_session_index(snap, today)

        # 전일 추천 Top3 현황 (추천가 대비 + 오늘 등락) — 프리장은 NXT 시세(#469)
        prev = find_prev_top3(today)
        if prev:
            d, picks = prev
            snap.prev_top3_status = await fetch_prev_top3_status(picks, adapter, use_nxt=use_nxt)
            snap.prev_top3_date = d
        # 전일 종가베팅 후보 시초 현황
        pc = find_prev_candidates(today)
        if pc:
            d, picks = pc
            snap.prev_candidates_status = await fetch_prev_top3_status(picks, adapter, use_nxt=use_nxt)
            snap.prev_candidates_date = d

        # 지수 신호등/이격도(고점·바닥 분위기)
        try:
            from src.market_report.pipeline import _fill_market_phase, _index_ma_gaps
            snap.ma_gaps = {"코스피": await _index_ma_gaps("KS11"), "코스닥": await _index_ma_gaps("KQ11")}
            _fill_market_phase(snap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s_ma_gaps_failed error=%s", mode, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s_kis_failed error=%s", mode, exc)

    # AI 시장 분위기 요약(전일 미국장·환율·수급·신호등 종합 → 오늘 시초 분위기)
    try:
        from src.market_report.analyzer import summarize_midday
        snap.summary = await summarize_midday(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s_summary_failed error=%s", mode, exc)

    logger.info("%s_ready gainers=%d nxt=%d prev_top3=%d prev_cand=%d", mode,
                len(snap.top_gainers or []), len(snap.overtime_gainers or []),
                len(snap.prev_top3_status or []), len(snap.prev_candidates_status or []))

    try:
        from src.market_report.pipeline import _render_candles
        await _render_candles(snap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s_candles_failed error=%s", mode, exc)
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


if __name__ == "__main__":
    import argparse
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="한국장 프리/장초 리포트")
    ap.add_argument("mode", choices=["kr_premarket", "kr_open"])
    ap.add_argument("--no-tg", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    snap = asyncio.run(run_kr_morning(
        args.mode, do_telegram=not args.no_tg, do_publish=not args.no_publish, force=args.force))
    print(f"✅ {args.mode} 완료" if snap else "휴장일 — 스킵")
