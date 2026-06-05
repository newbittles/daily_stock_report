"""미국장 장전(프리장) 리포트 — 평일 저녁 19:00 (KST).

미국 프리장 시간대(한국 17:00~23:30)에 발송. 직전 정규장 마감 일봉으로 ABCD 스크리닝
(us_morning과 동일 하이브리드 유니버스+필터) → 프리장 시세/등락률 오버레이 →
프리장에서 강한 종목·테마·추천주. 웹 발행(-us-pre.html) + 텔레그램.

design 결정(2026-06-04 사용자): 발송 저녁 7시, Q2=마감ABCD+프리장오버레이.
"""
from __future__ import annotations

import logging

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)


async def run_us_premarket(
    *, do_telegram: bool = True, do_publish: bool = True, force: bool = False,
) -> MarketSnapshot | None:
    """미국장 장전 리포트 생성·웹발행·발송. 주말이면 None(스킵).

    공용 러너(run_us_report)에 장전 차별점만 주입: 프리장 오버레이 + 프리장 급등 TOP5.
    """
    from src.market_report.pipeline import _overlay_premarket
    from src.market_report.us_report_runner import run_us_report

    return await run_us_report(
        "us_premarket", _overlay_premarket, extra_steps=_build_premarket_top,
        do_telegram=do_telegram, do_publish=do_publish, force=force,
    )


def _build_premarket_top(snap: MarketSnapshot, n: int = 5) -> None:
    """필터 통과(ABCD 스크린) 종목 중 프리장 상승률 TOP n → snap.us_premarket_top.

    각 픽의 change_pct는 _overlay_premarket으로 '프리장 등락률'로 덮인 상태. 프리장 체결된
    종목(premkt=True)만 대상, 프리장 상승률 내림차순. 섹터·매칭전략은 픽에 이미 있음(표시단).
    """
    seen: set[str] = set()
    pool: list[dict] = list(snap.us_top3 or []) + list(snap.us_theme_leaders or [])
    for g in (snap.us_screen_groups or []):
        pool.extend(g.get("picks", []))
    cand: list[dict] = []
    for p in pool:
        sym = p.get("symbol", "")
        if not sym or sym in seen or not p.get("premkt"):
            continue
        seen.add(sym)
        cand.append(p)
    cand.sort(key=lambda p: p.get("change_pct", 0), reverse=True)
    snap.us_premarket_top = cand[:n]
    logger.info("us_premarket_top n=%d top=%s", len(snap.us_premarket_top),
                [(p.get("symbol"), p.get("change_pct")) for p in snap.us_premarket_top[:3]])


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
