"""전날 추천 Top3의 현재 상태 — 장중 리포트(midday)용.

pre 리포트(14:50)가 `data/top3_<date>_pre.json`에 남긴 추천 Top3(ticker·이름·추천가)를
직전 거래일분으로 로드하고, KIS 현재가로 ① 추천가 대비 수익률 ② 오늘 등락률을 계산한다.

find_prev_top3 / compute_status 는 순수 함수(결정론 테스트), fetch_*는 KIS 호출.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path("data")
_FNAME_RE = re.compile(r"top3_(\d{4}-\d{2}-\d{2})_pre\.json$")


def find_prev_top3(
    today: str, base_dir: Path | str = _DATA_DIR
) -> tuple[str, list[dict]] | None:
    """today(YYYY-MM-DD) **이전** 거래일의 top3 JSON 중 가장 최근 것 로드.

    반환: (추천일, picks[{ticker,name,price}]) 또는 None(없음). 주말·휴장은 파일이
    없으니 '가장 최근 < today' 선택으로 자연 처리된다.
    """
    base = Path(base_dir)
    if not base.exists():
        return None
    candidates: list[tuple[str, Path]] = []
    for p in base.glob("top3_*_pre.json"):
        m = _FNAME_RE.search(p.name)
        if m and m.group(1) < today:
            candidates.append((m.group(1), p))
    if not candidates:
        return None
    date, path = max(candidates, key=lambda x: x[0])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("prev_top3_load_failed path=%s error=%s", path, exc)
        return None
    picks = data.get("picks", [])
    return (date, picks) if picks else None


def find_prev_candidates(
    today: str, base_dir: Path | str = _DATA_DIR
) -> tuple[str, list[dict]] | None:
    """today 이전 거래일의 종가베팅 후보(candidates_<date>.json) 중 가장 최근 것 로드(#404)."""
    base = Path(base_dir)
    if not base.exists():
        return None
    rx = re.compile(r"candidates_(\d{4}-\d{2}-\d{2})\.json$")
    cands: list[tuple[str, Path]] = []
    for p in base.glob("candidates_*.json"):
        m = rx.search(p.name)
        if m and m.group(1) < today:
            cands.append((m.group(1), p))
    if not cands:
        return None
    date, path = max(cands, key=lambda x: x[0])
    try:
        picks = (json.loads(path.read_text(encoding="utf-8")) or {}).get("picks", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("prev_candidates_load_failed path=%s error=%s", path, exc)
        return None
    return (date, picks) if picks else None


def compute_status(pick: dict, cur_price: float, today_pct: float) -> dict:
    """추천 pick + 현재가 → 상태 dict.

    return_pct = (현재가-추천가)/추천가*100 (추천가 대비 누적 수익률),
    today_pct  = 오늘 등락률(전일종가 대비, KIS prdy_ctrt).
    """
    rec = float(pick.get("price", 0) or 0)
    ret = (cur_price - rec) / rec * 100 if rec else 0.0
    return {
        "ticker": str(pick.get("ticker", "")),
        "name": str(pick.get("name", "")),
        "rec_price": rec,
        "cur_price": float(cur_price or 0),
        "return_pct": round(ret, 2),
        "today_pct": round(float(today_pct or 0), 2),
    }


async def fetch_prev_top3_status(picks: list[dict], adapter) -> list[dict]:
    """각 pick의 KIS 현재가 조회 → 상태 리스트. 개별 실패는 건너뜀(부분 결과 허용)."""
    out: list[dict] = []
    for pk in picks:
        tk = str(pk.get("ticker", "")).strip()
        if not tk:
            continue
        try:
            q = await adapter.get_quote(tk)
            out.append(compute_status(pk, q.price, q.change_pct))
        except Exception as exc:  # noqa: BLE001
            logger.warning("prev_top3_quote_failed ticker=%s error=%s", tk, exc)
    return out
