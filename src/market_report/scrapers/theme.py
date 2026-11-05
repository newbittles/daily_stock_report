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
from pathlib import Path


from src.market_report.http import fetch
from src.market_report.models import ThemeRank
from src.market_report.scrapers.judal import _is_nontheme

logger = logging.getLogger(__name__)

THEME_URL = "https://finance.naver.com/sise/theme.naver"
THEME_DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={no}"
_THEME_LINK_PATTERN = re.compile(r'theme&no=(\d+)"[^>]*>([^<]+)<')
_DETAIL_STOCK_PATTERN = re.compile(r'/item/main\.naver\?code=(\d{6})">([^<]+)</a>')
_THEME_MAP_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "theme_map_cache.json"


THEME_API = "https://m.stock.naver.com/api/stocks/theme"


def parse_mobile_themes(data: dict) -> list[tuple[int, str, float]]:
    """모바일 테마 목록 JSON → [(no, 이름, 등락률)] (순수·테스트가능). 비테마는 뺀다."""
    out: list[tuple[int, str, float]] = []
    for g in data.get("groups") or []:
        name = str(g.get("name") or "").strip()
        if not name or _is_nontheme(name):  # 계절/비테마(제습기·에어컨·독감 등) 강세테마 제외
            continue
        try:
            out.append((int(g["no"]), name, float(str(g.get("changeRate", "0")).replace(",", ""))))
        except (KeyError, ValueError):
            continue
    return out


async def fetch_top_themes(top: int = 10) -> list[ThemeRank]:
    """등락률 상위 테마 N개 + 각 테마의 주도주(Top 3).

    2026-11-05: 데스크톱 theme.naver 가 Next.js 로 바뀌어(표 없음 → html5lib 오류로 0건) 모바일 JSON 으로 전환."""
    try:
        raw = await fetch(f"{THEME_API}?page=1&pageSize={top + 10}", encoding="utf-8")
        metas = parse_mobile_themes(json.loads(raw))[:top]
    except Exception as exc:  # noqa: BLE001
        logger.warning("theme_table_failed error=%s", exc)
        return []

    results: list[ThemeRank] = []
    for no, name, pct in metas:
        leading: list[str] = []
        await asyncio.sleep(random.uniform(0.3, 0.9))  # §6 랜덤 딜레이
        try:
            d = json.loads(await fetch(f"{THEME_API}/{no}?page=1&pageSize=3", encoding="utf-8"))
            leading = [str(x.get("stockName", "")).strip() for x in (d.get("stocks") or [])[:3]]
        except Exception as exc:  # noqa: BLE001 — 주도주 없이도 테마는 쓴다
            logger.debug("theme_detail_failed no=%s error=%s", no, exc)
        results.append(ThemeRank(rank=0, name=name, change_pct=pct, leading_stocks=leading))

    # 등락률 절댓값 내림차순으로 정렬 (강세·약세 모두 노출)
    results.sort(key=lambda t: abs(t.change_pct), reverse=True)
    for i, t in enumerate(results, 1):
        t.rank = i
    return results


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
