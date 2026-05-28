"""Phase 3 검증 — 전체 파이프라인 + HTML 렌더링.

실행: python scripts/test_render.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.market_report.pipeline import generate_report
from src.market_report.render import render_report


async def run() -> None:
    print("🟢 마감 전 리포트 생성 중...")
    snap_pre = await generate_report("pre_close")
    path_pre = render_report(snap_pre)
    print(f"   ✅ {path_pre}")

    print("🔵 마감 후 리포트 생성 중...")
    snap_post = await generate_report("post_close")
    path_post = render_report(snap_post)
    print(f"   ✅ {path_post}")

    print(f"\n로컬에서 브라우저로 확인:")
    print(f"   file:///{path_pre.as_posix()}")
    print(f"   file:///{path_post.as_posix()}")
    print(f"\n메인 페이지: file:///{(path_pre.parent.parent / 'index.html').as_posix()}")


if __name__ == "__main__":
    asyncio.run(run())
