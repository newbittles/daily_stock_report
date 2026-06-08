"""장중 분봉 흐름 수집 — 종목별 당일 1분봉 → 15/60분 리샘플 → 궤적 요약 (#473/#474).

adapter(KIS) ↔ domain(indicators.intraday) 연결 기능모듈. 종목 배치 수집(§7 분산 딜레이).
prev_close(전일종가)는 호출측이 quote 등에서 산출해 전달 — 흐름 %는 전일종가 대비.
실패 종목은 건너뜀(부분 결과 허용, 리포트 best-effort).
"""
from __future__ import annotations

import asyncio
import logging

from src.indicators.intraday import Bar, analyze_flow, describe_flow, resample

logger = logging.getLogger(__name__)


def _to_bars(raw: list[dict]) -> list[Bar]:
    """adapter.get_today_minutes raw dict → Bar 리스트(순수 변환)."""
    return [Bar(hhmm=str(b.get("hhmm", "")), open=float(b.get("open", 0) or 0),
                high=float(b.get("high", 0) or 0), low=float(b.get("low", 0) or 0),
                close=float(b.get("close", 0) or 0), volume=float(b.get("volume", 0) or 0))
            for b in (raw or [])]


def flow_summary(raw_1m: list[dict], prev_close: float) -> dict | None:
    """1분봉 raw + 전일종가 → {desc, shape, cur_pct, low_pct, high_pct, trend60}. 순수.

    desc=15분봉 기준 한국어 추세 문구(사용자 예시 형식). trend60=60분봉 큰 추세(보조).
    데이터/전일종가 없으면 None.
    """
    bars1 = _to_bars(raw_1m)
    if not bars1 or prev_close <= 0:
        return None
    f15 = analyze_flow(resample(bars1, 15), prev_close)
    if f15 is None:
        return None
    f60 = analyze_flow(resample(bars1, 60), prev_close)
    return {
        "desc": describe_flow(f15),
        "shape": f15.shape,
        "cur_pct": f15.cur_pct,
        "low_pct": f15.low_pct,
        "high_pct": f15.high_pct,
        "trend60": f60.trend if f60 else "",
    }


async def fetch_intraday_flows(
    adapter, items: list[tuple[str, float]], day: str | None = None,
) -> dict[str, dict]:
    """items=[(ticker, prev_close)] → {ticker: flow_summary}. 개별 실패는 건너뜀.

    종목 간 §7 분산 딜레이는 adapter.get_today_minutes 내부 페이징에서 적용됨.
    prev_close≤0이거나 분봉 없으면 해당 종목 제외(키 없음)."""
    out: dict[str, dict] = {}
    seen: set[str] = set()
    for ticker, prev_close in items:
        tk = str(ticker or "").strip()
        if not tk or tk in seen or prev_close <= 0:
            continue
        seen.add(tk)
        try:
            raw = await adapter.get_today_minutes(tk, day)
        except Exception as exc:  # noqa: BLE001 (HardStop은 상위로)
            from src.datasource.kis.adapter import KisHardStop
            if isinstance(exc, KisHardStop):
                raise
            logger.warning("intraday_flow_failed ticker=%s error=%s", tk, exc)
            continue
        summ = flow_summary(raw, prev_close)
        if summ:
            out[tk] = summ
        await asyncio.sleep(0.0)  # 협조적 양보(딜레이는 adapter 내부)
    logger.info("intraday_flows collected=%d/%d", len(out), len(items))
    return out
