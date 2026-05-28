from __future__ import annotations

import logging
import re
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.messages import format_error, format_trade_history, format_watchlist, format_whatif
from src.storage.repos import TradeRecord, TradeHistoryRepo, WatchlistRepo

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^\d{6}$")

_HELP_TEXT = """
*주식 인사이트 봇* 명령어 안내

/watch <종목코드> — 관심종목 추가
/unwatch <종목코드> — 관심종목 제거
/watchlist — 관심종목 목록 조회
/sync_trades [일수] — 키움 체결내역 동기화 (기본: 7일)
/history [건수] — 최근 매매 내역 조회 (기본: 20건)
/whatif — 매도 종목 "안 팔았다면?" 수익률
/analyze <종목코드> — 온디맨드 패턴 분석 (module-2)
/hot — 금일 핫 종목 + 시그널 (module-3)
/summary — 증시 요약 (module-3)
/briefing — 종가 직전 브리핑 (module-3)
/settings — 알림 임계치 설정

※ 모든 분석은 참고용입니다. 투자 책임은 본인에게 있습니다.
""".strip()


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    await update.message.reply_text(  # type: ignore[union-attr]
        "안녕하세요! 주식 인사이트 봇입니다.\n/help 로 명령어를 확인하세요.",
    )


async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")  # type: ignore[union-attr]


async def cmd_watch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    args = context.args or []
    if not args or not _TICKER_RE.match(args[0]):
        await update.message.reply_text(  # type: ignore[union-attr]
            "사용법: /watch <6자리 종목코드>\n예: /watch 005930"
        )
        return

    ticker = args[0]
    repo: WatchlistRepo = deps["watchlist_repo"]
    datasource = deps["datasource"]

    if repo.exists(ticker):
        await update.message.reply_text(f"{ticker}은 이미 관심종목입니다.")  # type: ignore[union-attr]
        return

    try:
        quote = await datasource.get_quote(ticker)
        repo.add(ticker, quote.name)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"✅ 관심종목 추가: {quote.name} ({ticker})"
        )
    except Exception as exc:
        logger.error("cmd_watch_error ticker=%s error=%s", ticker, exc)
        await update.message.reply_text(  # type: ignore[union-attr]
            format_error(f"종목 조회 실패 ({ticker})", attempts=1)
        )


async def cmd_unwatch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    args = context.args or []
    if not args or not _TICKER_RE.match(args[0]):
        await update.message.reply_text(  # type: ignore[union-attr]
            "사용법: /unwatch <6자리 종목코드>\n예: /unwatch 005930"
        )
        return

    ticker = args[0]
    repo: WatchlistRepo = deps["watchlist_repo"]

    if repo.remove(ticker):
        await update.message.reply_text(f"✅ 관심종목 제거: {ticker}")  # type: ignore[union-attr]
    else:
        await update.message.reply_text(f"{ticker}이 관심종목에 없습니다.")  # type: ignore[union-attr]


async def cmd_watchlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    repo: WatchlistRepo = deps["watchlist_repo"]
    items = repo.get_all()
    await update.message.reply_text(  # type: ignore[union-attr]
        format_watchlist(items), parse_mode="Markdown"
    )


async def cmd_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    await update.message.reply_text(  # type: ignore[union-attr]
        "⚙️ 설정 기능은 추후 업데이트 예정입니다."
    )


async def cmd_sync_trades(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    import datetime

    args = context.args or []
    try:
        days = max(1, min(int(args[0]), 90)) if args else 7
    except ValueError:
        days = 7

    repo: TradeHistoryRepo = deps["trade_repo"]
    datasource = deps["datasource"]

    await update.message.reply_text(f"🔄 최근 {days}일 체결내역 동기화 중...")  # type: ignore[union-attr]

    today = datetime.date.today()
    total = 0
    errors = 0
    for i in range(days):
        date_str = (today - datetime.timedelta(days=i)).strftime("%Y%m%d")
        try:
            raw_records = await datasource.get_trade_history(date_str)
            for item in raw_records:
                repo.upsert(TradeRecord(**item))
                total += 1
        except Exception as exc:
            logger.error("sync_trades_error date=%s error=%s", date_str, exc)
            errors += 1

    msg = f"✅ 동기화 완료: {total}건 처리"
    if errors:
        msg += f" (오류 {errors}일)"
    await update.message.reply_text(msg)  # type: ignore[union-attr]


async def cmd_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    args = context.args or []
    try:
        limit = max(1, min(int(args[0]), 50)) if args else 20
    except ValueError:
        limit = 20

    repo: TradeHistoryRepo = deps["trade_repo"]
    records = repo.get_recent(limit)
    await update.message.reply_text(  # type: ignore[union-attr]
        format_trade_history(records), parse_mode="Markdown"
    )


async def cmd_whatif(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    repo: TradeHistoryRepo = deps["trade_repo"]
    datasource = deps["datasource"]

    sells = repo.get_sells()
    if not sells:
        await update.message.reply_text(  # type: ignore[union-attr]
            "매도 내역이 없습니다. /sync_trades 로 먼저 동기화하세요."
        )
        return

    await update.message.reply_text("💭 현재가 조회 중...")  # type: ignore[union-attr]

    results = []
    seen: set[str] = set()
    for record in sells:
        if record.ticker in seen:
            continue
        seen.add(record.ticker)
        try:
            quote = await datasource.get_quote(record.ticker)
            current = quote.price
            return_pct = (current - record.price) / record.price * 100
            gain = (current - record.price) * record.quantity
            results.append({
                "record": record,
                "current_price": current,
                "return_pct": return_pct,
                "gain": gain,
            })
        except Exception as exc:
            logger.warning("whatif_quote_error ticker=%s error=%s", record.ticker, exc)

    await update.message.reply_text(  # type: ignore[union-attr]
        format_whatif(results), parse_mode="Markdown"
    )
