"""Phase 2 검증 — 스크래퍼 + Gemini 분석기 통합 테스트.

실행: python scripts/test_analyzer.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.market_report.pipeline import generate_report


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


async def run() -> None:
    _section("🟢 마감 전 리포트 생성 (pre_close, 14:50 시뮬레이션)")
    snap = await generate_report("pre_close")

    print(f"\n[요약]\n  {snap.summary}\n")
    print(f"[왜 움직였나]\n  {snap.why_moved}\n")
    print(f"[테마 해설]\n  {snap.theme_commentary}\n")

    print(f"[종가베팅 후보 {len(snap.candidate_picks)}개]")
    for i, p in enumerate(snap.candidate_picks, 1):
        print(f"\n  {i}. {p['name']} ({p['ticker']})")
        print(f"     근거: {p['rationale']}")
        if p.get('risk'):
            print(f"     리스크: {p['risk']}")

    _section("🔴 마감 후 리포트 생성 (post_close, 16:30 시뮬레이션)")
    snap2 = await generate_report("post_close")

    print(f"\n[요약]\n  {snap2.summary}\n")
    print(f"[왜 움직였나]\n  {snap2.why_moved}\n")
    print(f"[테마 해설]\n  {snap2.theme_commentary}\n")

    print(f"[내일 관전 포인트]")
    for i, w in enumerate(snap2.candidate_picks, 1):
        print(f"  {i}. {w.get('watchpoint', '')}")


if __name__ == "__main__":
    asyncio.run(run())
