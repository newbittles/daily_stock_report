"""종가 눌림목 전략 백테스트 — 음봉 N연속 + 거래량급증 + 정배열.

전략: 신호 발생일(음봉 N연속 마지막 날) 종가 매수 → 다음날 종가 매도.
전체 발생 사례를 집계해 승률·평균수익·MDD를 측정 (생존 편향 방지).

실행: python scripts/backtest_pullback.py [종목코드...]
기본: 직전 조건검색 6종목 + LG전자
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_consecutive_bearish

# 기본 검증 대상 (조건검색 포착 6종목 + 추가 우량주)
DEFAULT_TICKERS = {
    "066570": "LG전자",
    "011070": "LG이노텍",
    "064400": "LG씨엔에스",
    "307950": "현대오토에버",
    "000150": "두산",
    "005380": "현대차",
    "012330": "현대모비스",
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
}

DECLINE_DAYS = 3       # 음봉 연속 일수
HOLD_DAYS = 1          # 보유 기간 (다음날 청산)


async def backtest_ticker(adapter, ticker: str, name: str) -> list[dict]:
    """단일 종목 백테스트. 신호 발생 → 다음날 종가 청산 기록 반환."""
    candles = await adapter.get_ohlcv(ticker, days=250)
    if len(candles) < 80:
        return []

    trades = []
    # i = 신호 발생일 (음봉 N연속 마지막). i+HOLD_DAYS 에 청산
    for i in range(70, len(candles) - HOLD_DAYS):
        window = candles[: i + 1]  # i일까지의 데이터로 판정
        result = is_consecutive_bearish(
            window, days=DECLINE_DAYS,
            require_alignment=True,
            require_volume_history=True,
        )
        if not result.matched:
            continue

        buy = candles[i].close          # 신호일 종가 매수
        sell = candles[i + HOLD_DAYS].close  # 다음날 종가 매도
        ret = (sell - buy) / buy * 100
        trades.append({
            "date": candles[i].date,
            "buy": buy,
            "sell": sell,
            "ret": ret,
            "next_date": candles[i + HOLD_DAYS].date,
        })
    return trades


def _summary(name: str, trades: list[dict]) -> dict:
    if not trades:
        return {"name": name, "n": 0}
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    return {
        "name": name,
        "n": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "avg_ret": sum(rets) / len(rets),
        "max_win": max(rets),
        "max_loss": min(rets),
        "total": sum(rets),
    }


async def main() -> None:
    args = sys.argv[1:]
    tickers = {t: t for t in args} if args else DEFAULT_TICKERS

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print(f"종가 눌림목 백테스트 — 음봉 {DECLINE_DAYS}연속 + 거래량급증 + 정배열")
    print(f"매수: 신호일 종가 / 매도: {HOLD_DAYS}일 후 종가")
    print("=" * 72)

    all_trades = []
    for ticker, name in tickers.items():
        try:
            trades = await backtest_ticker(adapter, ticker, name)
        except Exception as exc:
            print(f"  {name}({ticker}) 조회 실패: {exc}")
            continue
        all_trades.extend(trades)
        summ = _summary(name, trades)
        if summ["n"] == 0:
            print(f"\n■ {name} ({ticker}): 신호 없음")
            continue
        print(f"\n■ {name} ({ticker}) — 신호 {summ['n']}회")
        print(f"   승률 {summ['win_rate']:.0f}% | 평균 {summ['avg_ret']:+.2f}% | "
              f"최고 {summ['max_win']:+.1f}% | 최악 {summ['max_loss']:+.1f}%")
        for t in trades:
            mark = "✅" if t["ret"] > 0 else "❌"
            print(f"     {mark} {t['date']} 매수 {t['buy']:,.0f} → {t['next_date']} {t['sell']:,.0f}  {t['ret']:+.2f}%")

    print("\n" + "=" * 72)
    total = _summary("전체", all_trades)
    if total["n"] > 0:
        print(f"  📊 전체 {total['n']}회 | 승률 {total['win_rate']:.0f}% | "
              f"평균 {total['avg_ret']:+.2f}% | 누적 {total['total']:+.1f}%")
        print(f"     최고 {total['max_win']:+.1f}% | 최악 {total['max_loss']:+.1f}%")
        print(f"\n  ※ 슬리피지·수수료·세금 미반영. 모의 표본이며 과최적화 주의.")
    else:
        print("  신호 발생 없음 — 조건이 너무 엄격하거나 데이터 부족")


if __name__ == "__main__":
    asyncio.run(main())
