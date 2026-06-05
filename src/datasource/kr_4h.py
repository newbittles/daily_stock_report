"""한국 종목 4시간봉 볼린저밴드 과열 판정 — yfinance 1시간봉 → 4H 리샘플.

KIS 분봉은 이력이 1~2일뿐이라 4H BB(20)=약 10거래일치를 못 만든다 → yfinance 1h(최대 730일)
를 4H로 리샘플해 BB(20,2) 상단 돌파를 본다(2026-06-05 실측: KR 1h 60일→~88 4H봉 확보).

⚠️ yfinance KR 1h는 정규장(09:00~15:30)만 제공 → NXT 시간외(15:30~20:00)는 미포함.
   따라서 '정규장 4H봉' 기준 과열 판정(마감 후 NXT 음봉은 별도 연동 필요).

domain 아님(네트워크 어댑터). 입력=티커 리스트(6자리), 출력={ticker: 과열여부}. best-effort.
전역 §7: 종목당 분산 딜레이, 실패는 생략(리포트 깨지 않음).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def judge_4h_overheat(opens: list[float], highs: list[float], closes: list[float]) -> bool | None:
    """순수 판정: 마지막 4H봉이 과열인가. 데이터 부족 시 None.

    과열 = (종가 > BB상단; 돌파) OR (high ≥ BB상단 AND 종가 < 시가; 상단 음봉 거부).
    ⚠️ 음봉 조건만으론 '상단 돌파 양봉'(예: 삼성화재·신세계 2026-06-04, BB 위로 강하게 마감)을
    놓쳐 추천에서 못 거른다(실측 확인). → 돌파(close>upper)도 OR로 포함해야 의도 충족.
    외부 의존 없음(테스트 용이).
    """
    import statistics

    if len(closes) < 21 or len(opens) < 1 or len(highs) < 1:
        return None
    window = closes[-20:]
    ma = sum(window) / 20
    upper = ma + 2 * statistics.pstdev(window)
    breakout = closes[-1] > upper                              # 종가가 BB상단 위(돌파)
    rejection = highs[-1] >= upper and closes[-1] < opens[-1]  # 상단 터치 후 음봉(거부)
    return bool(breakout or rejection)


def _is_4h_bb_overheat_one(ticker: str) -> bool | None:
    """단일 종목 4H 과열 여부 = 마지막 4시간봉이 'BB(20,2) 상단에서 음봉'(거부/반전).

    판정: 마지막 4H봉 high ≥ BB상단(상단 터치/돌파) AND close < open(음봉) → 과열(사용자 2026-06-05).
    단순 상단 돌파보다 '상단에서 되밀린 음봉'이 과열 반전 신호로 정확. 데이터 부족/실패 시 None.
    """
    import yfinance as yf

    for suffix in (".KS", ".KQ"):  # KOSPI(.KS) 우선, 없으면 KOSDAQ(.KQ)
        try:
            df = yf.Ticker(f"{ticker}{suffix}").history(period="60d", interval="1h")
        except Exception as exc:  # noqa: BLE001
            logger.debug("kr_4h_fetch_failed ticker=%s%s error=%s", ticker, suffix, exc)
            continue
        if df is None or df.empty or not {"Open", "High", "Close"} <= set(df.columns):
            continue
        # 1h → 4h 리샘플(정규장): 시가=첫값·고가=최댓값·종가=마지막값.
        try:
            o4 = df["Open"].resample("4h").first()
            h4 = df["High"].resample("4h").max()
            c4 = df["Close"].resample("4h").last()
        except Exception as exc:  # noqa: BLE001
            logger.debug("kr_4h_resample_failed ticker=%s error=%s", ticker, exc)
            continue
        import pandas as pd
        bars = pd.concat([o4, h4, c4], axis=1, keys=["o", "h", "c"]).dropna()
        if len(bars) < 21:  # BB(20) + 현재봉
            continue
        res = judge_4h_overheat(
            [float(x) for x in bars["o"].values],
            [float(x) for x in bars["h"].values],
            [float(x) for x in bars["c"].values],
        )
        if res is not None:
            return res
    return None


def _fetch_sync(tickers: list[str]) -> dict[str, bool]:
    import random
    import time

    out: dict[str, bool] = {}
    for i, tk in enumerate(tickers):
        res = _is_4h_bb_overheat_one(tk)
        if res is not None:
            out[tk] = res
        if i < len(tickers) - 1:
            time.sleep(random.uniform(0.2, 0.5))  # §7 분산 딜레이
    logger.info("kr_4h_overheat_done checked=%d overheat=%d", len(out), sum(out.values()))
    return out


def judge_4h_rsi_oversold(closes: list[float], period: int = 14, rsi_max: float = 30.0) -> bool | None:
    """순수 판정: 마지막 4H봉 RSI(period) ≤ rsi_max(과매도). 데이터 부족 시 None."""
    from src.indicators.core import rsi
    if len(closes) < period + 2:
        return None
    rv = rsi(closes, period)[-1]
    if rv is None:
        return None
    return rv <= rsi_max


def _fetch_4h_closes(yf_symbol: str) -> list[float] | None:
    """yfinance 1h → 4h 리샘플 종가 리스트. 실패/빈 경우 None."""
    import yfinance as yf

    try:
        df = yf.Ticker(yf_symbol).history(period="60d", interval="1h")
    except Exception as exc:  # noqa: BLE001
        logger.debug("4h_closes_fetch_failed sym=%s error=%s", yf_symbol, exc)
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    try:
        c4 = df["Close"].resample("4h").last().dropna()
    except Exception:  # noqa: BLE001
        return None
    return [float(x) for x in c4.values] if len(c4) else None


def _yf_symbols_for(ticker: str, market: str) -> list[str]:
    if market == "US":
        from src.datasource.us.symbols import to_yf_symbol
        return [to_yf_symbol(ticker)]
    return [f"{ticker}.KS", f"{ticker}.KQ"]  # KR: KOSPI 우선, KOSDAQ 폴백


def _fetch_4h_rsi_oversold_sync(tickers: list[str], market: str) -> set[str]:
    import random
    import time

    out: set[str] = set()
    for i, tk in enumerate(tickers):
        for sym in _yf_symbols_for(tk, market):
            closes = _fetch_4h_closes(sym)
            if closes is None:
                continue
            res = judge_4h_rsi_oversold(closes)
            if res is not None:
                if res:
                    out.add(tk)
                break  # 데이터 확보됨 → 다음 종목
        if i < len(tickers) - 1:
            time.sleep(random.uniform(0.2, 0.5))  # §7
    logger.info("4h_rsi_oversold market=%s checked=%d oversold=%d", market, len(tickers), len(out))
    return out


async def fetch_4h_rsi_oversold(tickers: list[str], market: str = "KR") -> set[str]:
    """4시간봉 RSI(14) ≤ 30(과매도) 종목 집합 → E전략 4H 게이트(사용자 2026-06-05). market: KR|US."""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return set()
    return await asyncio.to_thread(_fetch_4h_rsi_oversold_sync, tickers, market)


async def fetch_4h_overheat(tickers: list[str]) -> dict[str, bool]:
    """KR 종목들의 4시간봉 BB 상단 돌파(과열) 여부 → {ticker: bool}. 실패 종목은 키 없음."""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}
    return await asyncio.to_thread(_fetch_sync, tickers)
