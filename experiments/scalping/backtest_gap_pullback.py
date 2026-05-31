"""B' 갭눌림 스캘핑 백테스트 (5분봉) — experiments, 메이저 코드 비침투.

입력: 바탕화면 스캘핑백테스트/*.xls (HTS 1분봉 export)
처리: 09:00~15:30 필터 → 1분→5분 리샘플 → VWAP·MA·거래량평균 계산
전략: B' 갭눌림 (갭상/장초반급등 → 눌림 → 지지 → 양봉 반등 진입)
청산: -1.5% 손절 / R:R 1:2 익절 / MA5·VWAP 트레일 / 장마감 강제청산
비용: 매도세 0.18% + 수수료 0.015%×2 + 슬리피지 1틱(진입·청산)
출력: 거래내역 CSV + 종목별/전체 승률·손익비 요약

파라미터는 CFG dict에서 수정 (코드 변경 불필요).
실행: python experiments/scalping/backtest_gap_pullback.py
"""
from __future__ import annotations

from datetime import time as dtime
from pathlib import Path

import pandas as pd

SRC = Path.home() / "Desktop" / "스캘핑백테스트"
OUT = Path(__file__).resolve().parent / "out"

CFG = {
    "session_start": dtime(9, 0),
    "session_end": dtime(15, 30),
    "no_new_entry_after": dtime(14, 50),
    # 진입 — 주도 흐름
    "gap_min": 0.02, "gap_max": 0.15,
    "surge_end": dtime(10, 0), "surge_min": 0.03,
    # 진입 — 눌림/지지/반등
    "pullback_min": 0.02, "pullback_max": 0.05,
    "support": "vwap",          # "vwap" | "ma20"
    "vol_ma": 20,
    # 청산
    "hard_stop": 0.015, "rr": 2.0,
    "trail_ma5": True, "trail_vwap": True,
    # 비용
    "fee_rate": 0.00015, "sell_tax": 0.0018, "slippage_ticks": 1,
}


# ── KRX 호가단위 (2023 개정) ────────────────────────────────────────────────
def tick_size(price: float) -> int:
    if price < 2000: return 1
    if price < 5000: return 5
    if price < 20000: return 10
    if price < 50000: return 50
    if price < 200000: return 100
    if price < 500000: return 500
    return 1000


# ── 로드 + 리샘플 ───────────────────────────────────────────────────────────
def resample_5min(c: pd.DataFrame) -> pd.DataFrame:
    """1분봉(DatetimeIndex, open/high/low/close/volume) → 09:00~15:30 5분봉.

    백테스트(xls)와 포워드(KIS)가 동일 엔진을 쓰도록 공용화.
    """
    c = c.sort_index()
    c = c.between_time(CFG["session_start"], CFG["session_end"])  # 장중만(시간외 제거)
    bucket = c.index.floor("5min")
    g = c.groupby(bucket).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    )
    g.index.name = "dt"
    return g


def load_5min(path: Path) -> pd.DataFrame:
    """HTS 1분봉 xls → 09:00~15:30 5분봉 OHLCV (오름차순)."""
    raw = pd.read_excel(path, engine="xlrd", header=0)
    # 컬럼명이 인코딩 깨짐 → 위치 인덱스로 접근 (0날짜 1시간 2시3고4저5종 19거래량)
    c = raw.iloc[:, [0, 1, 2, 3, 4, 5, 19]].copy()
    c.columns = ["date", "time", "open", "high", "low", "close", "volume"]
    c["dt"] = pd.to_datetime(
        c["date"].astype(str).str.strip() + " " + c["time"].astype(str).str.strip(),
        format="%Y/%m/%d %H:%M:%S", errors="coerce",
    )
    c = c.dropna(subset=["dt"]).sort_values("dt").set_index("dt")
    for col in ("open", "high", "low", "close", "volume"):
        c[col] = pd.to_numeric(c[col], errors="coerce")
    c = c.dropna(subset=["open", "high", "low", "close"])
    return resample_5min(c)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """MA5/10/20(연속) + 거래량평균(연속) + VWAP(일별 리셋)."""
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["vol_ma"] = df["volume"].rolling(CFG["vol_ma"]).mean()
    df["day"] = df.index.normalize()
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.groupby(df["day"]).cumsum() / df["volume"].groupby(df["day"]).cumsum()
    return df


# ── 전략 + 시뮬 ─────────────────────────────────────────────────────────────
def backtest(df: pd.DataFrame, name: str, prev_close_init: float | None = None) -> list[dict]:
    trades: list[dict] = []
    days = list(df.groupby("day"))
    prev_close = prev_close_init  # 전일 종가 (포워드 단일일 실행 시 일봉 API로 주입)

    for di, (day, d) in enumerate(days):
        d = d.reset_index()
        n = len(d)
        if n < 6:
            prev_close = d["close"].iloc[-1] if n else prev_close
            continue

        day_open = d["open"].iloc[0]
        gap_up = prev_close is not None and CFG["gap_min"] <= (day_open / prev_close - 1) <= CFG["gap_max"]

        run_high = -1e18
        run_low = 1e18
        surge_done = False
        in_pos = False
        entry = stop = target = 0.0
        entry_i = 0
        i = 0
        while i < n:
            bar = d.iloc[i]
            t = bar["dt"].time()
            run_high = max(run_high, bar["high"])
            run_low = min(run_low, bar["low"])

            if not in_pos:
                # 장초반 급등 감지 (저점 대비)
                if t <= CFG["surge_end"] and run_low > 0 and (run_high / run_low - 1) >= CFG["surge_min"]:
                    surge_done = True
                setup = gap_up or surge_done
                support = bar["vwap"] if CFG["support"] == "vwap" else bar["ma20"]
                prev_high = d["high"].iloc[i - 1] if i > 0 else bar["high"]

                pb = (run_high - bar["low"]) / run_high if run_high > 0 else 0
                cond = (
                    setup
                    and t < CFG["no_new_entry_after"]
                    and CFG["pullback_min"] <= pb <= CFG["pullback_max"]
                    and pd.notna(support) and bar["low"] >= support
                    and bar["close"] > bar["open"]                    # 양봉
                    and bar["close"] > prev_high                      # 직전봉 고점 상회
                    and pd.notna(bar["vol_ma"]) and bar["volume"] >= bar["vol_ma"]
                )
                if cond and i + 1 < n:
                    nxt = d.iloc[i + 1]
                    tk = tick_size(nxt["open"])
                    entry = nxt["open"] + CFG["slippage_ticks"] * tk
                    stop = entry * (1 - CFG["hard_stop"])
                    target = entry * (1 + CFG["hard_stop"] * CFG["rr"])
                    entry_i = i + 1
                    entry_dt = nxt["dt"]
                    setup_type = "gap" if gap_up else "surge"
                    in_pos = True
                    i += 1          # 진입봉으로 이동 → 다음 루프에서 진입봉부터 청산 평가
                    continue
                i += 1
                continue

            # ── 보유 중: 청산 판정 (진입봉부터) ──
            reason = None
            exit_px = bar["close"]
            if bar["low"] <= stop:
                reason, exit_px = "STOP", stop
            elif bar["high"] >= target:
                reason, exit_px = "TARGET", target
            elif CFG["trail_vwap"] and pd.notna(bar["vwap"]) and bar["close"] < bar["vwap"]:
                reason, exit_px = "TRAIL_VWAP", bar["close"]
            elif CFG["trail_ma5"] and pd.notna(bar["ma5"]) and bar["close"] < bar["ma5"]:
                reason, exit_px = "TRAIL_MA5", bar["close"]
            elif i == n - 1 or bar["dt"].time() >= CFG["session_end"]:
                reason, exit_px = "EOD", bar["close"]

            if reason:
                tk = tick_size(exit_px)
                fill = exit_px - CFG["slippage_ticks"] * tk           # 청산 슬리피지
                gross = fill / entry - 1
                cost = CFG["sell_tax"] + CFG["fee_rate"] * 2           # 매도세 + 양방 수수료
                net = gross - cost
                risk = CFG["hard_stop"]
                trades.append({
                    "stock": name, "setup": setup_type,
                    "entry_dt": entry_dt, "entry": round(entry, 1),
                    "exit_dt": bar["dt"], "exit": round(fill, 1),
                    "reason": reason, "gross_pct": round(gross * 100, 3),
                    "net_pct": round(net * 100, 3), "R": round(net / risk, 2),
                    "win": net > 0,
                })
                in_pos = False
            i += 1

        prev_close = d["close"].iloc[-1]
    return trades


# ── 요약 ────────────────────────────────────────────────────────────────────
def summarize(trades: list[dict], label: str) -> None:
    if not trades:
        print(f"  [{label}] 거래 없음")
        return
    t = pd.DataFrame(trades)
    n = len(t)
    wins = t[t["win"]]
    losses = t[~t["win"]]
    wr = len(wins) / n * 100
    avg_w = wins["net_pct"].mean() if len(wins) else 0
    avg_l = losses["net_pct"].mean() if len(losses) else 0
    pf = abs(wins["net_pct"].sum() / losses["net_pct"].sum()) if losses["net_pct"].sum() != 0 else float("inf")
    exp = t["net_pct"].mean()
    print(f"  [{label}] 거래 {n} · 승률 {wr:.0f}% ({len(wins)}승 {len(losses)}패) · "
          f"평균익 {avg_w:+.2f}% 평균손 {avg_l:+.2f}% · 손익비(PF) {pf:.2f} · "
          f"기대값/거래 {exp:+.3f}% · 누적 {t['net_pct'].sum():+.2f}%")


def main() -> None:
    files = sorted(SRC.glob("*.xls"))
    if not files:
        print(f"파일 없음: {SRC}")
        return
    OUT.mkdir(exist_ok=True)
    all_trades: list[dict] = []
    print(f"=== B' 갭눌림 백테스트 (5분봉) · {len(files)}종목 ===\n")
    print("진입: 갭상(+2~15%)/장초반급등(+3%) → 눌림(-2~5%) → 지지(VWAP) → 양봉반등+거래량")
    print("청산: -1.5%손절 / R:R 1:2 / MA5·VWAP 트레일 / EOD · 비용: 매도0.18%+수수료+슬리피지1틱\n")
    print("종목별:")
    for f in files:
        name = f.stem
        df = add_indicators(load_5min(f))
        tr = backtest(df, name)
        summarize(tr, name)
        all_trades.extend(tr)

    print("\n전체:")
    summarize(all_trades, "ALL")

    if all_trades:
        out_csv = OUT / "trades_gap_pullback.csv"
        pd.DataFrame(all_trades).to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"\n거래내역 저장: {out_csv}")
        print("\n--- 거래내역 (전체) ---")
        cols = ["stock", "setup", "entry_dt", "entry", "exit_dt", "exit", "reason", "net_pct", "R", "win"]
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(pd.DataFrame(all_trades)[cols].to_string(index=False))


if __name__ == "__main__":
    main()
