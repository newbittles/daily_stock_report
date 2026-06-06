"""SEIBro 미매핑 종목/ETF의 티커·한국어명 AI 보강 + ISIN 캐시 (사용자 #441/#442).

seibro_symbols(검증 매핑)에 없는 ISIN은 영문명만 있어 티커/한국어가 비어 있다.
Gemini로 (티커, 한국어명)을 1회 일괄 추론해 data/seibro_enrich.json에 ISIN 캐시 → 재사용.
AI 티커는 best-effort(모르면 빈값). 링크는 이름 검색이라 티커가 틀려도 링크엔 영향 없음.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "seibro_enrich.json"
_CACHE: dict[str, dict] = {}
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
        logger.debug("seibro_enrich_load_failed error=%s", exc)


def _save() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("seibro_enrich_save_failed error=%s", exc)


def cached(isin: str) -> dict:
    """캐시된 {ticker, ko} (없으면 {}). HTTP/AI 안 함 — 동기 안전."""
    _load()
    return _CACHE.get(isin, {})


def _ai_resolve(items: dict[str, str]) -> dict[str, dict]:
    """{isin: 영문명} → {isin: {ticker, ko}} Gemini 일괄. 실패 시 {}."""
    from src.config.settings import get_settings

    s = get_settings()
    if not getattr(s, "gemini_api_key", "") or not items:
        return {}
    try:
        import re

        from google import genai

        client = genai.Client(api_key=s.gemini_api_key)
        lines = "\n".join(f"{i}\t{nm}" for i, nm in items.items())
        prompt = (
            "다음은 미국 상장 ETF/주식의 ISIN과 영문명이다. 각각 (1)미국 거래 티커 (2)한국 투자자용 한국어명을 알려줘.\n"
            'JSON만 출력(설명 금지): {"ISIN":{"ticker":"TICK","ko":"한국어명"}}.\n'
            "티커가 확실치 않으면 ticker는 빈 문자열로. ETF는 한국어명을 'OOO ETF' 형태로.\n" + lines
        )
        resp = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
        m = re.search(r"\{.*\}", resp.text or "", re.S)
        if not m:
            return {}
        data = json.loads(m.group(0))
        return {
            k: {"ticker": str(v.get("ticker", "")).strip().upper(),
                "ko": str(v.get("ko", "")).strip()}
            for k, v in data.items() if isinstance(v, dict)
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("seibro_enrich_ai_failed error=%s", exc)
        return {}


async def enrich(items: list[tuple[str, str]]) -> dict[str, dict]:
    """[(isin, name_en)] → {isin: {ticker, ko}}. 캐시 우선, 미캐시만 AI 1회 일괄(성공 시 캐시)."""
    import asyncio

    _load()
    need = {i: nm for i, nm in items if i and i not in _CACHE}
    if need:
        got = await asyncio.to_thread(_ai_resolve, need)
        if got:  # AI 호출 성공 시에만 캐시(실패는 다음 실행 재시도)
            for i in need:
                _CACHE[i] = got.get(i, {})
            _save()
        logger.info("seibro_enrich n_need=%d n_got=%d", len(need), len(got))
    return {i: _CACHE.get(i, {}) for i, _ in items}
