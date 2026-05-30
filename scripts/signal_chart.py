"""종목+기간 매수신호를 차트에 마킹해 텔레그램으로 발송.

신호일(초록 ▲) + 선택적 사용자 매수일(노랑 ★)을 차트에 표시.

사용법:
  python scripts/signal_chart.py <종목코드> <시작일> <종료일> [매수일,매수일...]
  예: python scripts/signal_chart.py 006800 20260201 20260331 20260206,20260212
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Bot

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.logging_setup import setup_logging
from src.market_report.chart import _candles_to_df, _render_df
from src.notify.telegram.adapter import TelegramNotifier
from src.patterns.core import is_ma20_pullback


async def main() -> None:
    setup_logging()
    if len(sys.argv) < 4:
        print("사용법: python scripts/signal_chart.py <종목코드> <시작일> <종료일> [매수일,...]")
        return
    ticker, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    buy_dates = sys.argv[4].split(",") if len(sys.argv) > 4 else []

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    candles = await adapter.get_ohlcv(ticker, days=200, end_date=end)
    if len(candles) < 60:
        print(f"데이터 부족 ({len(candles)}봉)")
        return

    # 구간 내 신호일 탐색
    signal_dates = []
    for i in range(len(candles)):
        c = candles[i]
        if c.date < start or c.date > end:
            continue
        if len(candles[: i + 1]) < 60:
            continue
        if is_ma20_pullback(candles[: i + 1]).matched:
            signal_dates.append(c.date)

    print(f"종목 {ticker} · {start}~{end}")
    print(f"🟢 신호 {len(signal_dates)}일: {', '.join(signal_dates) or '없음'}")
    if buy_dates:
        print(f"⭐ 매수일: {', '.join(buy_dates)}")

    # 차트 생성 (신호 ▲ + 매수일 ★)
    df = _candles_to_df(candles)
    if df is None:
        print("차트 데이터 변환 실패")
        return
    chart = _render_df(df, ticker, ticker, date=end,
                       signal_dates=signal_dates, buy_dates=buy_dates,
                       out_suffix="-signals")
    if not chart:
        print("차트 생성 실패")
        return
    print(f"차트: {chart}")

    # 텔레그램 발송
    bot = Bot(token=s.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)
    chat_id = str(s.allowed_chat_ids()[0])
    cap = (f"*{ticker}* {start}~{end}\n"
           f"🟢 B 신호 {len(signal_dates)}일 (초록 ▲)\n"
           + (f"⭐ 매수일 (노랑 별)" if buy_dates else ""))
    await notifier.send_photo(chat_id, str(chart), cap)
    print("텔레그램 발송 완료")


if __name__ == "__main__":
    asyncio.run(main())
