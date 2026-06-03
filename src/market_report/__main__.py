"""CLI 진입점 — 수동으로 일일 리포트 생성.

사용법:
    python -m src.market_report pre              # 마감 전 리포트 (모든 단계)
    python -m src.market_report post             # 마감 후 리포트
    python -m src.market_report pre --dry        # 데이터 수집 + 렌더링만 (push·텔레그램 X)
    python -m src.market_report pre --no-publish # git push 스킵
    python -m src.market_report pre --no-tg      # 텔레그램 발송 스킵
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.market_report.pipeline import run_full

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily market report generator")
    parser.add_argument("mode", choices=["pre", "post"], help="pre_close | post_close")
    parser.add_argument("--dry", action="store_true", help="render만 (push·텔레그램 스킵)")
    parser.add_argument("--no-publish", action="store_true", help="git push 스킵")
    parser.add_argument("--no-tg", action="store_true", help="텔레그램 발송 스킵")
    parser.add_argument("--force", action="store_true", help="휴장일 스킵 무시하고 강제 실행")
    args = parser.parse_args()

    mode = "pre_close" if args.mode == "pre" else "post_close"
    do_publish = not (args.dry or args.no_publish)
    do_telegram = not (args.dry or args.no_tg)

    snap = asyncio.run(run_full(mode, do_publish=do_publish, do_telegram=do_telegram, force=args.force))

    print()
    print(f"✅ {snap.mode} 리포트 생성 완료")
    print(f"   요약: {snap.summary[:80] if snap.summary else '(AI 분석 폴백)'}")
    print(f"   후보/포인트: {len(snap.candidate_picks)}개")
    print(f"   테마: {len(snap.top_themes)}개")
    if not args.dry and not args.no_publish:
        from src.market_report.publisher import report_url
        print(f"   URL: {report_url(snap)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
