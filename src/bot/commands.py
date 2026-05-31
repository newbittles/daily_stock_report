from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.messages import (
    format_error,
    format_pick_caption,
    format_screening_by_strategy,
    format_screening_result,
    format_trade_history,
    format_watchlist,
    format_whatif,
)
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
/screen — 조건 검색 실행 (관심종목+핫종목 → 매수 의견)
/holdings — 보유종목 A/B/C 상태 (홀딩/손절/추가매수)
/analyze <종목코드> — 온디맨드 패턴 분석 (module-2)
/hot — 금일 핫 종목 + 시그널 (module-3)
/summary — 증시 요약 (module-3)
/briefing — 종가 직전 브리핑 (module-3)
/settings — 알림 임계치 설정

💡 조건 검색 규칙은 config/screener.yaml 파일에서 수정하세요.

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
    # 다중 종목 지원: "/watch 005930 000660 ..." 또는 콤마 구분 (복사버튼 일괄등록)
    raw = " ".join(context.args or []).replace(",", " ").split()
    tickers = [t for t in raw if _TICKER_RE.match(t)]
    if not tickers:
        await update.message.reply_text(  # type: ignore[union-attr]
            "사용법: /watch <6자리 종목코드> (여러 개 가능)\n예: /watch 005930 000660"
        )
        return

    repo: WatchlistRepo = deps["watchlist_repo"]
    datasource = deps["datasource"]
    added, skipped, failed = [], [], []
    for ticker in tickers:
        if repo.exists(ticker):
            skipped.append(ticker)
            continue
        try:
            quote = await datasource.get_quote(ticker)
            repo.add(ticker, quote.name)
            added.append(f"{quote.name}({ticker})")
        except Exception as exc:
            logger.error("cmd_watch_error ticker=%s error=%s", ticker, exc)
            failed.append(ticker)

    lines = []
    if added:
        lines.append(f"✅ 관심종목 추가 {len(added)}: " + ", ".join(added))
    if skipped:
        lines.append(f"⏭️ 이미 등록 {len(skipped)}: " + ", ".join(skipped))
    if failed:
        lines.append(f"⚠️ 실패 {len(failed)}: " + ", ".join(failed))
    await update.message.reply_text("\n".join(lines) or "처리할 종목 없음")  # type: ignore[union-attr]


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
    start = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    total = 0
    try:
        # KIS 어댑터: get_trade_history(start, end) — 기간 일괄 조회
        raw_records = await datasource.get_trade_history(start, end)
        for item in raw_records:
            repo.upsert(TradeRecord(**item))
            total += 1
        msg = f"✅ 동기화 완료: {total}건 처리"
    except Exception as exc:
        logger.error("sync_trades_error start=%s end=%s error=%s", start, end, exc)
        msg = format_error("체결내역 동기화 실패", attempts=1)

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


async def cmd_holdings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    """보유종목 A/B/C 종합 상태 — 홀딩/손절/추가매수 진단."""
    from src.alerts.holdings_report import diagnose_holdings, format_holdings_report

    datasource = deps["datasource"]
    await update.message.reply_text("🔍 보유종목 전략 상태 진단 중...")  # type: ignore[union-attr]
    try:
        rows = await diagnose_holdings(datasource)
    except Exception as exc:
        logger.error("cmd_holdings_error error=%s", exc)
        await update.message.reply_text(format_error("보유종목 조회 실패", attempts=1))  # type: ignore[union-attr]
        return
    await update.message.reply_text(  # type: ignore[union-attr]
        format_holdings_report(rows), parse_mode="Markdown"
    )


async def cmd_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deps: dict[str, Any],
) -> None:
    """조건 검색 실행 — 전략별 분류 + 종목별 차트 발송.

    1. config/screener.yaml 전략으로 관심종목+핫종목 스크리닝
    2. 전략별로 그룹핑한 요약 발송 (볼드체 + 근거 + 복붙 코드)
    3. 각 포착 종목의 차트 이미지 개별 발송
    """
    from src.market_report.chart import render_chart
    from src.screener.config import load_screener_config
    from src.screener.pipeline import run_screening

    datasource = deps["datasource"]
    notifier = deps["notifier"]
    watchlist_repo: WatchlistRepo = deps["watchlist_repo"]
    chat_id = str(update.effective_chat.id) if update.effective_chat else None

    cfg = load_screener_config()  # 매번 최신 YAML 로딩
    enabled = cfg.enabled_strategies()
    if not enabled:
        await update.message.reply_text(  # type: ignore[union-attr]
            "활성 전략이 없습니다. config/screener.yaml에서 enabled: true 로 켜세요."
        )
        return

    await update.message.reply_text(  # type: ignore[union-attr]
        f"🔍 조건 검색 중... ({len(enabled)}개 전략)\n관심종목 + 핫종목 분석, 잠시만요."
    )

    watchlist = [(item.ticker, item.name) for item in watchlist_repo.get_all()]
    try:
        picks = await run_screening(datasource, watchlist, cfg)
    except Exception as exc:
        logger.error("cmd_screen_error error=%s", exc)
        await update.message.reply_text(format_error("조건 검색 실패", attempts=1))  # type: ignore[union-attr]
        return

    # 1. 전략별 요약 발송
    await update.message.reply_text(  # type: ignore[union-attr]
        format_screening_by_strategy(picks, enabled), parse_mode="Markdown"
    )
    if not picks:
        return

    # 2. 종목별 차트 발송 (중복 종목은 1회만)
    seen: set[str] = set()
    for p in picks:
        if p.ticker in seen:
            continue
        seen.add(p.ticker)
        try:
            # pykrx 250봉으로 차트 (MA120·일목구름 위해 충분한 데이터 필요)
            chart_path = await asyncio.to_thread(render_chart, p.ticker, p.name)
            if chart_path and chat_id:
                caption = format_pick_caption(p, p.matches[0])
                await notifier.send_photo(chat_id, str(chart_path), caption)
        except Exception as exc:
            logger.warning("chart_send_failed ticker=%s error=%s", p.ticker, exc)


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
