"""주달(judal.co.kr) — 종목별 테마 분류. 네이버보다 트렌드 반영·정확.

judal은 종목→테마 직접조회가 막혀(stockList&code= 본문 고정 + 종목분석은 robots 차단),
역방향(테마별 종목, ?view=stockList&themeIdx=N)만 가능. 상위 테마들을 역인덱스해
{종목코드: 테마명}을 구성한다. data/judal_theme_map.json 일1회 캐시.

robots.txt: Allow:/ (stockAI·userLogin만 차단) → 테마/종목 리스트는 허용.
§7: 랜덤 딜레이 + 배치 휴식 + robot 감지 2회 시 즉시 중단(모은 만큼 사용).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


async def _fetch(url: str) -> str:
    """judal 전용 fetch — 본문 키워드 검사 없이 status만 확인.

    judal 페이지 본문엔 '차단'·'robot' 같은 단어가 정상적으로 들어있어
    공용 http.fetch의 hard_stop 키워드 검사가 오탐한다. 여기선 status code로만 판단.
    """
    import httpx
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": _UA},
                                 follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code in (429, 403, 503):
            raise RuntimeError(f"robot/blocked status={resp.status_code}")
        resp.raise_for_status()
        return resp.content.decode("utf-8", errors="ignore")

THEME_LIST_URL = "https://judal.co.kr/?view=themeList"
THEME_STOCKS_URL = "https://judal.co.kr/?view=stockList&themeIdx={idx}"
THEME_LINK_URL = "https://judal.co.kr/?view=stockList&themeIdx={idx}"
# 사이드바 테마 목록: themeIdx=N" ...list-group-item-sub...><span>테마명(종목수)</span>
_THEME_PATTERN = re.compile(
    r'themeIdx=(\d+)"[^>]*list-group-item-sub[^>]*>\s*<span>([^<]+)</span>')
_CODE_PATTERN = re.compile(r'item/main\.nhn\?code=(\d{6})')
_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "judal_theme_map.json"

# 비테마성(분류·지수·그룹·계절/이벤트) — 종목 대표 테마로 부적합 → 제외
_NONTHEME = ("MSCI", "코스피", "코스닥", "KRX", "밸류업", "지주", "그룹",
             "ETF", "ETN", "ETP", "레버리지", "인버스", "선물",
             "우선주", "배당", "PER", "PBR", "스팩", "신규상장", "관리종목",
             "액면", "증자", "출자", "지분",
             # 계절·날씨·일시 이벤트 (대표 테마 부적합)
             "제습기", "공기청정기", "에어컨", "난방", "보일러", "폭염", "한파", "황사",
             "미세먼지", "장마", "태풍", "김장", "모기", "빙과", "마스크", "감기", "독감")


def _is_nontheme(name: str) -> bool:
    return any(k in name for k in _NONTHEME)


_THEME_CAP = 45  # 종목수 상한 — 초과 테마는 범용(스마트폰·밸류업·제습기 등)으로 보고 제외


def _pick_theme(cands: list) -> dict:
    """후보 테마들 → 대표 1개. 비테마 제외 + 범용(종목수 과다) 제외 후 그 중 최대.

    종목수 최소(specific)=네온가스 마이너, 무제한 최대=스마트폰/제습기 범용 → 둘 다 부정확.
    비테마 제외 + 종목수 _THEME_CAP 이하(핵심 테마) 중 최대 = 반도체·2차전지 등 대표 선정.
    """
    real = [c for c in cands if not _is_nontheme(c[0])]
    if not real:  # 유효 테마 없음(계절/비테마뿐) → 빈값 → pipeline에서 업종 폴백
        return {"theme": "", "idx": ""}
    pool = [c for c in real if c[2] <= _THEME_CAP] or real
    name, idx, _cnt = max(pool, key=lambda c: c[2])
    return {"theme": name, "idx": idx}


async def build_judal_theme_map(max_themes: int = 318) -> dict[str, dict]:
    """{종목코드: {"theme": 대표테마명, "idx": themeIdx}} 역인덱스. 일1회 캐시.

    종목별 '모든' 테마를 모은 뒤 → 비테마성(MSCI·그룹·지주 등) 제외 →
    종목수 적은(specific) 테마를 대표로 선정. robot 감지 2회 시 중단(모은 만큼).
    """
    today = date.today().isoformat()
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == today and c.get("cand"):
                # 후보에서 대표 재선정 (선정 로직 변경 시 재크롤링 불필요)
                return {code: _pick_theme(lst) for code, lst in c["cand"].items()}
    except Exception as exc:
        logger.debug("judal_cache_read_failed error=%s", exc)

    # 종목별 모든 테마 후보: code -> [(theme_name, idx, stock_count)]
    cand: dict[str, list[tuple[str, str, int]]] = {}
    try:
        html = await _fetch(THEME_LIST_URL)
        themes, seen = [], set()
        for idx, raw in _THEME_PATTERN.findall(html):
            m = re.search(r"\((\d+)\)\s*$", raw)
            count = int(m.group(1)) if m else 999
            name = re.sub(r"\(\d+\)\s*$", "", raw).strip()
            if idx not in seen and name:
                seen.add(idx)
                themes.append((idx, name, count))
        robot_hits = 0
        for i, (idx, name, count) in enumerate(themes[:max_themes]):
            await asyncio.sleep(random.uniform(0.8, 1.8))  # §7 딜레이
            if i and i % 15 == 0:
                await asyncio.sleep(random.uniform(4.0, 8.0))  # 배치 휴식
            try:
                page = await _fetch(THEME_STOCKS_URL.format(idx=idx))
            except Exception as exc:
                if "robot" in str(exc).lower() or "blocked" in str(exc).lower():
                    robot_hits += 1
                    if robot_hits >= 2:
                        logger.warning("judal_robot_halt themes_done=%d", i)
                        break
                continue
            for code in dict.fromkeys(_CODE_PATTERN.findall(page)):
                cand.setdefault(code, []).append((name, idx, count))
    except Exception as exc:
        logger.warning("judal_theme_map_failed error=%s", exc)
        return {}

    # 대표 테마 선정 (비테마 제외 → 종목수 최대)
    mapping = {code: _pick_theme(lst) for code, lst in cand.items()}

    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        # 후보(cand) 저장 → 선정 로직 변경 시 재크롤링 없이 재선정
        _CACHE.write_text(json.dumps({"date": today, "cand": cand}, ensure_ascii=False),
                          encoding="utf-8")
    except Exception as exc:
        logger.debug("judal_cache_write_failed error=%s", exc)

    logger.info("judal_theme_map_built themes=%d codes=%d", min(len(themes), max_themes), len(mapping))
    return mapping


def judal_theme_url(idx: str | int) -> str:
    return THEME_LINK_URL.format(idx=idx)
