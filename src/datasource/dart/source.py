"""DART 전자공시 — 종목별 최근 공시 조회 (opendart.fss.or.kr OpenAPI).

종목코드→corp_code 매핑(corpCode.xml, ~3.5MB zip)은 data/dart_corpcode.json에 일1회 캐시.
공시목록은 list.json. 전역 CLAUDE.md §7(외부 API 안전): 랜덤 딜레이·재시도3·HARD STOP(429/한도).

⚠️ AI 환각 방지: 조회 성공+공시없음 = [](='없음'), 조회 실패/매핑없음 = None(='확인 불가').
   둘을 구분해 '없었는데 있다고' 또는 '있었는데 없다고' 하지 않도록 호출측에서 분기.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_FILE = PROJECT_ROOT / "data" / "dart_corpcode.json"  # gitignore된 런타임 캐시
_BASE = "https://opendart.fss.or.kr/api"

_corp_map: dict[str, str] | None = None  # 프로세스 메모리 캐시


async def _load_corp_map(key: str) -> dict[str, str]:
    """종목코드(6)→corp_code(8) 매핑. 메모리→일일 파일캐시→DART corpCode.xml 다운로드 순."""
    global _corp_map
    if _corp_map is not None:
        return _corp_map
    today = date.today().isoformat()
    if CACHE_FILE.exists():
        try:
            obj = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if obj.get("date") == today and obj.get("map"):
                _corp_map = obj["map"]
                return _corp_map
        except Exception:  # noqa: BLE001
            pass
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{_BASE}/corpCode.xml", params={"crtfc_key": key})
        z = zipfile.ZipFile(io.BytesIO(r.content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        m: dict[str, str] = {}
        for e in root.findall("list"):
            sc = (e.findtext("stock_code") or "").strip()
            if sc and sc != " ":
                m[sc] = (e.findtext("corp_code") or "").strip()
        _corp_map = m
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"date": today, "map": m}, ensure_ascii=False), encoding="utf-8")
        logger.info("dart_corpcode_loaded listed=%d", len(m))
        return m
    except Exception as exc:  # noqa: BLE001
        logger.warning("dart_corpcode_failed error=%s", exc)
        _corp_map = {}
        return {}


async def fetch_recent_disclosures(
    ticker: str, key: str, days: int = 10, top: int = 5
) -> list[dict] | None:
    """종목 최근 공시 목록 [{date(YYYYMMDD), title}]. 최신순.

    반환: list(0건이면 [] = '공시 없음') / None(매핑없음·조회실패 = '확인 불가').
    """
    if not key:
        return None
    cmap = await _load_corp_map(key)
    corp_code = cmap.get(str(ticker).strip())
    if not corp_code:
        return None
    bgn = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    params = {"crtfc_key": key, "corp_code": corp_code, "bgn_de": bgn, "page_count": 30}
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1))))
            else:
                await asyncio.sleep(random.uniform(0.1, 0.35))
            async with httpx.AsyncClient(timeout=12.0) as c:
                r = await c.get(f"{_BASE}/list.json", params=params)
            if r.status_code == 429:  # HARD STOP — 한도 초과, 재시도 금지
                logger.warning("dart_rate_limit_429 ticker=%s — 중단", ticker)
                return None
            data = r.json()
            status = data.get("status")
            if status == "013":          # 조회된 데이터 없음 = 공시 없음(사실)
                return []
            if status != "000":
                logger.warning("dart_status ticker=%s status=%s msg=%s", ticker, status, data.get("message"))
                return None
            return [
                {"date": str(it.get("rcept_dt", "")).strip(),
                 "title": str(it.get("report_nm", "")).strip()}
                for it in (data.get("list") or [])[:top]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("dart_fetch_error ticker=%s attempt=%d error=%s", ticker, attempt, exc)
    return None
