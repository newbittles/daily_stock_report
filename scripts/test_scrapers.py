"""Phase 1 빠른 검증 — 네이버 금융 스크래퍼 동작 확인.

실행: python scripts/test_scrapers.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.market_report.scrapers.naver import (
    fetch_index,
    fetch_investor_flow,
    fetch_top_gainers,
    fetch_top_losers,
    fetch_top_volume,
)
from src.market_report.scrapers.news import fetch_market_news
from src.market_report.scrapers.theme import fetch_top_themes


def _print_header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def _print_ranks(ranks, limit=10):
    print(f"  {'순위':<4} {'코드':<8} {'종목명':<24} {'현재가':>10} {'등락률':>8} {'거래량':>14}")
    print(f"  {'-'*4} {'-'*8} {'-'*24} {'-'*10} {'-'*8} {'-'*14}")
    for s in ranks[:limit]:
        sign = "+" if s.change_pct >= 0 else ""
        # 종목명 길이 자르기 (한글 대응)
        name = s.name[:14] + "…" if len(s.name) > 15 else s.name
        print(f"  {s.rank:<4} {s.ticker:<8} {name:<24} {s.price:>10,.0f}원 {sign}{s.change_pct:>6.2f}% {s.volume:>14,}")


async def run() -> None:
    _print_header("① 코스피 지수")
    kospi = await fetch_index("KOSPI")
    if kospi:
        sign = "+" if kospi.change_pct >= 0 else ""
        print(f"  KOSPI: {kospi.value:,.2f}  ({sign}{kospi.change:.2f} / {sign}{kospi.change_pct:.2f}%)")
        print(f"  거래량: {kospi.volume:,}  거래대금: {kospi.trade_value:,.0f}")
    else:
        print("  ❌ 지수 수집 실패")

    _print_header("② 코스닥 지수")
    kosdaq = await fetch_index("KOSDAQ")
    if kosdaq:
        sign = "+" if kosdaq.change_pct >= 0 else ""
        print(f"  KOSDAQ: {kosdaq.value:,.2f}  ({sign}{kosdaq.change:.2f} / {sign}{kosdaq.change_pct:.2f}%)")
    else:
        print("  ❌ 지수 수집 실패")

    _print_header("③ 코스피 거래량 상위 10")
    top_vol = await fetch_top_volume("KOSPI", top=15)
    _print_ranks(top_vol)

    _print_header("④ 코스피 상승률 상위 10")
    top_gain = await fetch_top_gainers("KOSPI", top=15)
    _print_ranks(top_gain)

    _print_header("⑤ 코스피 하락률 상위 10")
    top_loss = await fetch_top_losers("KOSPI", top=15)
    _print_ranks(top_loss)

    _print_header("⑥ 투자자별 수급 (최근 일자)")
    flow = await fetch_investor_flow()
    if flow:
        def fmt(v):
            sign = "+" if v >= 0 else ""
            return f"{sign}{v:,.0f}"
        print(f"  일자: {flow.date}")
        print(f"  외국인: {fmt(flow.foreign_net)}")
        print(f"  기관계: {fmt(flow.institution_net)}")
        print(f"  개인:   {fmt(flow.individual_net)}")
    else:
        print("  ❌ 수급 수집 실패")

    _print_header("⑦ 강세/약세 테마 Top 10")
    themes = await fetch_top_themes(top=10)
    if themes:
        for t in themes:
            sign = "+" if t.change_pct >= 0 else ""
            leads = ", ".join(t.leading_stocks[:3]) if t.leading_stocks else "—"
            print(f"  {t.rank:>2}. {t.name:<20} {sign}{t.change_pct:>6.2f}%   ▸ {leads}")
    else:
        print("  ❌ 테마 수집 실패")

    _print_header("⑧ 주요 시장 뉴스 Top 10")
    news = await fetch_market_news(top=10)
    if news:
        for i, n in enumerate(news, 1):
            src = f"[{n.source}]" if n.source else ""
            time = f"({n.published_at})" if n.published_at else ""
            print(f"  {i:>2}. {n.title[:60]} {src} {time}")
    else:
        print("  ❌ 뉴스 수집 실패")

    print()
    _print_header("검증 완료")


if __name__ == "__main__":
    asyncio.run(run())
