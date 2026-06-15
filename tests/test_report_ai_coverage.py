"""US 리포트 종목 섹션 AI요약 커버리지 가드 (사용자 2026-06-11).

'종목 단위 기능을 일부 섹션에서 빠뜨리는' 누락을 자동 검출하는 메타 테스트.
report.html이 렌더하는 모든 US 종목 리스트(snap.X)는 summarize_us_stocks의 pools에 포함돼야 한다.

배경: 미국 종목 스크리닝 섹션은 snap.us_screen_ranked를 렌더하는데 AI요약 pools엔
snap.us_screen_groups만 있어, 스크리닝 종목에 🤖AI요약 버튼이 안 나오던 버그(06-11).
이런 '렌더 리스트 ↔ AI pools 불일치' 누락이 다시 생기면 이 테스트가 잡는다.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from src.market_report.analyzer import summarize_us_stocks

_TEMPLATE = Path("src/market_report/templates/report.html")

# 종목이 아닌 리스트(지수·뉴스·야간M7·섹터등락률) — 종목별 AI요약 대상이 아님(정당한 제외)
# us_turnover_top10: 거래대금 순위(정량 랭킹) 참고 섹션 — 상위 종목은 스크리닝/Top3와 중복돼
#   거기서 AI요약 제공, 랭킹 자체엔 AI버튼 미부착(정보 다이어트, 사용자 2026-06-14).
# us_holdings_status: 본인 미국 보유종목 상태(라이브 시세·평가손익·손절상태) 섹션 — 라이브 시세
#   연동만 요청(item③, 2026-06-15), 종목별 AI요약은 대상 아님(정당한 제외).
_NON_STOCK = {
    "us_indices", "us_news", "us_overnight", "us_sectors", "us_turnover_top10",
    "us_holdings_status",
}


def _rendered_us_stock_lists() -> set[str]:
    """report.html의 '{% for x in snap.<list> %}' 중 US 종목 리스트 이름 집합."""
    src = _TEMPLATE.read_text(encoding="utf-8")
    names = set(re.findall(r"\{%\s*for\s+\w+\s+in\s+snap\.([a-z_0-9]+)", src))
    return {
        n for n in names
        if (n.startswith("us_") or n in {"e_picks", "surge_picks", "support_picks", "coil_picks"})
        and n not in _NON_STOCK
    }


def test_us_stock_sections_covered_by_summary_pools() -> None:
    """렌더되는 모든 US 종목 리스트가 summarize_us_stocks pools에 들어있어야 AI요약이 표시된다."""
    pools_src = inspect.getsource(summarize_us_stocks)
    missing = {n for n in _rendered_us_stock_lists() if n not in pools_src}
    assert not missing, (
        f"US 종목 섹션이 summarize_us_stocks pools에 누락됨: {sorted(missing)} "
        "→ 해당 섹션 종목에 🤖AI요약 버튼이 안 나옵니다. pools에 추가하세요."
    )
