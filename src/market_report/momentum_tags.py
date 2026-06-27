"""모멘텀 천장/생존 경고 태그 부착 — RS 약세 다이버전스 + 60선 복귀실패(lower high).

사용자 2026-06-22 연구 반영. 픽/보유종목 dict에 아래 키를 best-effort로 부착한다(실패 무시):
  - p["rs_warn"]   : RS 약세 다이버전스(가격 신고가권인데 RS 꺾임 = 천장 주의)
  - p["rs_confirm"]: RS 강세 유지(시장 압도 — 눌림 추매 신뢰 가점)
  - p["ma60_fail"] : 60선 이탈 후 직전고점 복귀 실패(lower high — 추세 훼손)

⚠️ 모두 가중치 0 '경고/필터' 태그(추천 제외·매도 강제 아님). OOS 백테스트상 엣지 약함
   → 보조 정보로만. domain 순수성 위해 지수 조회·정렬은 여기(어댑터 계층)서 한다.

KR은 KOSPI(KS11)를 시장 벤치마크로 사용(코스닥 종목도 광의 시장 기준 — 사용자 연구가 KOSPI 기준).
US는 benchmark="US500" 등으로 호출.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def _fetch_closes_by_date(symbol: str, tail: int = 200) -> dict[str, float]:
    """FDR 일봉 종가를 {YYYY-MM-DD: close} dict로 (best-effort). 실패 시 빈 dict."""
    import FinanceDataReader as fdr
    try:
        df = await asyncio.to_thread(lambda: fdr.DataReader(symbol).tail(tail))
        if df is None or len(df) == 0:
            return {}
        return {str(i.date()): float(r.Close) for i, r in df.iterrows()}
    except Exception:  # noqa: BLE001
        return {}


async def tag_momentum_warn(
    picks: list[dict] | None, key: str = "ticker", benchmark: str = "KS11",
) -> None:
    """픽/보유 dict 리스트에 RS 다이버전스·60선 복귀실패 경고 태그 부착(in-place, best-effort).

    benchmark 지수를 1회 조회해 종목과 날짜 정렬 후 rs_divergence·ma60_recovery_failure 판정.
    """
    if not picks:
        return
    from src.datasource.base import Candle
    from src.patterns.core import ma60_recovery_failure, rs_divergence

    idx_by_date = await _fetch_closes_by_date(benchmark)
    sem = asyncio.Semaphore(6)

    async def _one(p: dict) -> None:
        tk = p.get(key) or p.get("symbol") or p.get("ticker")
        if not tk:
            return
        async with sem:
            stk_by_date = await _fetch_closes_by_date(tk)
            if len(stk_by_date) < 70:
                return
            # 60선 복귀실패는 종목만으로 판정 — 일봉 캔들 구성(고가는 종가 근사, 보수적)
            dates = sorted(stk_by_date)
            closes = [stk_by_date[d] for d in dates]
            try:
                import FinanceDataReader as fdr
                df = await asyncio.to_thread(lambda: fdr.DataReader(tk).tail(200))
                cs = [Candle(date=str(i.date()), open=float(r.Open), high=float(r.High),
                             low=float(r.Low), close=float(r.Close), volume=int(r.Volume))
                      for i, r in df.iterrows()]
            except Exception:  # noqa: BLE001
                cs = [Candle(date=d, open=c, high=c, low=c, close=c, volume=0)
                      for d, c in zip(dates, closes)]
            try:
                if ma60_recovery_failure(cs).matched:
                    p["ma60_fail"] = True
            except Exception:  # noqa: BLE001
                pass
            # RS 다이버전스 — 지수와 날짜 교집합 정렬 후 판정
            if idx_by_date:
                common = [d for d in dates if d in idx_by_date]
                if len(common) >= 60:
                    al_closes = [stk_by_date[d] for d in common]
                    al_index = [idx_by_date[d] for d in common]
                    al_candles = [Candle(date=d, open=c, high=c, low=c, close=c, volume=0)
                                  for d, c in zip(common, al_closes)]
                    try:
                        r = rs_divergence(al_candles, al_index)
                        if r.matched:
                            p["rs_warn"] = True
                        elif r.metrics.get("confirming"):
                            p["rs_confirm"] = True
                    except Exception:  # noqa: BLE001
                        pass

    await asyncio.gather(*[_one(p) for p in picks], return_exceptions=True)
