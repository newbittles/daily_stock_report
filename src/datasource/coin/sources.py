"""코인 시세 소스 — 업비트 KRW + CoinGecko USD/글로벌 + 코인 공포탐욕(alternative.me).

전부 무인증 공개 API. 전역 §7 적용: 재시도 3회 + 랜덤 지수백오프, HARD STOP(429/503) 즉시 중단.
파서는 순수 함수로 분리(_parse_*) — 결정론 단위 테스트 대상. fetch는 fear_greed.py 패턴.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_UPBIT_URL = "https://api.upbit.com/v1/ticker"
_UPBIT_DAYS_URL = "https://api.upbit.com/v1/candles/days"
_UPBIT_4H_URL = "https://api.upbit.com/v1/candles/minutes/240"
_GECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
_GECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
_FNG_URL = "https://api.alternative.me/fng/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_FNG_CACHE = Path(__file__).resolve().parents[3] / "data" / "coin_fng_cache.json"

# 유니버스 — 6개 고정(사용자 2026-06-07 정보과다 축소): USDT(달러 프리미엄, 최상단)
# + BTC·ETH·XRP·SOL·DOGE. 업비트 KRW 마켓 기준.
COIN_UNIVERSE: list[dict] = [
    # USDT: 시세·김프(달러 프리미엄)만 — 스테이블 평탄차트가 A/C/D를 오탐하므로 분석 제외
    {"sym": "USDT", "name_ko": "테더", "upbit": "KRW-USDT", "gecko": "tether", "analyze": False},
    {"sym": "BTC", "name_ko": "비트코인", "upbit": "KRW-BTC", "gecko": "bitcoin"},
    {"sym": "ETH", "name_ko": "이더리움", "upbit": "KRW-ETH", "gecko": "ethereum"},
    {"sym": "XRP", "name_ko": "리플", "upbit": "KRW-XRP", "gecko": "ripple"},
    {"sym": "SOL", "name_ko": "솔라나", "upbit": "KRW-SOL", "gecko": "solana"},
    {"sym": "DOGE", "name_ko": "도지코인", "upbit": "KRW-DOGE", "gecko": "dogecoin"},
]

FNG_RATING_KO = {
    "extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립",
    "greed": "탐욕", "extreme greed": "극단적 탐욕",
}


# ─── 순수 파서 ────────────────────────────────────────────────────────────────


def _parse_upbit(payload: list[dict]) -> dict[str, dict]:
    """업비트 /v1/ticker 응답 → {market: {krw, change_pct, value_24h}}."""
    out: dict[str, dict] = {}
    for t in payload or []:
        m = t.get("market")
        if not m:
            continue
        rate = t.get("signed_change_rate")
        out[m] = {
            "krw": float(t["trade_price"]) if t.get("trade_price") is not None else None,
            "change_pct": float(rate) * 100 if rate is not None else None,
            "value_24h": float(t.get("acc_trade_price_24h") or 0.0),
        }
    return out


def _parse_gecko_markets(payload: list[dict]) -> dict[str, dict]:
    """CoinGecko /coins/markets 응답 → {id: {usd, change_pct, mcap, rank}}. 결측 허용."""
    out: dict[str, dict] = {}
    for c in payload or []:
        cid = c.get("id")
        if not cid:
            continue
        chg = c.get("price_change_percentage_24h")
        out[cid] = {
            "usd": float(c["current_price"]) if c.get("current_price") is not None else None,
            "change_pct": float(chg) if chg is not None else None,
            "mcap": float(c.get("market_cap") or 0.0),
            "rank": c.get("market_cap_rank"),
        }
    return out


def _parse_gecko_global(payload: dict) -> dict | None:
    """CoinGecko /global 응답 → {btc_dominance, mcap_change_24h}. 결측 시 None."""
    d = (payload or {}).get("data") or {}
    dom = (d.get("market_cap_percentage") or {}).get("btc")
    if dom is None:
        return None
    chg = d.get("market_cap_change_percentage_24h_usd")
    return {"btc_dominance": float(dom),
            "mcap_change_24h": float(chg) if chg is not None else None}


def _parse_fng(payload: dict) -> dict | None:
    """alternative.me /fng/ 응답 → {score, rating, rating_ko}. 결측 시 None."""
    rows = (payload or {}).get("data") or []
    if not rows:
        return None
    try:
        score = int(rows[0]["value"])
    except (KeyError, TypeError, ValueError):
        return None
    rating = str(rows[0].get("value_classification", "")).strip()
    return {"score": score, "rating": rating,
            "rating_ko": FNG_RATING_KO.get(rating.lower(), rating)}


def _parse_upbit_candles(payload: list[dict]) -> list:
    """업비트 캔들 응답(최신순) → Candle 리스트(과거→현재).

    ⚠️ volume은 float 유지 — 코인 거래량은 소수(예: 0.5 BTC)라 int 캐스팅 시 0이 되어
    거래량 조건(E 투매 2x 등)이 왜곡됨. Candle 타입힌트(int)는 미강제(주식 호환)."""
    from src.datasource.base import Candle
    out: list[Candle] = []
    for c in payload or []:
        try:
            out.append(Candle(
                date=str(c.get("candle_date_time_kst", ""))[:10].replace("-", ""),
                open=float(c["opening_price"]), high=float(c["high_price"]),
                low=float(c["low_price"]), close=float(c["trade_price"]),
                volume=float(c.get("candle_acc_trade_volume") or 0.0),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    out.reverse()  # 업비트는 최신순 응답 → 지표 계산용 과거→현재로
    return out


# ─── fetch (§7: 재시도 3·랜덤 백오프·HARD STOP) ──────────────────────────────


def _get_json(url: str, params: dict | None = None, label: str = "coin") -> object | None:
    import requests

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=12)
        except requests.RequestException as exc:
            logger.warning("%s_req_error attempt=%d/3 error=%s", label, attempt + 1, exc)
        else:
            if r.status_code in (429, 503):  # §7 HARD STOP — 재시도 금지
                logger.warning("%s_hard_stop status=%d", label, r.status_code)
                return None
            if r.status_code != 200:
                logger.warning("%s_bad_status status=%d", label, r.status_code)
                return None
            try:
                return r.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s_parse_error error=%s", label, exc)
                return None
        if attempt < 2:
            time.sleep(random.uniform(2.0 * (2 ** attempt), 4.0 * (2 ** attempt)))
    return None


async def fetch_upbit_tickers(markets: list[str]) -> dict[str, dict]:
    """업비트 현재가 일괄 → {market: {krw, change_pct, value_24h}}. 실패 시 빈 dict."""
    payload = await asyncio.to_thread(
        _get_json, _UPBIT_URL, {"markets": ",".join(markets)}, "upbit"
    )
    return _parse_upbit(payload) if isinstance(payload, list) else {}


async def fetch_upbit_daily(market: str, count: int = 200) -> list:
    """업비트 일봉(과거→현재). 실패 시 빈 리스트. 120MA·전략평가용 200봉."""
    payload = await asyncio.to_thread(
        _get_json, _UPBIT_DAYS_URL, {"market": market, "count": count}, "upbit_days"
    )
    return _parse_upbit_candles(payload) if isinstance(payload, list) else []


async def fetch_upbit_4h(market: str, count: int = 200) -> list:
    """업비트 4시간봉(과거→현재). 실패 시 빈 리스트. RSI·20MA 이격용."""
    payload = await asyncio.to_thread(
        _get_json, _UPBIT_4H_URL, {"market": market, "count": count}, "upbit_4h"
    )
    return _parse_upbit_candles(payload) if isinstance(payload, list) else []


async def fetch_gecko_markets(ids: list[str]) -> dict[str, dict]:
    """CoinGecko USD 시세 일괄 → {id: {usd, change_pct, mcap, rank}}. 실패 시 빈 dict."""
    params = {"vs_currency": "usd", "ids": ",".join(ids), "per_page": len(ids)}
    payload = await asyncio.to_thread(_get_json, _GECKO_MARKETS_URL, params, "gecko")
    return _parse_gecko_markets(payload) if isinstance(payload, list) else {}


async def fetch_gecko_global() -> dict | None:
    """BTC 도미넌스 + 전체 시총 24h 변화율. 실패 시 None(섹션 생략)."""
    payload = await asyncio.to_thread(_get_json, _GECKO_GLOBAL_URL, None, "gecko_global")
    return _parse_gecko_global(payload) if isinstance(payload, dict) else None


async def fetch_coin_fng(use_cache: bool = True) -> dict | None:
    """코인 공포탐욕지수(0~100). 일1회 캐시(리포트 1일 1회라 호출 최소). 실패 시 None."""
    if use_cache:
        try:
            if _FNG_CACHE.exists():
                c = json.loads(_FNG_CACHE.read_text(encoding="utf-8"))
                if c.get("date") == date.today().isoformat():
                    return c.get("value")
        except Exception as exc:  # noqa: BLE001
            logger.debug("coin_fng_cache_read_failed error=%s", exc)
    payload = await asyncio.to_thread(_get_json, _FNG_URL, {"limit": 1}, "coin_fng")
    v = _parse_fng(payload) if isinstance(payload, dict) else None
    if v:
        try:
            _FNG_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _FNG_CACHE.write_text(
                json.dumps({"date": date.today().isoformat(), "value": v}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("coin_fng_cache_write_failed error=%s", exc)
    return v
