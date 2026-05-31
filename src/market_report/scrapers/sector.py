"""네이버 금융 — 종목별 세분 업종(섹터). 테마 누락 종목의 폴백 분류.

종목 메인 페이지에 테마 링크는 없으나 '업종'(반도체와반도체장비, 전기기기 등 세분)은
전 종목에 분류돼 있다. 테마가 없는 종목에 업종을 채워 누락 0을 만든다.

업종은 거의 안 바뀌므로 data/sector_cache.json 에 영구 캐시(신규 종목만 크롤링).
robot 감지 시 즉시 중단(§7) — 모은 만큼만 반환.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path

from src.market_report.http import fetch

logger = logging.getLogger(__name__)

ITEM_URL = "https://finance.naver.com/item/main.naver?code={code}"
_UPJONG_PATTERN = re.compile(r'type=upjong&no=\d+["\']?>([^<]+)<')
_SECTOR_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "sector_cache.json"


def _load() -> dict[str, str]:
    try:
        if _SECTOR_CACHE.exists():
            return {str(k): str(v) for k, v in json.loads(_SECTOR_CACHE.read_text(encoding="utf-8")).items()}
    except Exception:
        pass
    return {}


def _save(cache: dict[str, str]) -> None:
    try:
        _SECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SECTOR_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("sector_cache_write_failed error=%s", exc)


async def get_stock_sectors(codes: list[str], max_fetch: int = 40) -> dict[str, str]:
    """종목코드 리스트 → {code: 세분업종명}. 영구 캐시, 신규만 크롤링.

    max_fetch: 1회 호출당 신규 크롤링 상한 (robot 방지). 초과분은 다음 기회에.
    """
    cache = _load()
    missing = [c for c in codes if c and c not in cache][:max_fetch]
    robot_hits = 0
    for idx, code in enumerate(missing):
        await asyncio.sleep(random.uniform(0.8, 2.0))  # §7 딜레이
        if idx and idx % 10 == 0:
            await asyncio.sleep(random.uniform(3.0, 6.0))
        try:
            html = await fetch(ITEM_URL.format(code=code), encoding="utf-8")
        except Exception as exc:
            if "robot" in str(exc).lower() or "hard_stop" in str(exc).lower():
                robot_hits += 1
                if robot_hits >= 2:
                    logger.warning("sector_robot_halt collected=%d", len(cache))
                    break
            continue
        m = _UPJONG_PATTERN.search(html)
        if m:
            cache[code] = m.group(1).strip()
    if missing:
        _save(cache)
    return {c: cache.get(c, "") for c in codes}
