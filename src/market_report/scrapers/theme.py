"""네이버 금융 — 테마 강세 순위.

URL: https://finance.naver.com/sise/theme.naver
- 테마별 등락률·종목수·주도주 제공
- 테마 상세: theme.naver?no={theme_no}
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from src.market_report.http import fetch
from src.market_report.models import ThemeRank

logger = logging.getLogger(__name__)

THEME_URL = "https://finance.naver.com/sise/theme.naver"
THEME_DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={no}"
_THEME_LINK_PATTERN = re.compile(r'theme&no=(\d+)"[^>]*>([^<]+)<')
_DETAIL_STOCK_PATTERN = re.compile(r'/item/main\.naver\?code=(\d{6})">([^<]+)</a>')
_THEME_MAP_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "theme_map_cache.json"


async def fetch_top_themes(top: int = 10) -> list[ThemeRank]:
    """등락률 상위 테마 N개 + 각 테마의 주도주(Top 3)."""
    html = await fetch(THEME_URL, encoding="euc-kr")

    # 테마 테이블 파싱
    try:
        tables = pd.read_html(StringIO(html))
        # 가장 큰 테이블이 테마 시세표
        main = max(tables, key=lambda t: len(t))
        main = main.dropna(how="all")

        # 테마명·등락률·상승종목수·하락종목수·주도주 컬럼이 보통 포함
        # 컬럼 수는 페이지마다 다를 수 있으므로 가변 처리
    except Exception as exc:
        logger.warning("theme_table_failed error=%s", exc)
        return []

    # 테마명+번호 추출 (HTML에서 순서대로)
    theme_matches = _THEME_LINK_PATTERN.findall(html)
    # 중복 제거 (등장 순서 유지)
    seen = set()
    theme_meta = []
    for no, name in theme_matches:
        if no not in seen:
            seen.add(no)
            theme_meta.append((no, name.strip()))

    # 주도주는 BeautifulSoup으로 더 자세히 추출
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table.type_1 tr")

    results: list[ThemeRank] = []
    for i, row in enumerate(rows):
        if i >= len(theme_meta) + 5:  # 헤더 여유분
            break
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        # 테마명 셀 (대개 첫번째 또는 두번째 td)
        name_link = row.select_one('a[href*="theme&no="]')
        if not name_link:
            continue
        theme_name = name_link.get_text(strip=True)

        # 등락률 추출 (텍스트에서 % 포함된 값 찾기)
        change_pct = 0.0
        for cell in cells:
            t = cell.get_text(strip=True)
            m = re.search(r"([+-]?\d+\.\d+)%", t)
            if m:
                change_pct = float(m.group(1))
                break

        # 주도주: 같은 행의 다른 a 태그 (item/main.naver?code= 형식)
        stock_links = row.select('a[href*="item/main.naver"]')
        leading = [a.get_text(strip=True) for a in stock_links[:3]]

        results.append(
            ThemeRank(
                rank=len(results) + 1,
                name=theme_name,
                change_pct=change_pct,
                leading_stocks=leading,
            )
        )

        if len(results) >= top:
            break

    # 등락률 절댓값 내림차순으로 정렬 (강세·약세 모두 노출)
    results.sort(key=lambda t: abs(t.change_pct), reverse=True)
    for i, t in enumerate(results, 1):
        t.rank = i

    return results[:top]


async def build_ticker_theme_map(max_themes: int = 25) -> dict[str, str]:
    """종목코드 → 대표 테마명 역인덱스. 테마 상세페이지에서 종목 전체 수집.

    네이버 테마 목록의 상위 max_themes개 상세를 순회해 {ticker: theme_name} 구성.
    하루 1회 data/theme_map_cache.json 캐시. 네이버 robot 감지 방지를 위해
    요청 수를 제한하고 딜레이를 넉넉히 둔다(§7). 실패/부분 시 가능한 만큼 반환.
    """
    today = date.today().isoformat()
    try:
        if _THEME_MAP_CACHE.exists():
            c = json.loads(_THEME_MAP_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == today and c.get("map"):
                return {str(k): str(v) for k, v in c["map"].items()}
    except Exception as exc:
        logger.debug("theme_map_cache_read_failed error=%s", exc)

    mapping: dict[str, str] = {}
    try:
        html = await fetch(THEME_URL, encoding="euc-kr")
        metas, seen = [], set()
        for no, name in _THEME_LINK_PATTERN.findall(html):
            if no not in seen:
                seen.add(no)
                metas.append((no, name.strip()))
        hard_stops = 0
        for idx, (no, tname) in enumerate(metas[:max_themes]):
            await asyncio.sleep(random.uniform(0.8, 2.0))  # §7 넉넉한 랜덤 딜레이
            if idx and idx % 10 == 0:
                await asyncio.sleep(random.uniform(3.0, 6.0))  # 배치 휴식
            try:
                detail = await fetch(THEME_DETAIL_URL.format(no=no), encoding="euc-kr")
            except Exception as exc:
                if "robot" in str(exc).lower() or "hard_stop" in str(exc).lower():
                    hard_stops += 1
                    if hard_stops >= 2:  # robot 2회 → 즉시 중단(§7), 모은 만큼 사용
                        logger.warning("theme_map_robot_halt collected=%d", len(mapping))
                        break
                continue
            for code, _nm in _DETAIL_STOCK_PATTERN.findall(detail):
                mapping.setdefault(code, tname)  # 먼저 등장(상위 테마) 우선
    except Exception as exc:
        logger.warning("theme_map_build_failed error=%s", exc)
        return {}

    try:
        _THEME_MAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _THEME_MAP_CACHE.write_text(
            json.dumps({"date": today, "map": mapping}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("theme_map_cache_write_failed error=%s", exc)

    logger.info("theme_map_built themes=%d tickers=%d", min(len(metas), max_themes), len(mapping))
    return mapping
