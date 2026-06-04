"""미국 증시 시세 — FinanceDataReader 기반 (us_morning 리포트용).

KIS 해외 API 대신 무료 FDR 사용. 미국장 마감 후(한국 아침) 지연 일봉으로
지수·빅테크·섹터 ETF 등락을 수집한다. 동기 라이브러리라 asyncio.to_thread로 감싼다.
FDR 심볼 검증 완료(2026-06-02): US500·IXIC·DJI·^SOX·NVDA·AAPL·TSLA 등.

design: docs/02-design/features/us-morning-report.design.md (U1 데이터소스=FDR)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from src.datasource.base import Candle
from src.datasource.us.symbols import to_yf_symbol

_OHLCV_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "us_ohlcv_cache.json"

logger = logging.getLogger(__name__)

# 지수 (심볼 → 표시명)
US_INDICES = {
    "US500": "S&P500",
    "IXIC": "나스닥",
    "DJI": "다우",
    "^SOX": "필라델피아반도체",
}

# 빅테크 + 주요 종목 (심볼 → 한글명)
US_BIGTECH = {
    "NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트",
    "GOOGL": "알파벳", "AMZN": "아마존", "META": "메타", "TSLA": "테슬라",
    "AVGO": "브로드컴", "AMD": "AMD", "NFLX": "넷플릭스",
}

# 섹터/테마 대표 ETF (강세 테마 추출용) — 심볼 → 테마명
US_SECTORS = {
    "SOXX": "반도체", "XLK": "기술/IT", "XLE": "에너지", "XLF": "금융",
    "XLV": "헬스케어/바이오", "XLY": "경기소비재", "ITA": "방산/우주항공",
    "TAN": "태양광/신재생", "LIT": "2차전지/리튬",
}


@dataclass(frozen=True)
class USQuote:
    symbol: str
    name: str
    price: float
    change_pct: float  # 전일 대비 등락률(%)
    date: str = ""     # 최신 데이터 거래일(YYYY-MM-DD) — 휴장 신선도 판정용
    volume: int = 0    # 최신 거래량 (거래량 상위 섹터 산출용)


def _fetch_quotes_sync(symbols: dict[str, str]) -> list[USQuote]:
    """동기 — 각 심볼 최근 2영업일 종가로 등락률 계산."""
    import FinanceDataReader as fdr
    from datetime import datetime, timedelta

    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    out: list[USQuote] = []
    for sym, name in symbols.items():
        try:
            df = fdr.DataReader(sym, start)
            if df is None or len(df) < 2 or "Close" not in df.columns:
                continue
            closes = df["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            chg = (last - prev) / prev * 100 if prev else 0.0
            last_date = ""
            try:
                last_date = closes.index[-1].strftime("%Y-%m-%d")
            except Exception:
                pass
            vol = 0
            try:
                if "Volume" in df.columns:
                    vol = int(df["Volume"].dropna().iloc[-1])
            except Exception:
                vol = 0
            out.append(USQuote(sym, name, round(last, 2), round(chg, 2), last_date, vol))
        except Exception as exc:  # noqa: BLE001
            logger.warning("us_fetch_failed symbol=%s error=%s", sym, exc)
    return out


# 각 섹터(US_SECTORS 한국어 테마명)의 대장주 — 시총 1등(사용자 2026-06-04). FDR 심볼.
US_SECTOR_LEADER: dict[str, str] = {
    "반도체": "NVDA", "기술/IT": "MSFT", "에너지": "XOM", "금융": "JPM",
    "헬스케어/바이오": "LLY", "경기소비재": "AMZN", "방산/우주항공": "GE",
    "태양광/신재생": "FSLR", "2차전지/리튬": "ALB",
}


async def fetch_sector_leaders(sector_names: list[str]) -> list[dict]:
    """표시된 섹터들의 대장주 시세 → [{sector, symbol, name, price, change_pct}].

    섹터별 대장(시총1등) 시세를 한 번에 조회. 한국어명은 korean_name로 표시단에서.
    """
    from src.datasource.us.names_ko import korean_name

    pairs = [(sec, US_SECTOR_LEADER[sec]) for sec in dict.fromkeys(sector_names)
             if sec in US_SECTOR_LEADER]
    if not pairs:
        return []
    quotes = await asyncio.to_thread(_fetch_quotes_sync, {tk: tk for _, tk in pairs})
    qmap = {q.symbol: q for q in quotes}
    out: list[dict] = []
    for sec, tk in pairs:
        q = qmap.get(tk)
        if q:
            out.append({"sector": sec, "symbol": tk, "name": korean_name(tk, tk),
                        "price": round(q.price, 2), "change_pct": round(q.change_pct, 2)})
    return out


async def fetch_us_top_volume_sectors(top: int = 5) -> list[USQuote]:
    """섹터 ETF 거래대금(종가×거래량) 상위 top개 → 핫테마용(거래대금 내림차순, 사용자)."""
    quotes = await asyncio.to_thread(_fetch_quotes_sync, US_SECTORS)
    ranked = [q for q in quotes if q.volume > 0]
    ranked.sort(key=lambda q: q.price * q.volume, reverse=True)
    return ranked[:top]


async def fetch_us_indices() -> list[USQuote]:
    """미국 주요 지수 등락 (S&P500·나스닥·다우·SOX)."""
    return await asyncio.to_thread(_fetch_quotes_sync, US_INDICES)


async def fetch_us_bigtech() -> list[USQuote]:
    """빅테크/주요 종목 등락 → 상승률 내림차순."""
    quotes = await asyncio.to_thread(_fetch_quotes_sync, US_BIGTECH)
    return sorted(quotes, key=lambda q: q.change_pct, reverse=True)


async def fetch_us_sectors(threshold: float = 1.0) -> list[USQuote]:
    """섹터 ETF 등락 → **전체** 섹터 상승률 내림차순(강세/약세 모두). threshold 무시(호환).

    강세 섹터=앞쪽, 약세 섹터=뒤쪽(가장 음수). 호출측에서 top/bottom 슬라이스.
    """
    quotes = await asyncio.to_thread(_fetch_quotes_sync, US_SECTORS)
    return sorted(quotes, key=lambda q: q.change_pct, reverse=True)


# ─── 미국 종목 스크리닝용 OHLCV 배치 (us_screening) ──────────────────────────
# 개별 FDR 호출은 503종목×0.3s≈150s로 무겁다 → yfinance 일괄 다운로드로 부하 절감.
# design: docs/02-design/features/us-screening.design.md §3·§5


def _df_to_candles(df) -> list[Candle]:
    """yfinance OHLCV DataFrame → list[Candle] (NaN·결측 행 제거, 날짜 오름차순)."""
    import math

    out: list[Candle] = []
    for idx, row in df.iterrows():
        try:
            o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
            v = row.get("Volume", 0)
            if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in (o, h, l, c)):
                continue
            try:
                date_str = idx.strftime("%Y%m%d")
            except Exception:
                date_str = str(idx)[:10].replace("-", "")
            vol = 0
            try:
                vol = int(v) if v == v else 0  # NaN 가드
            except Exception:
                vol = 0
            out.append(Candle(
                date=date_str,
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=vol,
            ))
        except Exception:
            continue
    return out


def _parse_ohlcv_chunk(data, syms: list[str], out: dict[str, list[Candle]]) -> None:
    """yfinance 다운로드 결과 → out 에 {symbol: [Candle]} 누적.

    syms 는 FDR 심볼. yfinance 결과는 yf 심볼(BRK-B 등)로 키잉돼 있으므로
    to_yf_symbol()로 조회하고 원래 FDR 키(sym)로 저장한다(양방향 정규화)."""
    if len(syms) == 1:
        out[syms[0]] = _df_to_candles(data)
        return
    for sym in syms:
        try:
            sub = data[to_yf_symbol(sym)].dropna(how="all")
            out[sym] = _df_to_candles(sub)
        except Exception as exc:  # noqa: BLE001
            logger.debug("us_ohlcv_missing symbol=%s error=%s", sym, exc)


def _fetch_ohlcv_batch_sync(symbols: list[str], days: int,
                            chunk_size: int = 200) -> dict[str, list[Candle]]:
    """동기 — 청크별 yfinance 일봉 다운로드 → {symbol: [Candle]}.

    대량(수백~수천) 한방 호출은 yfinance rate limit(429)에 취약하므로(전역 §7),
    청크로 나눠 배치 사이 랜덤 딜레이를 둔다. rate limit 연속 2회 → 중단(모은 만큼 사용).
    """
    import random
    import time
    from datetime import datetime, timedelta

    import yfinance as yf

    if not symbols:
        return {}
    # 일봉 days개 확보 위해 주말·휴장 고려 여유(×1.8 + 10일)
    start = (datetime.now() - timedelta(days=int(days * 1.8) + 10)).strftime("%Y-%m-%d")
    out: dict[str, list[Candle]] = {}
    rate_hits = 0
    n_chunks = (len(symbols) + chunk_size - 1) // chunk_size
    for ci in range(n_chunks):
        part = symbols[ci * chunk_size:(ci + 1) * chunk_size]
        try:
            data = yf.download(
                [to_yf_symbol(s) for s in part], start=start, group_by="ticker",
                auto_adjust=False, threads=True, progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "rate" in msg or "too many" in msg or "429" in msg:
                rate_hits += 1
                logger.warning("us_ohlcv_rate_limited chunk=%d/%d hits=%d",
                               ci + 1, n_chunks, rate_hits)
                if rate_hits >= 2:  # §7 — 연속 rate limit 즉시 중단
                    logger.warning("us_ohlcv_halt collected=%d", len(out))
                    break
                time.sleep(random.uniform(10.0, 20.0))
                continue
            logger.warning("us_ohlcv_chunk_failed chunk=%d error=%s", ci + 1, exc)
            continue
        _parse_ohlcv_chunk(data, part, out)
        if ci < n_chunks - 1:
            time.sleep(random.uniform(1.5, 3.5))  # §7 배치 사이 랜덤 휴식
    return out


def _load_ohlcv_cache(days: int) -> dict[str, list[Candle]]:
    """당일·동일 days 캐시 로드 → {symbol: [Candle]}. 불일치/없음 시 {}."""
    try:
        if _OHLCV_CACHE.exists():
            c = json.loads(_OHLCV_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == date.today().isoformat() and c.get("days") == days:
                return {s: [Candle(**cd) for cd in cds]
                        for s, cds in c.get("candles", {}).items()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("ohlcv_cache_read_failed error=%s", exc)
    return {}


def _save_ohlcv_cache(days: int, candles: dict[str, list[Candle]]) -> None:
    try:
        _OHLCV_CACHE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": date.today().isoformat(),
            "days": days,
            "candles": {s: [asdict(c) for c in cs] for s, cs in candles.items()},
        }
        _OHLCV_CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("ohlcv_cache_write_failed error=%s", exc)


async def fetch_us_ohlcv_batch(
    symbols: list[str], days: int = 120, use_cache: bool = True,
) -> dict[str, list[Candle]]:
    """미국 종목 일봉 일괄 수집 → {symbol: [Candle]} (오름차순).

    use_cache: 같은 날·같은 days 캐시(`data/us_ohlcv_cache.json`)된 심볼은 재다운로드
    생략(rate limit 회피·반복 실측 고속화). 캐시에 없는 심볼만 다운로드 후 누적 저장.
    """
    symbols = list(symbols)
    cache = _load_ohlcv_cache(days) if use_cache else {}
    cached = {s: cache[s] for s in symbols if s in cache}
    missing = [s for s in symbols if s not in cache]

    fresh: dict[str, list[Candle]] = {}
    if missing:
        fresh = await asyncio.to_thread(_fetch_ohlcv_batch_sync, missing, days)
        if use_cache and fresh:
            merged = {**cache, **fresh}
            _save_ohlcv_cache(days, merged)

    return {**cached, **fresh}


# ─── 1단계: 당일 거래대금 (나스닥 전체 → 핫 유니버스 추출용) ─────────────────
# 나스닥 3902종목 전체를 상세 일봉으로 받으면 무거우므로(≈96s), 가벼운 최근 시세로
# 당일 거래대금(=종가×거래량)·등락률만 산출해 상위 N을 1차 추출한다(2단계 필터).


def _parse_turnover_chunk(data, syms: list[str], out: dict[str, dict]) -> None:
    """yfinance 다운로드 결과 → out 에 {price, turnover, change_pct, date} 누적."""
    multi = len(syms) > 1
    for sym in syms:
        try:
            sub = data[to_yf_symbol(sym)] if multi else data
            sub = sub.dropna(subset=["Close"])
            if len(sub) < 1:
                continue
            last = sub.iloc[-1]
            price = float(last["Close"])
            vol = float(last.get("Volume", 0) or 0)
            prev = float(sub.iloc[-2]["Close"]) if len(sub) >= 2 else price
            chg = (price - prev) / prev * 100 if prev else 0.0
            try:
                last_date = sub.index[-1].strftime("%Y%m%d")
            except Exception:
                last_date = ""
            out[sym] = {
                "price": round(price, 2),
                "turnover": price * vol,
                "change_pct": round(chg, 2),
                "date": last_date,
            }
        except Exception:  # noqa: BLE001
            continue


def _fetch_turnover_sync(symbols: list[str], lookback: int = 7,
                         chunk_size: int = 350) -> dict[str, dict]:
    """동기 — 청크별 yfinance 다운로드로 {symbol: {price, turnover, change_pct, date}}.

    3902종목 한방 호출은 yfinance rate limit(429)에 걸리므로(전역 §7), 청크로 나눠
    배치 사이 랜덤 딜레이를 둔다(고정 딜레이 금지). rate limit 연속 2회 → 중단(모은 만큼 사용).
    """
    import random
    import time

    import yfinance as yf

    if not symbols:
        return {}
    out: dict[str, dict] = {}
    rate_hits = 0
    n_chunks = (len(symbols) + chunk_size - 1) // chunk_size
    for ci in range(n_chunks):
        part = symbols[ci * chunk_size:(ci + 1) * chunk_size]
        try:
            data = yf.download(
                [to_yf_symbol(s) for s in part], period=f"{lookback}d", group_by="ticker",
                auto_adjust=False, threads=True, progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "rate" in msg or "too many" in msg or "429" in msg:
                rate_hits += 1
                logger.warning("us_turnover_rate_limited chunk=%d/%d hits=%d",
                               ci + 1, n_chunks, rate_hits)
                if rate_hits >= 2:  # §7 — 연속 rate limit 즉시 중단, 자동 재시도 금지
                    logger.warning("us_turnover_halt collected=%d", len(out))
                    break
                time.sleep(random.uniform(10.0, 20.0))  # 지수 백오프성 대기
                continue
            logger.warning("us_turnover_chunk_failed chunk=%d error=%s", ci + 1, exc)
            continue
        _parse_turnover_chunk(data, part, out)
        if ci < n_chunks - 1:
            time.sleep(random.uniform(1.5, 3.5))  # §7 배치 사이 랜덤 휴식
    return out


async def fetch_us_daily_turnover(symbols: list[str], lookback: int = 7) -> dict[str, dict]:
    """미국 종목 당일 거래대금·등락률 일괄 → {symbol: {price, turnover, change_pct, date}}."""
    return await asyncio.to_thread(_fetch_turnover_sync, list(symbols), lookback)


# ─── 시총(USD) + USD/KRW 환율 — 리포트 원화(조/억) 표기·시총순 정렬용 ──────────


def _fetch_market_caps_sync(symbols: list[str]) -> dict[str, float]:
    """동기 — yfinance fast_info로 시총(USD) 조회 → {symbol(FDR키): marketCap}.

    종목당 1회라 분산 딜레이(rate limit 완화). 실패 종목은 생략(부분 결과 허용)."""
    import random
    import time

    import yfinance as yf

    out: dict[str, float] = {}
    for i, sym in enumerate(symbols):
        try:
            fi = yf.Ticker(to_yf_symbol(sym)).fast_info
            mc = None
            try:
                mc = fi.market_cap
            except Exception:  # noqa: BLE001
                try:
                    mc = fi["market_cap"]
                except Exception:  # noqa: BLE001
                    mc = None
            if mc:
                out[sym] = float(mc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("us_marcap_failed symbol=%s error=%s", sym, exc)
        if i < len(symbols) - 1:
            time.sleep(random.uniform(0.1, 0.3))
    return out


async def fetch_us_market_caps(symbols: list[str]) -> dict[str, float]:
    """미국 종목 시총(USD) 일괄 → {symbol: marketCap}. 실패 시 빈 dict."""
    if not symbols:
        return {}
    return await asyncio.to_thread(_fetch_market_caps_sync, list(symbols))


def _fetch_usd_krw_sync() -> float:
    """USD/KRW 환율(종가). 실패 시 0.0."""
    try:
        import FinanceDataReader as fdr
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        s = fdr.DataReader("USD/KRW", start)["Close"].dropna()
        return float(s.iloc[-1]) if len(s) else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("usd_krw_failed error=%s", exc)
        return 0.0


async def fetch_usd_krw() -> float:
    """USD/KRW 환율. 미국 시총·거래대금을 원화(조/억)로 환산할 때 사용. 실패 시 0.0."""
    return await asyncio.to_thread(_fetch_usd_krw_sync)


# ─── 프리장(pre-market) 시세 — 미국장 장전 리포트용 ──────────────────────────


def _fetch_premarket_sync(symbols: list[str]) -> dict[str, dict]:
    """동기 — yfinance .info로 프리장 가격/등락률. {symbol(FDR키): {price, change_pct}}.

    preMarketPrice/preMarketChangePercent(이미 %단위). 종목당 1회라 분산 딜레이.
    프리장 미체결 종목은 생략(부분 결과 허용)."""
    import random
    import time

    import yfinance as yf

    out: dict[str, dict] = {}
    for i, sym in enumerate(symbols):
        try:
            info = yf.Ticker(to_yf_symbol(sym)).info
            pp = info.get("preMarketPrice")
            if pp is not None:
                pc = info.get("preMarketChangePercent")
                out[sym] = {"price": float(pp), "change_pct": round(float(pc or 0), 2)}
        except Exception as exc:  # noqa: BLE001
            logger.debug("premarket_failed symbol=%s error=%s", sym, exc)
        if i < len(symbols) - 1:
            time.sleep(random.uniform(0.1, 0.3))
    return out


async def fetch_us_premarket(symbols: list[str]) -> dict[str, dict]:
    """미국 종목 프리장 시세 일괄 → {symbol: {price, change_pct}}. 실패 시 빈 dict."""
    if not symbols:
        return {}
    return await asyncio.to_thread(_fetch_premarket_sync, list(symbols))


def _fetch_postmarket_sync(symbols: list[str]) -> dict[str, dict]:
    """동기 — yfinance .info로 애프터장(시간외) 가격/등락률. {symbol(FDR키): {price, change_pct}}.

    postMarketPrice/postMarketChangePercent(이미 %단위). us_morning(07:00 KST)은 미국
    애프터장(장마감 후) 시간대라 값이 있을 때만 채운다. 미체결 종목은 생략(부분 결과 허용)."""
    import random
    import time

    import yfinance as yf

    out: dict[str, dict] = {}
    for i, sym in enumerate(symbols):
        try:
            info = yf.Ticker(to_yf_symbol(sym)).info
            pp = info.get("postMarketPrice")
            if pp is not None:
                pc = info.get("postMarketChangePercent")
                out[sym] = {"price": float(pp), "change_pct": round(float(pc or 0), 2)}
        except Exception as exc:  # noqa: BLE001
            logger.debug("postmarket_failed symbol=%s error=%s", sym, exc)
        if i < len(symbols) - 1:
            time.sleep(random.uniform(0.1, 0.3))
    return out


async def fetch_us_postmarket(symbols: list[str]) -> dict[str, dict]:
    """미국 종목 애프터장(시간외) 시세 일괄 → {symbol: {price, change_pct}}. 실패 시 빈 dict."""
    if not symbols:
        return {}
    return await asyncio.to_thread(_fetch_postmarket_sync, list(symbols))


# ─── 미국 시장 뉴스 — 장전/마감 AI 해설용(장후 뉴스·이슈) ──────────────────────


def _fetch_us_news_sync(top: int) -> list[dict]:
    """동기 — yfinance ^GSPC.news로 미국 시장 헤드라인. [{title, source}]."""
    import yfinance as yf

    out: list[dict] = []
    try:
        news = yf.Ticker("^GSPC").news or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_news_failed error=%s", exc)
        news = []
    for x in news[:top]:
        if not isinstance(x, dict):
            continue
        c = x.get("content") if isinstance(x.get("content"), dict) else x
        title = c.get("title") or x.get("title")
        prov = c.get("provider") if isinstance(c.get("provider"), dict) else None
        pub = prov.get("displayName") if prov else x.get("publisher")
        if title:
            out.append({"title": str(title).strip(), "source": str(pub or "")})
    return out


async def fetch_us_news(top: int = 10) -> list[dict]:
    """미국 시장 뉴스 헤드라인 → [{title, source}]. 실패 시 빈 리스트."""
    return await asyncio.to_thread(_fetch_us_news_sync, top)
