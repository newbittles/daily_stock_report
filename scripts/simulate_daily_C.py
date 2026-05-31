"""C 전략 일별 추천 시뮬 (대세 정배열 추세추종).

사용법: python scripts/simulate_daily_C.py <시작> <종료> [--tg]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.base import RankingKind
from src.datasource.kis.adapter import KisAdapter
from src.patterns.core import is_trend_follow
from src.screener.pipeline import _is_etf

EXTRA = {
    "000660": "SK하이닉스", "005930": "삼성전자", "009150": "삼성전기",
    "011070": "LG이노텍", "066570": "LG전자", "005380": "현대차",
    "347850": "디엔디파마텍", "277810": "레인보우로보틱스", "010170": "대한광통신",
    "043260": "성호전자", "298040": "효성중공업",
}


async def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "20260518"
    end = sys.argv[2] if len(sys.argv) > 2 else "20260529"
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    print("유니버스 수집 (핫종목 + 검증종목)...")
    universe = dict(EXTRA)
    try:
        for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
            for r in await a.get_ranking(kind, top=30):
                if r.ticker and not _is_etf(r.name):
                    universe[r.ticker] = r.name
    except Exception as exc:
        print(f"순위 일부 실패: {exc}")
    print(f"유니버스 {len(universe)}종목\n")

    cm = {}
    for tk in universe:
        try:
            c = await a.get_ohlcv(tk, days=200, end_date=end)
            if len(c) >= 135:
                cm[tk] = c
        except Exception:
            pass

    sample = next(iter(cm.values()))
    dates = sorted({c.date for c in sample if start <= c.date <= end})

    tg = ["📊 *C 전략(대세 추세추종) 일별 추천*", ""]
    print("=" * 78)
    for d in dates:
        recs = []
        for tk, c in cm.items():
            idx = next((i for i, x in enumerate(c) if x.date == d), None)
            if idx is None or idx < 135:
                continue
            r = is_trend_follow(c[: idx + 1])
            if r.matched:
                recs.append((universe[tk], tk, c[idx].close, r))
        dd = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        print(f"\n📅 {d} — 추천 {len(recs)}종목")
        tg.append(f"📅 *{dd}* — {len(recs)}종목")
        if not recs:
            print("   (신호 없음)")
            tg.append("  (신호 없음)")
            tg.append("")
            continue
        for name, tk, px, r in recs:
            warn = " ⚠️끝물주의" if r.metrics.get("endstage") else ""
            print(f"   🟢 {name}({tk}) {px:,.0f}원{warn} — {r.reason}")
            tg.append(f"  🟢 *{name}* `{tk}` {px:,.0f}원{warn}")
        tg.append("")
    tg.append("_※ 손절=60일선 2일이탈(알림). ⚠️끝물주의=이미 과열, 매도는 본인 판단._")

    if "--tg" in sys.argv:
        from telegram import Bot
        from src.notify.telegram.adapter import TelegramNotifier
        bot = Bot(token=s.telegram_bot_token)
        ok = await TelegramNotifier(bot=bot).send(str(s.allowed_chat_ids()[0]), "\n".join(tg))
        print(f"\n텔레그램: {'✅' if ok else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
