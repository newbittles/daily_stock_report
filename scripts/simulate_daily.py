"""일별 B 전략 추천 시뮬레이션 — 지정 기간 동안 매일 어떤 종목이 추천됐을지.

유니버스: 현재 핫종목(거래량+등락률 순위) ∪ 검증 종목 (과거 순위 API 불가로 현재 기준 근사).
각 종목 일봉을 종료일까지 1회 조회 → 날짜별로 슬라이싱해 B 신호 체크.

사용법: python scripts/simulate_daily.py <시작일> <종료일>
  예: python scripts/simulate_daily.py 20260525 20260529
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.base import RankingKind
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_ma20_pullback
from src.screener.pipeline import _is_etf

# 그동안 검증한 추세주 (유니버스에 항상 포함)
EXTRA = {
    "010170": "대한광통신", "307950": "현대오토에버", "001740": "SK네트웍스",
    "009150": "삼성전기", "006800": "미래에셋증권", "011070": "LG이노텍",
    "066570": "LG전자", "000660": "SK하이닉스",
}


def _find_surge_day(window, lookback=10):
    seg = window[-lookback:]
    if not seg:
        return None, 0.0
    hi = max(seg, key=lambda c: c.high)
    lo = min(c.low for c in seg)
    return hi.date, ((hi.high - lo) / lo * 100 if lo > 0 else 0.0)


async def main() -> None:
    if len(sys.argv) < 3:
        print("사용법: python scripts/simulate_daily.py <시작일> <종료일>")
        return
    start, end = sys.argv[1], sys.argv[2]

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    # 유니버스: 현재 핫종목 + 검증종목
    print("유니버스 수집 중 (핫종목 + 검증종목)...")
    universe: dict[str, str] = dict(EXTRA)
    try:
        for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
            for r in await adapter.get_ranking(kind, top=30):
                if r.ticker and not _is_etf(r.name):
                    universe[r.ticker] = r.name
    except Exception as exc:
        print(f"순위 수집 일부 실패: {exc}")
    print(f"유니버스 {len(universe)}종목\n")

    # 각 종목 일봉 1회 조회 (종료일까지 충분히)
    candles_map: dict[str, list] = {}
    for ticker, name in universe.items():
        try:
            c = await adapter.get_ohlcv(ticker, days=100, end_date=end)
            if len(c) >= 60:
                candles_map[ticker] = c
        except Exception:
            continue

    # 대상 거래일 = 데이터에 존재하는 start~end 날짜
    sample = next(iter(candles_map.values()))
    target_dates = sorted({c.date for c in sample if start <= c.date <= end})

    print("=" * 78)
    tg_lines = ["🔍 *B 전략 일별 추천 시뮬* (5/25~5/29)", ""]
    for d in target_dates:
        recs = []
        for ticker, candles in candles_map.items():
            idx = next((i for i, c in enumerate(candles) if c.date == d), None)
            if idx is None or idx < 60:
                continue
            r = is_ma20_pullback(candles[: idx + 1])
            if r.matched:
                sd, sr = _find_surge_day(candles[: idx + 1])
                recs.append((universe[ticker], ticker, candles[idx].close, r, sd, sr))

        dd = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        print(f"\n📅 {d} — 추천 {len(recs)}종목")
        tg_lines.append(f"📅 *{dd}* — {len(recs)}종목")
        if not recs:
            print("   (B 신호 없음)")
            tg_lines.append("  (신호 없음)")
            tg_lines.append("")
            continue
        for name, tk, px, r, sd, sr in recs:
            print(f"   🟢 {name}({tk}) {px:,.0f}원")
            print(f"      {r.reason}")
            if sd:
                print(f"      급등일: {sd} (+{sr:.0f}%)")
            surge_txt = f"  (급등 {sd} +{sr:.0f}%)" if sd else ""
            tg_lines.append(f"  🟢 *{name}* `{tk}` {px:,.0f}원{surge_txt}")
        tg_lines.append("")

    tg_lines.append("_※ 참고용. 매수일=신호일, 손절=2일연속 20일선 이탈._")

    if "--tg" in sys.argv:
        from telegram import Bot
        from src.notify.telegram.adapter import TelegramNotifier
        bot = Bot(token=s.telegram_bot_token)
        notifier = TelegramNotifier(bot=bot)
        chat_id = str(s.allowed_chat_ids()[0])
        ok = await notifier.send(chat_id, "\n".join(tg_lines))
        print(f"\n텔레그램 발송: {'✅' if ok else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
