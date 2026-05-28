"""5월 26일 장 기준 봇 동작 시뮬레이션 — 실제 텔레그램 전송.

실제 Kiwoom 연결 없이 샘플 데이터로 전체 흐름을 확인하고
실제 텔레그램 봇으로 메시지를 전송합니다.
실행: python scripts/simulate.py
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Bot

from src.config.settings import get_settings
from src.datasource.base import Candle, Quote, RankedStock, RankingKind
from src.notify.telegram.adapter import TelegramNotifier
from src.storage.db import init_db
from src.storage.repos import (
    AlertHistoryRepo,
    TradeHistoryRepo,
    TradeRecord,
    WatchlistRepo,
)
from src.bot.messages import format_trade_history, format_watchlist, format_whatif

# ── 5월 26일 샘플 시세 ───────────────────────────────────────────────────────

SAMPLE_QUOTES: dict[str, Quote] = {
    "005930": Quote(ticker="005930", name="삼성전자",   price=82500,  change_pct=+3.21, volume=18_432_000, timestamp="20260526"),
    "000660": Quote(ticker="000660", name="SK하이닉스", price=211000, change_pct=+5.78, volume=4_210_000,  timestamp="20260526"),
    "035420": Quote(ticker="035420", name="NAVER",      price=198500, change_pct=-1.34, volume=892_000,    timestamp="20260526"),
    "005380": Quote(ticker="005380", name="현대차",     price=282000, change_pct=+2.10, volume=1_543_000,  timestamp="20260526"),
    "035720": Quote(ticker="035720", name="카카오",     price=48700,  change_pct=-0.82, volume=3_120_000,  timestamp="20260526"),
    "373220": Quote(ticker="373220", name="LG에너지솔루션", price=395000, change_pct=+4.60, volume=670_000, timestamp="20260526"),
}

SAMPLE_RANKING = [
    RankedStock(rank=1, ticker="000660", name="SK하이닉스",     price=211000, change_pct=+5.78, volume=4_210_000),
    RankedStock(rank=2, ticker="373220", name="LG에너지솔루션", price=395000, change_pct=+4.60, volume=670_000),
    RankedStock(rank=3, ticker="005930", name="삼성전자",       price=82500,  change_pct=+3.21, volume=18_432_000),
    RankedStock(rank=4, ticker="005380", name="현대차",         price=282000, change_pct=+2.10, volume=1_543_000),
    RankedStock(rank=5, ticker="035420", name="NAVER",          price=198500, change_pct=-1.34, volume=892_000),
]

# 과거 매수/매도 내역 (시뮬레이션용)
SAMPLE_TRADES: list[TradeRecord] = [
    TradeRecord(ticker="000660", name="SK하이닉스",     trade_type="BUY",  price=185000, quantity=5,  trade_date="20260410"),
    TradeRecord(ticker="000660", name="SK하이닉스",     trade_type="SELL", price=199000, quantity=5,  trade_date="20260430"),
    TradeRecord(ticker="005930", name="삼성전자",       trade_type="BUY",  price=73000,  quantity=20, trade_date="20260501"),
    TradeRecord(ticker="035420", name="NAVER",          trade_type="BUY",  price=202000, quantity=3,  trade_date="20260512"),
    TradeRecord(ticker="035420", name="NAVER",          trade_type="SELL", price=195000, quantity=3,  trade_date="20260520"),
    TradeRecord(ticker="373220", name="LG에너지솔루션", trade_type="BUY",  price=380000, quantity=2,  trade_date="20260515"),
]


# ── Mock datasource ──────────────────────────────────────────────────────────

class MockDatasource:
    async def get_quote(self, ticker: str) -> Quote:
        if ticker not in SAMPLE_QUOTES:
            raise ValueError(f"No sample data for {ticker}")
        return SAMPLE_QUOTES[ticker]

    async def get_ranking(self, kind: RankingKind, top: int = 5):
        return SAMPLE_RANKING[:top]

    async def get_ohlcv(self, ticker: str, days: int = 60) -> list[Candle]:
        return []


class ConsoleAndTelegramNotifier:
    """콘솔 출력 + 실제 텔레그램 전송."""

    def __init__(self, notifier: TelegramNotifier, chat_id: str) -> None:
        self._notifier = notifier
        self._chat_id = chat_id

    async def send(self, chat_id: str, text: str) -> None:
        _box("텔레그램 발송", text)
        await self._notifier.send(chat_id, text)

    async def send_to_me(self, text: str) -> None:
        _box("텔레그램 발송", text)
        await self._notifier.send(self._chat_id, text)


def _box(title: str, content: str) -> None:
    width = 60
    print(f"\n{'-' * width}")
    print(f"  {title}")
    print(f"{'-' * width}")
    print(content)
    print(f"{'-' * width}")


def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ── 시뮬레이션 ────────────────────────────────────────────────────────────────

async def run() -> None:
    # 설정 로드 (실제 .env)
    settings = get_settings()
    chat_id = str(settings.allowed_chat_ids()[0])

    # 실제 텔레그램 봇 초기화
    bot = Bot(token=settings.telegram_bot_token)
    tg = TelegramNotifier(bot=bot)
    notifier = ConsoleAndTelegramNotifier(tg, chat_id)

    # 인메모리 DB 초기화
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    watchlist_repo = WatchlistRepo(conn)
    alert_repo = AlertHistoryRepo(conn)
    trade_repo = TradeHistoryRepo(conn)

    datasource = MockDatasource()

    # ── 1. 관심종목 설정 ─────────────────────────────────────────────────────
    _section("① 관심종목 등록 (/watch)")
    for ticker, quote in SAMPLE_QUOTES.items():
        watchlist_repo.add(ticker, quote.name)
        print(f"  ✅ 추가: {quote.name} ({ticker})")

    msg_watchlist = format_watchlist(watchlist_repo.get_all())
    await notifier.send_to_me(msg_watchlist)

    # ── 2. 매매 내역 적재 ────────────────────────────────────────────────────
    _section("② 과거 매매 내역 입력 (/sync_trades)")
    for t in SAMPLE_TRADES:
        trade_repo.insert(t)
        icon = "🟢 매수" if t.trade_type == "BUY" else "🔴 매도"
        print(f"  {icon}  {t.name} ({t.ticker})  {t.price:,}원 × {t.quantity}주  [{t.trade_date}]")

    await notifier.send_to_me(format_trade_history(trade_repo.get_recent(10)))

    # ── 3. /whatif 분석 ──────────────────────────────────────────────────────
    _section("③ 안 팔았다면? (/whatif)")
    sells = trade_repo.get_sells()
    results = []
    seen: set[str] = set()
    for record in sells:
        if record.ticker in seen:
            continue
        seen.add(record.ticker)
        try:
            quote = await datasource.get_quote(record.ticker)
            pct = (quote.price - record.price) / record.price * 100
            gain = (quote.price - record.price) * record.quantity
            results.append({"record": record, "current_price": quote.price,
                             "return_pct": pct, "gain": gain})
        except Exception:
            pass
    await notifier.send_to_me(format_whatif(results))

    # ── 4. 관심종목 모니터링 1사이클 (임계치 3%) ─────────────────────────────
    _section("④ 관심종목 모니터 1사이클 (임계치 3%)")
    print("  스케줄러가 5분마다 아래를 실행합니다:\n")

    triggered = []
    for item in watchlist_repo.get_all():
        quote = await datasource.get_quote(item.ticker)
        threshold = item.conditions.get("change_pct_threshold", 3.0)
        sign = "+" if quote.change_pct >= 0 else ""
        status = "🔔 알림 발송" if abs(quote.change_pct) >= threshold else "  대기 중"
        print(f"  {status}  {quote.name} ({quote.ticker})  {sign}{quote.change_pct:.2f}%  (임계치 {threshold}%)")
        if abs(quote.change_pct) >= threshold:
            triggered.append(quote)

    if triggered:
        print()
        for q in triggered:
            sign = "+" if q.change_pct >= 0 else ""
            msg = (
                f"🔔 *관심종목 알림*\n"
                f"{q.name} ({q.ticker})\n"
                f"현재가: {q.price:,.0f}원  ({sign}{q.change_pct:.2f}%)\n"
                f"거래량: {q.volume:,}\n"
                f"※ 참고용 시그널입니다. 투자 판단·책임은 본인에게 있습니다."
            )
            await notifier.send_to_me(msg)

    # ── 5. 등락률 순위 (핫종목 미리보기) ─────────────────────────────────────
    # ── 5. 등락률 순위 텔레그램 전송 ─────────────────────────────────────────
    _section("⑤ 등락률 TOP5 (module-3에서 시그널 판정 예정)")
    ranking = await datasource.get_ranking(RankingKind.CHANGE_PCT, top=5)
    print(f"  {'순위':<4} {'종목명':<16} {'현재가':>8}   {'등락률':>7}   {'거래량':>12}")
    print(f"  {'─'*4} {'─'*16} {'─'*8}   {'─'*7}   {'─'*12}")
    ranking_lines = ["📈 *등락률 TOP5* (5월 26일 기준)\n"]
    for s in ranking:
        sign = "+" if s.change_pct >= 0 else ""
        print(f"  {s.rank:<4} {s.name:<16} {s.price:>8,.0f}원  {sign}{s.change_pct:.2f}%  {s.volume:>12,}")
        ranking_lines.append(f"{s.rank}. {s.name} ({s.ticker})  {s.price:,.0f}원  {sign}{s.change_pct:.2f}%")
    ranking_lines.append("\n※ module-3 구현 후 매수 시그널 판정 추가 예정")
    await notifier.send_to_me("\n".join(ranking_lines))

    _section("시뮬레이션 완료")
    print("  실제 봇 실행: conda activate kiwoom → python main.py")
    print("  32비트 Python 필요 (키움 OCX 요건)\n")


if __name__ == "__main__":
    asyncio.run(run())
