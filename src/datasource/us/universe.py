"""미국 S&P500 유니버스 — FinanceDataReader StockListing 기반.

미국 listing은 시총·거래대금 컬럼이 없어 심볼+섹터/산업만 제공한다(2026-06-03 실측).
따라서 거래대금 1차 필터는 OHLCV 수집 단계(us_pipeline)에서 수행하고,
여기서는 후보 풀(심볼+섹터/산업)만 구성한다.

순위는 자주 안 바뀌므로 data/us_universe_cache.json 에 하루 1회만 캐시(외부 호출 최소화).
실패 시 빈 리스트 반환 → 호출측에서 안전 폴백.

design: docs/02-design/features/us-screening.design.md §3·§4
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
_CACHE = _DATA_DIR / "us_universe_cache.json"
_NASDAQ_CACHE = _DATA_DIR / "us_nasdaq_hot_cache.json"


@dataclass(frozen=True)
class USStock:
    """미국 종목 메타 — 스크리닝 유니버스 단위."""
    symbol: str
    name: str
    sector: str = ""
    industry: str = ""


def get_sp500_universe() -> list[USStock]:
    """S&P500 503종목 → [USStock(symbol, name, sector, industry)].

    GICS Sector/Industry 포함(FDR 제공). 하루 1회 캐시.
    FDR/네트워크 실패 시 [] 반환 (호출측 폴백).
    """
    today = date.today().isoformat()

    # 캐시 히트 (같은 날)
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == today and c.get("items"):
                return [USStock(**it) for it in c["items"]]
    except Exception as exc:
        logger.debug("us_universe_cache_read_failed error=%s", exc)

    items: list[dict] = []
    try:
        import FinanceDataReader as fdr

        df = fdr.StockListing("S&P500")
        # 컬럼: Symbol, Name, Sector, Industry
        for _, r in df.iterrows():
            sym = str(r.get("Symbol", "")).strip()
            if not sym:
                continue
            items.append({
                "symbol": sym,
                "name": str(r.get("Name", "")).strip(),
                "sector": str(r.get("Sector", "") or "").strip(),
                "industry": str(r.get("Industry", "") or "").strip(),
            })
    except Exception as exc:
        logger.warning("us_universe_failed error=%s", exc)
        return []

    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(
            json.dumps({"date": today, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("us_universe_cache_write_failed error=%s", exc)

    logger.info("us_universe loaded sp500=%d", len(items))
    return [USStock(**it) for it in items]


# 관심 성장주/양자주 큐레이션 — S&P500 밖 인기 종목(사용자 2026-06-04).
# 나스닥 전체 스캔(3902종목, ~11분)이 비실용적이라 큐레이션으로 대체. 시총/주가 필터로 거름.
# 이름은 한국어(표시용), sector는 테마 라벨. 종목 추가는 여기에 한 줄씩.
US_GROWTH_WATCHLIST: list[USStock] = [
    # 양자컴퓨팅
    USStock("IONQ", "아이온큐", "양자컴퓨팅", "양자컴퓨팅"),
    USStock("RGTI", "리게티", "양자컴퓨팅", "양자컴퓨팅"),
    USStock("QBTS", "디웨이브 퀀텀", "양자컴퓨팅", "양자컴퓨팅"),
    USStock("QUBT", "퀀텀컴퓨팅", "양자컴퓨팅", "양자컴퓨팅"),
    USStock("LAES", "세알사이", "양자보안", "양자보안"),
    USStock("ARQQ", "아르킷 퀀텀", "양자보안", "양자보안"),
    # 우주/방산/eVTOL
    USStock("RKLB", "로켓랩", "우주/방산", "우주"),
    USStock("ASTS", "AST스페이스모바일", "우주/통신", "우주"),
    USStock("LUNR", "인튜이티브 머신스", "우주", "우주"),
    USStock("ACHR", "아처 에비에이션", "eVTOL/항공", "eVTOL"),
    USStock("JOBY", "조비 에비에이션", "eVTOL/항공", "eVTOL"),
    # AI 소프트웨어/클라우드
    USStock("AI", "C3.ai", "AI 소프트웨어", "AI"),
    USStock("BBAI", "빅베어.ai", "AI 소프트웨어", "AI"),
    USStock("SOUN", "사운드하운드 AI", "AI 음성", "AI"),
    USStock("TEM", "템퍼스 AI", "AI 헬스케어", "AI"),
    USStock("CRWV", "코어위브", "AI 클라우드", "AI"),
    USStock("NBIS", "네비우스", "AI 클라우드", "AI"),
    # 핀테크
    USStock("SOFI", "소파이", "핀테크", "핀테크"),
    USStock("AFRM", "어펌", "핀테크", "핀테크"),
    USStock("UPST", "업스타트", "핀테크", "핀테크"),
    USStock("RKT", "로켓 컴퍼니스", "핀테크", "핀테크"),
    # 원자력(SMR)/에너지
    USStock("OKLO", "오클로", "소형원자로(SMR)", "원자력"),
    USStock("SMR", "뉴스케일파워", "소형원자로(SMR)", "원자력"),
    # 전기차/기타 성장주
    USStock("LCID", "루시드", "전기차", "전기차"),
    USStock("CHPT", "차지포인트", "전기차 충전", "전기차"),
    USStock("HIMS", "힘스앤허스", "헬스케어", "헬스케어"),
    USStock("RDDT", "레딧", "인터넷", "인터넷"),
]


def get_extended_universe() -> list[USStock]:
    """S&P500 ∪ 관심 성장주/양자주 큐레이션 (중복 제거, S&P500 우선=GICS 섹터 보존).

    나스닥 전체 스캔보다 빠르고(추가 네트워크 거의 없음) 원하는 테마를 정확히 포착.
    """
    sp = get_sp500_universe()
    seen = {u.symbol for u in sp}
    extra = [w for w in US_GROWTH_WATCHLIST if w.symbol not in seen]
    logger.info("extended_universe sp500=%d watchlist=%d total=%d",
                len(sp), len(extra), len(sp) + len(extra))
    return sp + extra


def _nasdaq_listing() -> list[USStock]:
    """나스닥 전체 listing → USStock (Industry를 sector 자리에 best-effort)."""
    import FinanceDataReader as fdr

    out: list[USStock] = []
    df = fdr.StockListing("NASDAQ")  # Symbol, Name, IndustryCode, Industry
    for _, r in df.iterrows():
        sym = str(r.get("Symbol", "")).strip()
        if not sym:
            continue
        industry = str(r.get("Industry", "") or "").strip()
        out.append(USStock(symbol=sym, name=str(r.get("Name", "")).strip(),
                           sector=industry, industry=industry))
    return out


async def get_nasdaq_hot_universe(
    top: int = 300, min_amount: float = 50_000_000, min_price: float = 5.0,
) -> list[USStock]:
    """나스닥 전체에서 당일 거래대금 상위 top종목 → [USStock] (2단계 필터 1단계).

    1) 나스닥 listing(3902) → 심볼/산업
    2) 가벼운 시세로 당일 거래대금 산출 → min_amount·min_price 필터 → 상위 top
    하루 1회 캐시(거래대금은 매일 바뀌므로 날짜+key). 실패 시 [] (호출측 폴백).
    """
    from src.datasource.us.fdr_source import fetch_us_daily_turnover

    today = date.today().isoformat()
    key = f"{top}-{int(min_amount)}-{int(min_price)}"
    try:
        if _NASDAQ_CACHE.exists():
            c = json.loads(_NASDAQ_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == today and c.get("key") == key and c.get("items"):
                return [USStock(**it) for it in c["items"]]
    except Exception as exc:
        logger.debug("nasdaq_hot_cache_read_failed error=%s", exc)

    try:
        listing = _nasdaq_listing()
    except Exception as exc:
        logger.warning("nasdaq_listing_failed error=%s", exc)
        return []
    if not listing:
        return []

    meta = {u.symbol: u for u in listing}
    turnover = await fetch_us_daily_turnover([u.symbol for u in listing])

    ranked = []
    for sym, info in turnover.items():
        if info["price"] < min_price or info["turnover"] < min_amount:
            continue
        ranked.append((info["turnover"], sym))
    ranked.sort(reverse=True)

    hot = [meta[sym] for _, sym in ranked[:top] if sym in meta]

    try:
        _NASDAQ_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _NASDAQ_CACHE.write_text(
            json.dumps({"date": today, "key": key, "items": [asdict(u) for u in hot]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("nasdaq_hot_cache_write_failed error=%s", exc)

    logger.info("nasdaq_hot_universe loaded candidates=%d hot=%d", len(turnover), len(hot))
    return hot


async def get_combined_universe(
    nasdaq_hot_top: int = 300, min_amount: float = 50_000_000, min_price: float = 5.0,
) -> list[USStock]:
    """S&P500 ∪ 나스닥 거래대금 상위 (중복 제거, S&P500 우선=GICS 섹터 보존)."""
    sp500 = get_sp500_universe()
    hot = await get_nasdaq_hot_universe(nasdaq_hot_top, min_amount, min_price)
    seen = {u.symbol for u in sp500}
    combined = sp500 + [h for h in hot if h.symbol not in seen]
    logger.info("combined_universe sp500=%d nasdaq_hot=%d total=%d",
                len(sp500), len(hot), len(combined))
    return combined
