"""종목 → 소속 시장 라벨 (KR: 코스피/코스닥, US: 나스닥/NYSE/AMEX) — 사용자 #471.

FDR StockListing으로 시장별 종목 리스트를 받아 {정규화키: 라벨} 맵을 만들고
하루 1회 캐시(data/market_map.json). 네트워크 실패 시 **이전 날짜 캐시라도 사용**
(시장 소속은 거의 안 변해 stale 허용) — 리포트 발송을 막지 않는 best-effort.

라벨 조회(label_any 등)는 순수 dict 조회라 결정론 테스트 가능.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE = Path(__file__).resolve().parents[2] / "data" / "market_map.json"

_KR_MARKET_KO = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KOSDAQ GLOBAL": "코스닥", "KONEX": "코넥스"}
_US_MARKETS = {"NASDAQ": "나스닥", "NYSE": "NYSE", "AMEX": "AMEX"}
# FDR이 쓰는 KRX 일별 상장목록 캐시(GitHub). fdr.StockListing(KOSPI)는 KRX max_work_dt(당일)
# CSV를 찾는데 **장중엔 당일분이 아직 없어 404**(2026-06-08 실측) → 직전 영업일로 직접 폴백.
_KRX_CSV = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
            "refs/heads/master/data/listing/krx/{d}.csv")

# 프로세스 내 싱글턴 — ensure_maps()가 채움
_MAPS: dict[str, dict[str, str]] | None = None


def norm_key(symbol: str) -> str:
    """심볼 정규화 — 표기 차이(BRK-B/BRK.B, 공백) 흡수해 매칭 견고화."""
    return str(symbol or "").strip().upper().replace(".", "").replace("-", "")


def label_from_maps(ticker: str, maps: dict[str, dict[str, str]]) -> str:
    """ticker/symbol → 시장 라벨. 6자리 숫자=KR, 그 외=US. 미발견은 ""(표기 생략)."""
    t = str(ticker or "").strip()
    if not t:
        return ""
    if t.isdigit() and len(t) == 6:
        return maps.get("kr", {}).get(t, "")
    return maps.get("us", {}).get(norm_key(t), "")


def _build_kr_map() -> dict[str, str]:
    """KRX 상장목록 CSV(오늘→과거 7일 walk-back) → {ticker: 코스피/코스닥/코넥스}."""
    import io
    from datetime import timedelta

    import pandas as pd
    import requests

    last_err: Exception | None = None
    for back in range(8):
        d = (date.today() - timedelta(days=back)).isoformat()
        try:
            r = requests.get(_KRX_CSV.format(d=d), timeout=15)
            if r.status_code != 200:  # 주말·휴장·당일 미생성 → 하루 더 과거로
                continue
            df = pd.read_csv(io.StringIO(r.text), dtype={"Code": str})
            kr = {}
            for code, mkt in zip(df["Code"].astype(str), df["Market"].astype(str)):
                label = _KR_MARKET_KO.get(mkt.strip().upper())
                if label:
                    kr[code.zfill(6)] = label
            if kr:
                logger.info("market_map_kr_loaded date=%s n=%d", d, len(kr))
                return kr
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"krx listing csv unavailable (last_err={last_err})")


def _build_maps() -> dict[str, dict[str, str]]:
    """KR(KRX CSV) + US(FDR 거래소 리스팅) → {kr: {ticker: 라벨}, us: {정규화심볼: 라벨}}."""
    import FinanceDataReader as fdr

    kr = _build_kr_map()

    us: dict[str, str] = {}
    for mkt, label in _US_MARKETS.items():
        try:
            df = fdr.StockListing(mkt)  # 컬럼 Symbol/Name (2026-06-08 실측)
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_map_us_failed mkt=%s error=%s", mkt, exc)
            continue
        for sym in df["Symbol"].astype(str):
            k = norm_key(sym)
            if k:
                us.setdefault(k, label)  # 중복 상장은 먼저 온 시장 우선(나스닥)
    return {"kr": kr, "us": us}


def ensure_maps(cache_path: Path | str = _CACHE) -> dict[str, dict[str, str]]:
    """당일 캐시 로드 또는 갱신. 갱신 실패 시 이전 캐시 폴백, 그것도 없으면 빈 맵.

    리포트 파이프라인 시작/발송 전에 1회 호출(동기 — 호출측에서 to_thread 권장).
    """
    global _MAPS
    p = Path(cache_path)
    today = date.today().isoformat()

    cached: dict | None = None
    try:
        if p.exists():
            cached = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("market_map_cache_read_failed error=%s", exc)

    if cached and cached.get("date") == today:
        _MAPS = {"kr": cached.get("kr", {}), "us": cached.get("us", {})}
        return _MAPS

    try:
        maps = _build_maps()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"date": today, **maps}, ensure_ascii=False),
                         encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.debug("market_map_cache_write_failed error=%s", exc)
        _MAPS = maps
        logger.info("market_map_built kr=%d us=%d", len(maps["kr"]), len(maps["us"]))
    except Exception as exc:  # noqa: BLE001
        # 빌드 실패 → stale 캐시 폴백(시장 소속은 거의 불변), 없으면 빈 맵(라벨 생략)
        logger.warning("market_map_build_failed error=%s — stale cache fallback", exc)
        _MAPS = ({"kr": cached.get("kr", {}), "us": cached.get("us", {})}
                 if cached else {"kr": {}, "us": {}})
    return _MAPS


def label_any(ticker: str) -> str:
    """전역 맵에서 라벨 조회 — ensure_maps 미호출/실패 시 ""(라벨만 생략, 안전)."""
    if _MAPS is None:
        return ""
    return label_from_maps(ticker, _MAPS)
