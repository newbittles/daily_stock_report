"""일별 A 전략 추천 시뮬레이션 (수렴 돌파 + MACD 약한필수).

사용법: python scripts/simulate_daily_A.py <시작> <종료> [--tg]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.base import RankingKind
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_convergence_breakout
from src.screener.pipeline import _is_etf

EXTRA = {
    "010170": "대한광통신", "307950": "현대오토에버", "001740": "SK네트웍스",
    "009150": "삼성전기", "043260": "성호전자", "011070": "LG이노텍",
    "066570": "LG전자", "000660": "SK하이닉스", "047040": "대우건설",
    "012330": "현대모비스", "319400": "현대무벡스", "018880": "한온시스템",
    "000720": "현대건설", "353200": "대덕전자",
}


def _macd_tags(metrics):
    tags = []
    if metrics.get("macd_zero_cross"): tags.append("0선돌파")
    if metrics.get("macd_gc"): tags.append("GC")
    if metrics.get("macd_above_zero"): tags.append("0선위")
    if metrics.get("macd_rising"): tags.append("상승")
    return tags


async def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "20260525"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260529"
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print("유니버스 수집 중 (핫종목 + 검증종목)...")
    universe = dict(EXTRA)
    try:
        for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
            for r in await adapter.get_ranking(kind, top=30):
                if r.ticker and not _is_etf(r.name):
                    universe[r.ticker] = r.name
    except Exception as exc:
        print(f"순위 일부 실패: {exc}")
    print(f"유니버스 {len(universe)}종목\n")

    candles_map = {}
    for ticker, name in universe.items():
        try:
            c = await adapter.get_ohlcv(ticker, days=150, end_date=end)
            if len(c) >= 135:
                candles_map[ticker] = c
        except Exception:
            continue

    sample = next(iter(candles_map.values()))
    target_dates = sorted({c.date for c in sample if start <= c.date <= end})

    tg = ["📈 *A 전략(수렴 돌파) 일별 추천* (5/25~29)", ""]
    print("=" * 78)
    for d in target_dates:
        recs = []
        for ticker, candles in candles_map.items():
            idx = next((i for i, c in enumerate(candles) if c.date == d), None)
            if idx is None or idx < 135:
                continue
            r = is_convergence_breakout(candles[: idx + 1], strict_align=False)
            if r.matched:
                recs.append((universe[ticker], ticker, candles[idx].close, r))

        dd = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        print(f"\n📅 {d} — 추천 {len(recs)}종목")
        tg.append(f"📅 *{dd}* — {len(recs)}종목")
        if not recs:
            print("   (A 신호 없음)")
            tg.append("  (신호 없음)")
            tg.append("")
            continue
        for name, tk, px, r in recs:
            tags = _macd_tags(r.metrics)
            tagtxt = f" MACD[{','.join(tags)}]" if tags else ""
            print(f"   🟢 {name}({tk}) {px:,.0f}원 — {r.reason}")
            tg.append(f"  🟢 *{name}* `{tk}` {px:,.0f}원{tagtxt}")
        tg.append("")
    tg.append("_※ 참고용. A=수렴 후 상승전환. 청산: 20선/MACD/일목._")

    if "--tg" in sys.argv:
        from telegram import Bot
        from src.notify.telegram.adapter import TelegramNotifier
        bot = Bot(token=s.telegram_bot_token)
        notifier = TelegramNotifier(bot=bot)
        ok = await notifier.send(str(s.allowed_chat_ids()[0]), "\n".join(tg))
        print(f"\n텔레그램 발송: {'✅' if ok else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
