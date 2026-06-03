"""60분봉 20MA 손절 백테스트 — 주도주 매매 1차 손절률 (yfinance 한국 60분봉).

손절 체계(사용자 정의 2026-06-02):
  1차: 60분봉 종가가 20MA를 2캔들 연속 이탈 → 30% 손절
  2차: 일봉 20MA 이탈 → 50%
  3차: 일봉 60MA 이탈 → 전량

이 스크립트는 1차(60분 20MA 2연속) 손절 발동률을 측정한다.
데이터: yfinance 60분봉(KIS 분봉은 당일~1.x일치라 부족 → yfinance로 우회).
매수 가정: 한 달 각 영업일 종가(종가베팅) 매수 후 60분봉 손절 추적.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 주도주 (시총 상위 대형주) — .KS 코스피
LEADERS = {
    "005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차", "000270": "기아",
    "005490": "POSCO홀딩스", "035420": "NAVER", "035720": "카카오", "066570": "LG전자",
    "207940": "삼성바이오로직스", "373220": "LG에너지솔루션", "006400": "삼성SDI",
    "012450": "한화에어로스페이스", "009150": "삼성전기", "011070": "LG이노텍",
    "028260": "삼성물산", "105560": "KB금융", "055550": "신한지주", "086790": "하나금융지주",
}
MA = 20
CONSEC = 2  # 2캔들 연속 이탈


def backtest_ticker(code: str, name: str) -> list[dict]:
    """각 영업일 종가 매수 → 60분 20MA 2연속 마감 이탈 시 손절. 거래 리스트."""
    try:
        h = yf.download(f"{code}.KS", interval="60m", period="2mo", progress=False, auto_adjust=True)
    except Exception:
        return []
    if h is None or len(h) < MA + CONSEC + 2:
        return []
    close = h["Close"].squeeze()
    ma20 = close.rolling(MA).mean()
    below = (close < ma20)  # 각 캔들 20MA 아래 마감 여부

    # 일별 그룹 → 각 영업일의 마지막 캔들(종가) = 매수 시점
    h2 = pd.DataFrame({"close": close, "ma20": ma20, "below": below})
    h2["day"] = h2.index.date
    days = sorted(h2["day"].unique())

    trades = []
    # 매수 가능일: 20MA 채워진 이후 ~ 손절 추적 여지 남기기 (마지막 2일 제외)
    for di, day in enumerate(days):
        day_rows = h2[h2["day"] == day]
        if day_rows["ma20"].isna().all():
            continue
        buy = float(day_rows["close"].iloc[-1])  # 그날 종가(마지막 60분봉)
        buy_idx = h2.index.get_loc(day_rows.index[-1])
        if pd.isna(buy) or buy <= 0:
            continue
        # 매수 다음 캔들부터 2연속 마감 이탈 탐색
        exit_price, exit_when, status = None, None, "보유중"
        seq = h2.iloc[buy_idx + 1:]
        run = 0
        for ts, row in seq.iterrows():
            if bool(row["below"]):
                run += 1
                if run >= CONSEC:
                    exit_price = float(row["close"])
                    exit_when = ts
                    status = "손절"
                    break
            else:
                run = 0
        if status == "손절":
            ret = (exit_price - buy) / buy * 100
            hold_h = (exit_when - day_rows.index[-1]).total_seconds() / 3600
            trades.append({"name": name, "buy": buy, "ret": ret, "status": "손절",
                           "hold_h": hold_h})
        else:
            last = float(close.iloc[-1])
            trades.append({"name": name, "buy": buy, "ret": (last - buy) / buy * 100,
                           "status": "보유중", "hold_h": None})
    return trades


def main():
    print("주도주 60분봉 수집(yfinance) + 20MA 2연속 이탈 손절 백테스트...\n")
    all_trades = []
    for code, name in LEADERS.items():
        t = backtest_ticker(code, name)
        all_trades.extend(t)
    if not all_trades:
        print("데이터 없음")
        return

    total = len(all_trades)
    stopped = [t for t in all_trades if t["status"] == "손절"]
    held = [t for t in all_trades if t["status"] == "보유중"]
    print(f"총 매수(주도주×영업일): {total}건")
    print(f"1차 손절 발동(60분 20MA 2연속 이탈): {len(stopped)}건 ({len(stopped)/total*100:.1f}%)")
    print(f"미발동(보유중): {len(held)}건 ({len(held)/total*100:.1f}%)")
    if stopped:
        avg_loss = sum(t["ret"] for t in stopped) / len(stopped)
        avg_hold = sum(t["hold_h"] for t in stopped) / len(stopped)
        print(f"손절 시 평균 손익: {avg_loss:+.2f}% · 평균 보유 {avg_hold:.1f}시간({avg_hold/6.5:.1f}거래일)")
    if held:
        avg_h = sum(t["ret"] for t in held) / len(held)
        print(f"미발동 보유분 현재 평균: {avg_h:+.2f}%")
    # 전체 기대값 (1차 30% 손절 + 70% 유지 단순 가정)
    print(f"\n참고: 손절 발동률 {len(stopped)/total*100:.0f}% → 1차는 '주도주가 60분 20MA를 "
          f"이틀 연속 종가로 깨는' 빈도. 낮을수록 추세 유지가 잘 됨.")


if __name__ == "__main__":
    main()
