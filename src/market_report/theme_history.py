"""주도테마 이력 로그 (사용자 2026-06-11).

매 리포트 실행 시 그날의 주도테마(leading_themes)를 data/leading_themes_log.json에 누적 저장하고,
'최근 N일 내 주도테마였는지'를 판정한다. (과거 이력을 안 쌓아둬서 처음엔 비어있고 누적되며 유효해짐)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
_LOG = Path("data/leading_themes_log.json")


def log_leading_themes(themes: list[str], date: str | None = None) -> None:
    """오늘의 주도테마를 로그에 기록(중복 날짜는 덮어씀). 70일치만 유지."""
    themes = [t for t in (themes or []) if t]
    if not themes:
        return
    date = date or datetime.now().strftime("%Y-%m-%d")
    try:
        data = json.loads(_LOG.read_text(encoding="utf-8")) if _LOG.exists() else {}
    except Exception:  # noqa: BLE001
        data = {}
    data[date] = sorted(set(themes))
    cutoff = (datetime.now() - timedelta(days=70)).strftime("%Y-%m-%d")
    data = {d: v for d, v in data.items() if d >= cutoff}
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        _LOG.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("theme_log_write_failed error=%s", exc)


def recent_leading_themes(days: int = 30) -> set[str]:
    """최근 days일 내 주도테마였던 테마명 집합. 이력 없으면 빈 집합."""
    if not _LOG.exists():
        return set()
    try:
        data = json.loads(_LOG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out: set[str] = set()
    for d, themes in data.items():
        if d >= cutoff:
            out.update(themes)
    return out
