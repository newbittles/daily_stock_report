"""미국 S&P500 종목 스크리닝 실행 (us_screening P1 진입점/스모크).

    python scripts/run_us_screening.py

config/screener_us.yaml 의 활성 전략(P1=C 추세추종)으로 S&P500 전체를 스크리닝한다.
design: docs/02-design/features/us-screening.design.md
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.datasource.us.universe import (  # noqa: E402
    get_combined_universe,
    get_nasdaq_hot_universe,
)
from src.screener.us_pipeline import DISCLAIMER, run_us_screening  # noqa: E402
from src.screener.us_report import (  # noqa: E402
    build_us_screening_report,
    send_us_screening_report,
)


async def main(mode: str = "combined", send: bool = False) -> None:
    t0 = time.time()
    if mode == "sp500":
        universe = None  # run_us_screening 기본 = S&P500
    elif mode == "nasdaq-hot":
        universe = await get_nasdaq_hot_universe()
    else:  # combined
        universe = await get_combined_universe()
    picks = await run_us_screening(universe=universe)
    elapsed = time.time() - t0

    # 거래대금(달러) 내림차순
    picks.sort(key=lambda p: p.price * (p.candles[-1].volume if p.candles else 0), reverse=True)

    print(f"\n=== 미국 S&P500 스크리닝 — {len(picks)}종목 포착 ({elapsed:.1f}s) ===\n")
    for p in picks:
        ops = " / ".join(p.opinions)
        print(f"[{p.symbol}] {p.name}  ({p.sector})  ${p.price:.2f}  {p.change_pct:+.1f}%")
        print(f"    {ops}")
        for r in p.all_reasons:
            print(f"      · {r}")
    print(f"\n{DISCLAIMER}")

    # ── 텔레그램 리포트 (전략별 Top N) ──
    print("\n" + "=" * 50)
    print("텔레그램 리포트 미리보기:")
    print("=" * 50)
    print(build_us_screening_report(picks, top_n=5))

    if send:
        print("\n발송 중...")
        ok = await send_us_screening_report(picks, top_n=5)
        print("✅ 발송 완료" if ok else "⚠️ 발송 실패 (chat_id/토큰 확인)")
    else:
        print("\n(미리보기만 — 실제 발송은 --send 플래그)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["sp500", "nasdaq-hot", "combined"],
                    default="combined", help="유니버스 모드 (기본: combined)")
    ap.add_argument("--send", action="store_true", help="텔레그램 실제 발송 (기본: 미리보기만)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main(args.universe, send=args.send))
