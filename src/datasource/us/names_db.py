"""미국 종목 한국어명 DB — 네이버 해외주식 best-effort 조회 + JSON 캐시(사용자 2026-06-04).

큐레이션(US_NAME_KO)에 없는 종목은 네이버 해외주식 API로 한국어명을 가져와
data/us_names_ko.json에 영구 캐시(처음 등장 시 1회). NYSE 등 suffix 불명은 .O/.K 시도,
실패하면 영문명 폴백. 캐시라 매 실행 시 재조회 안 함(외부 호출 최소화·CLAUDE.md §7).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "us_names_ko.json"
_CACHE: dict[str, str] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _CACHE_PATH.exists():
            _CACHE.update(json.loads(_CACHE_PATH.read_text(encoding="utf-8")) or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("us_names_db_load_failed error=%s", exc)


def cached_name(ticker: str) -> str:
    """캐시된 한국어명(없으면 빈 문자열). HTTP 안 함 — korean_name에서 안전 사용."""
    _load()
    return _CACHE.get(ticker, "")


def _save() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("us_names_db_save_failed error=%s", exc)


def _fetch_naver_ko(ticker: str) -> str:
    """네이버 해외주식 API로 한국어명 1종목. .O→.K 순 시도. 실패 시 빈 문자열."""
    import httpx

    for suf in ("O", "K"):
        try:
            r = httpx.get(f"https://api.stock.naver.com/stock/{ticker}.{suf}/basic",
                          timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 429:  # §7 HARD STOP
                logger.warning("us_names_db_rate_limited ticker=%s", ticker)
                return ""
            if r.status_code == 200:
                nm = (r.json() or {}).get("stockName") or ""
                # 영문(ASCII만)이면 한국어 아님 → 폴백
                if nm and any(ord(c) > 0x7F for c in nm):
                    return str(nm).strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug("us_names_db_fetch_failed ticker=%s suf=%s error=%s", ticker, suf, exc)
    return ""


async def ensure_names(tickers: list[str]) -> None:
    """미캐시 종목의 한국어명을 네이버에서 조회·캐시(best-effort). 거래대금 상위부터 소수만.

    캐시에 없는 종목만 조회. 분산 딜레이(§7). 실패는 캐시에 영문폴백 표식 안 남김(다음 기회 재시도).
    """
    import asyncio
    import random

    _load()
    missing = [t for t in dict.fromkeys(tickers) if t and t not in _CACHE]
    if not missing:
        return

    def _work() -> int:
        import time
        n = 0
        for t in missing:
            ko = _fetch_naver_ko(t)
            if ko:
                _CACHE[t] = ko
                n += 1
            time.sleep(random.uniform(0.15, 0.4))  # §7 분산
        if n:
            _save()
        return n

    got = await asyncio.to_thread(_work)
    logger.info("us_names_db_ensure missing=%d cached=%d", len(missing), got)
