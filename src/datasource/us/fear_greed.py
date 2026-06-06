"""CNN Fear & Greed Index — 미국 시장 공포탐욕지수(0~100). 바닥 신호 보조(사용자 #331).

소스: production.dataviz.cnn.io/index/fearandgreed/graphdata (현재값 + 과거 이력). User-Agent 필요.
검증(2026-06-06): 현재 score/rating 제공, /graphdata/<date>로 과거 이력. 백테스트상 F&G≤25부터
나스닥 매수 시 20일 +4~9%(승률 69~87%), 최저(2025-04-08 score 3)→20일 +16% — extreme fear=바닥권.
전역 §7: 재시도3·지수백오프·HARD STOP(429/503). 일1회 캐시(외부호출 최소화).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_CACHE = Path(__file__).resolve().parents[3] / "data" / "fear_greed_cache.json"

# CNN rating → 한국어 라벨
RATING_KO = {
    "extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립",
    "greed": "탐욕", "extreme greed": "극단적 탐욕",
}


def _fetch_sync() -> dict | None:
    import requests

    for attempt in range(3):
        try:
            r = requests.get(_URL, headers=_HEADERS, timeout=12)
        except requests.RequestException as exc:
            logger.warning("fear_greed_req_error attempt=%d/3 error=%s", attempt + 1, exc)
        else:
            if r.status_code in (429, 503):  # §7 HARD STOP
                logger.warning("fear_greed_hard_stop status=%d", r.status_code)
                return None
            if r.status_code != 200:
                logger.warning("fear_greed_bad_status status=%d", r.status_code)
                return None
            try:
                d = (r.json() or {}).get("fear_and_greed", {})
                score = round(float(d.get("score", 0)))
                return {"score": score, "rating": str(d.get("rating", "")).strip()}
            except Exception as exc:  # noqa: BLE001
                logger.warning("fear_greed_parse_error error=%s", exc)
                return None
        if attempt < 2:
            time.sleep(random.uniform(3.0 * (2 ** attempt), 6.0 * (2 ** attempt)))
    return None


def _load_cache() -> dict | None:
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == date.today().isoformat():
                return c.get("value")
    except Exception as exc:  # noqa: BLE001
        logger.debug("fear_greed_cache_read_failed error=%s", exc)
    return None


async def fetch_fear_greed(use_cache: bool = True) -> dict | None:
    """현재 공포탐욕지수 → {score(0~100), rating, rating_ko}. 실패 시 None(섹션 생략)."""
    if use_cache:
        cached = _load_cache()
        if cached is not None:
            return cached
    v = await asyncio.to_thread(_fetch_sync)
    if v:
        v["rating_ko"] = RATING_KO.get(v.get("rating", "").lower(), v.get("rating", ""))
        try:
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE.write_text(json.dumps({"date": date.today().isoformat(), "value": v},
                                         ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.debug("fear_greed_cache_write_failed error=%s", exc)
    return v
