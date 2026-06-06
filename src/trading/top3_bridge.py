"""보고서 Top3 ↔ auto_trader 브리지 — pre 리포트가 남긴 top3 JSON 기록/로드.

보고서가 보여준 Top3와 자동매수 종목을 동일하게 보장(일관성)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
DEFAULT_DIR = Path("data")


def _path(date: str, base_dir: Path) -> Path:
    return base_dir / f"top3_{date}_pre.json"


def persist_top3(
    picks: list[dict], mode: str, date: str, base_dir: Path | str = DEFAULT_DIR
) -> Path:
    """ticker/name/price만 추려 JSON 기록. pre_close 전용."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    slim = [
        {"ticker": p["ticker"], "name": p.get("name", ""), "price": p.get("price", 0)}
        for p in picks
    ]
    path = _path(date, base)
    path.write_text(
        json.dumps({"date": date, "mode": mode, "picks": slim}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def persist_candidates(
    picks: list[dict], date: str, base_dir: Path | str = DEFAULT_DIR
) -> Path:
    """종가베팅 후보(candidate_picks)를 JSON 기록 — 다음날 프리/장초 리포트 시초 등락 표시용(#404)."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    slim = [
        {"ticker": p.get("ticker", ""), "name": p.get("name", ""), "theme": p.get("theme", ""),
         "rationale": p.get("rationale", ""), "risk": p.get("risk", "")}
        for p in picks if p.get("ticker")
    ]
    path = base / f"candidates_{date}.json"
    path.write_text(json.dumps({"date": date, "picks": slim}, ensure_ascii=False), encoding="utf-8")
    return path


def load_top3(date: str, base_dir: Path | str = DEFAULT_DIR) -> list[dict] | None:
    """오늘자 top3 picks 로드. 파일 없거나 날짜 불일치면 None(구픽 매매 방지)."""
    path = _path(date, Path(base_dir))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("top3_load_failed error=%s", exc)
        return None
    if data.get("date") != date:
        return None
    return data.get("picks", [])
