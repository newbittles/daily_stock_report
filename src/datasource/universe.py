"""시총 상위 유니버스 — HTS 조건검색식 디텍팅을 위한 넓은 후보 풀.

FinanceDataReader로 코스피·코스닥 시가총액(Marcap) 상위 N종목을 가져온다.
시총 순위는 자주 안 바뀌므로 data/universe_cache.json 에 하루 1회만 캐시(외부 호출 최소화).
실패 시 빈 리스트 반환 → 기존 유니버스(핫종목+관심종목)로 안전 폴백.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "universe_cache.json"


def get_market_cap_universe(
    kospi_top: int = 200, kosdaq_top: int = 100, min_amount: float = 0,
) -> list[tuple[str, str]]:
    """코스피 시총 상위 kospi_top + 코스닥 시총 상위 kosdaq_top → [(ticker, name)].

    min_amount(원) > 0이면 당일 거래대금 하한 필터 (거래 활발한 종목만 → 스캔 부하↓).
    하루 1회 캐시. FDR/네트워크 실패 시 [] (호출측에서 기존 유니버스로 폴백).
    """
    today = date.today().isoformat()
    key = f"{kospi_top}-{kosdaq_top}-{int(min_amount)}"

    # 캐시 히트 (같은 날·같은 규모)
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == today and c.get("key") == key:
                return [(str(t), str(n)) for t, n in c.get("items", [])]
    except Exception as exc:
        logger.debug("universe_cache_read_failed error=%s", exc)

    items: list[tuple[str, str]] = []
    try:
        import FinanceDataReader as fdr
        for mkt, top in (("KOSPI", kospi_top), ("KOSDAQ", kosdaq_top)):
            df = fdr.StockListing(mkt)
            df = df.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False).head(top)
            if min_amount > 0 and "Amount" in df.columns:
                df = df[df["Amount"].fillna(0) >= min_amount]
            items += [(str(r["Code"]).zfill(6), str(r["Name"])) for _, r in df.iterrows()]
    except Exception as exc:
        logger.warning("market_cap_universe_failed error=%s", exc)
        return []

    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(
            json.dumps({"date": today, "key": key, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("universe_cache_write_failed error=%s", exc)

    logger.info("market_cap_universe loaded kospi=%d kosdaq=%d total=%d", kospi_top, kosdaq_top, len(items))
    return items
