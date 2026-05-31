"""B' 갭눌림 EOD 포워드테스트 러너 — experiments, 메이저 코드 비침투.

매 영업일 장 마감 후 실행:
  1. KIS 순위(거래량+등락률) 상위 → 오늘의 유니버스
  2. 각 종목 당일 1분봉 수집(KIS) + raw 저장(데이터 축적)
  3. 전일 종가(일봉 API)로 갭 판정 보정
  4. 백테스트와 동일한 B' 엔진으로 그날 가상 거래 평가
  5. forward_trades.csv 에 누적(중복 방지) → 누적 승률·손익비 요약

검증과 백테스트가 100% 동일 로직을 쓰도록 backtest_gap_pullback 엔진을 import.
실행: python experiments/scalping/forward_eod.py [--max N] [--date YYYYMMDD] [--watch 005930,000660]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.config.settings import get_settings
from src.datasource.base import RankingKind
from src.datasource.kis.adapter import KisAdapter, KisHardStop, _TR

from backtest_gap_pullback import (  # noqa: E402  동일 엔진 재사용
    CFG, add_indicators, backtest, resample_5min, summarize,
)

MIN_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
FWD_CSV = OUT / "forward_trades.csv"


async def fetch_today_minute(adapter: KisAdapter, ticker: str, day: str) -> pd.DataFrame:
    """당일(day=YYYYMMDD) 1분봉 수집 → DataFrame(dt index, OHLCV)."""
    rows: dict[str, dict] = {}  # hour -> bar (당일만)
    cursor = "153000"
    prev_oldest = None
    for _ in range(40):
        params = {
            "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker, "FID_INPUT_HOUR_1": cursor,
            "FID_PW_DATA_INCU_YN": "N",  # 당일만
        }
        try:
            data = await adapter._request(MIN_ENDPOINT, _TR["ohlcv_minute"], params)
        except KisHardStop:
            raise  # §7: 429/인증급변 → 전체 중단 (삼키지 않음)
        except Exception as exc:  # noqa: BLE001
            print(f"    {ticker} 분봉 요청 실패: {type(exc).__name__}: {exc}")
            break
        out = data.get("output2", []) or []
        today_rows = [r for r in out if str(r.get("stck_bsop_date", "")).strip() == day]
        if not today_rows:
            break
        for r in today_rows:
            h = str(r.get("stck_cntg_hour", "")).strip()
            if h and h not in rows:
                rows[h] = r
        oldest = min(str(r.get("stck_cntg_hour", "")).strip() for r in today_rows)
        if oldest == prev_oldest or oldest <= "090000":
            break
        prev_oldest = oldest
        cursor = oldest

    if not rows:
        return pd.DataFrame()
    recs = []
    for h, r in rows.items():
        recs.append({
            "dt": pd.to_datetime(f"{day} {h}", format="%Y%m%d %H%M%S", errors="coerce"),
            "open": float(r.get("stck_oprc", 0) or 0),
            "high": float(r.get("stck_hgpr", 0) or 0),
            "low": float(r.get("stck_lwpr", 0) or 0),
            "close": float(r.get("stck_prpr", 0) or 0),
            "volume": float(r.get("cntg_vol", 0) or 0),
        })
    df = pd.DataFrame(recs).dropna(subset=["dt"]).set_index("dt").sort_index()
    return df[df[["open", "high", "low", "close"]].gt(0).all(axis=1)]


async def prev_close_of(adapter: KisAdapter, ticker: str, day: str) -> float | None:
    """전일(=day 이전 마지막 영업일) 종가 — 일봉 API."""
    try:
        candles = await adapter.get_ohlcv(ticker, days=5)
    except KisHardStop:
        raise  # §7: 전체 중단
    except Exception:  # noqa: BLE001
        return None
    prev = [c for c in candles if c.date < day]
    return prev[-1].close if prev else None


async def build_universe(adapter: KisAdapter, top: int, watch: list[str]) -> list[tuple[str, str]]:
    """KIS 순위(거래량+등락률) 상위 ∪ 관심종목 → [(ticker, name)] 중복제거."""
    uni: dict[str, str] = {}
    for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
        try:
            for s in await adapter.get_ranking(kind, top=top):
                uni.setdefault(s.ticker, s.name)
        except Exception as exc:  # noqa: BLE001
            print(f"  순위 조회 실패({kind.value}): {exc}")
    for t in watch:
        uni.setdefault(t, t)
    return list(uni.items())


def append_dedup(new_trades: list[dict]) -> pd.DataFrame:
    """forward_trades.csv 에 (stock, entry_dt) 기준 중복 없이 누적."""
    OUT.mkdir(exist_ok=True)
    new_df = pd.DataFrame(new_trades)
    if FWD_CSV.exists():
        old = pd.read_csv(FWD_CSV)
        merged = pd.concat([old, new_df], ignore_index=True)
        merged["entry_dt"] = merged["entry_dt"].astype(str)
        merged = merged.drop_duplicates(subset=["stock", "entry_dt"], keep="first")
    else:
        merged = new_df
    merged.to_csv(FWD_CSV, index=False, encoding="utf-8-sig")
    return merged


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=20, help="순위 상위 N (종류별)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--watch", default="", help="관심종목 콤마구분 6자리")
    args = ap.parse_args()
    day = args.date
    watch = [t for t in args.watch.split(",") if t.strip().isdigit() and len(t.strip()) == 6]

    s = get_settings()
    if not s.kis_app_key or not s.kis_app_secret:
        print("KIS_APP_KEY/SECRET 미설정 — .env 확인.")
        return
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print(f"=== B' 갭눌림 EOD 포워드 · {day} · env={s.kis_env} ===")
    universe = await build_universe(adapter, args.max, watch)
    print(f"유니버스 {len(universe)}종목\n")

    day_dir = DATA / day
    day_dir.mkdir(parents=True, exist_ok=True)
    day_trades: list[dict] = []
    fetched = 0

    try:
        for ticker, name in universe:
            df1 = await fetch_today_minute(adapter, ticker, day)
            if df1.empty or len(df1) < 30:
                continue
            fetched += 1
            df1.to_csv(day_dir / f"{ticker}.csv", encoding="utf-8-sig")  # raw 축적
            pc = await prev_close_of(adapter, ticker, day)
            df5 = add_indicators(resample_5min(df1))
            tr = backtest(df5, name or ticker, prev_close_init=pc)
            for t in tr:
                t["date"] = day
            if tr:
                print(f"  {name or ticker}({ticker}): {len(tr)}거래")
            day_trades.extend(tr)
    except KisHardStop as exc:
        print(f"\n[HARD STOP] {exc}\n  §7: 자동 중단. 수집분만 저장하고 종료. "
              f"브라우저에서 계정 상태 확인 후 재개하세요.")

    print(f"\n수집 성공 {fetched}종목 · 당일 신규거래 {len(day_trades)}건")
    if not day_trades:
        print("당일 신규 거래 없음 (휴장/시그널 미발생).")
        if not FWD_CSV.exists():
            return

    merged = append_dedup(day_trades) if day_trades else pd.read_csv(FWD_CSV)
    print(f"\n=== 누적 포워드 결과 ({FWD_CSV.name}) ===")
    summarize(merged.to_dict("records"), "FORWARD-CUMULATIVE")
    print(f"누적 거래내역: {FWD_CSV}")
    print(f"raw 분봉 축적: {day_dir}")


if __name__ == "__main__":
    asyncio.run(main())
