"""시장 투자자 수급 일자별 히스토리 — 매 발행 시 당일치 누적(JSON 영속화).

naver 모바일 API는 '당일치 1건'만 제공하고(다일치 엔드포인트 없음), pykrx 시장투자자는
KRX 로그인 필요(환경 미설정 시 빈응답)라 과거 백필 불가. → 매 거래일 당일치를 저장해
최근 N일을 일자별 표로 보여준다. (거래일이 지날수록 1→2→3일로 채워짐)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "market_flows.json"


def update_flows_history(today: list[dict], keep_days: int = 3) -> list[dict]:
    """당일 수급(today)을 히스토리에 upsert하고 최근 keep_days일을 일자별로 반환.

    today: [{market, personal, foreign, institution, date}] (fetch_market_investor_flows 결과)
    반환: [{date, kospi:{personal,foreign,institution}, kosdaq:{...}}] 최신순, 최대 keep_days.
    """
    # 1) 기존 히스토리 로드 {date: {KOSPI:{p,f,i}, KOSDAQ:{p,f,i}}}
    store: dict[str, dict] = {}
    try:
        if _CACHE.exists():
            store = json.loads(_CACHE.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("flows_history_read_failed error=%s", exc)

    # 2) 당일치 upsert (날짜별, 시장별)
    for f in (today or []):
        d = str(f.get("date", "")).strip()
        mk = str(f.get("market", "")).strip()
        if not d or not mk:
            continue
        store.setdefault(d, {})[mk] = {
            "personal": int(f.get("personal", 0)),
            "foreign": int(f.get("foreign", 0)),
            "institution": int(f.get("institution", 0)),
        }

    # 3) 오래된 날짜 정리 (최근 30일만 보관) + 저장
    try:
        for old in sorted(store.keys(), reverse=True)[30:]:
            store.pop(old, None)
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("flows_history_write_failed error=%s", exc)

    # 4) 최근 keep_days일 (최신순) → 일자별 dict
    out: list[dict] = []
    for d in sorted(store.keys(), reverse=True)[:keep_days]:
        day = store[d]
        out.append({
            "date": d,
            "kospi": day.get("KOSPI", {}),
            "kosdaq": day.get("KOSDAQ", {}),
        })
    return out
