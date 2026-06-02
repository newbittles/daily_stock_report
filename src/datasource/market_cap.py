"""종목 시가총액 맵 — FDR StockListing 기반, 일1회 캐시.

리포트(Top3·전략스크린·종가베팅·대시보드)에 시총을 억 단위로 표기하기 위한 공통 소스.
FDR 'KRX' 목록의 Marcap(원) 필드 사용 (백테스트에서 검증된 필드).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "market_cap.json"


def get_market_cap_map() -> dict[str, int]:
    """{종목코드: 시가총액(원)}. 일1회 캐시. 실패 시 빈 dict."""
    today = date.today().isoformat()
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == today and c.get("map"):
                return {str(k): int(v) for k, v in c["map"].items()}
    except Exception as exc:
        logger.debug("marcap_cache_read_failed error=%s", exc)

    mapping: dict[str, int] = {}
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        for _, r in df.iterrows():
            code = str(r.get("Code", "")).zfill(6)
            mc = r.get("Marcap")
            if code and mc and mc == mc:  # NaN 제외
                mapping[code] = int(mc)
    except Exception as exc:
        logger.warning("marcap_map_failed error=%s", exc)
        return {}

    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps({"date": today, "map": mapping}), encoding="utf-8")
    except Exception as exc:
        logger.debug("marcap_cache_write_failed error=%s", exc)
    logger.info("marcap_map_built codes=%d", len(mapping))
    return mapping


def format_marcap(won: int | float | None) -> str:
    """시총(원) → 억 단위 표기. 1조 이상은 '조 억' 가독 표기, 미만은 억."""
    if not won or won <= 0:
        return ""
    eok = won / 1e8  # 억
    if eok >= 10000:  # 1조 이상
        jo = int(eok // 10000)
        rem = int(round(eok % 10000))
        return f"{jo:,}조" + (f" {rem:,}억" if rem else "")
    return f"{eok:,.0f}억"
