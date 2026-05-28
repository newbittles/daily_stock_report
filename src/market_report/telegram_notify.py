"""텔레그램 요약 발송 — 리포트 URL 동봉.

기존 src/notify/telegram/adapter.py 재사용.
"""
from __future__ import annotations

import logging

from telegram import Bot

from src.config.settings import get_settings
from src.market_report.models import MarketSnapshot
from src.market_report.publisher import report_url
from src.notify.telegram.adapter import TelegramNotifier

logger = logging.getLogger(__name__)


def _format_pre_summary(snap: MarketSnapshot) -> str:
    """마감 전 텔레그램 요약 메시지 (Markdown)."""
    url = report_url(snap)
    date = snap.generated_at.strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append(f"🟡 *마감 전 리포트* — {date}")
    lines.append("")

    # 지수
    if snap.kospi or snap.kosdaq:
        idx_parts = []
        for idx in (snap.kospi, snap.kosdaq):
            if idx:
                sign = "+" if idx.change_pct >= 0 else ""
                idx_parts.append(f"{idx.market} {idx.value:,.1f}({sign}{idx.change_pct:.2f}%)")
        if idx_parts:
            lines.append("📊 " + "  ·  ".join(idx_parts))
            lines.append("")

    # AI 한줄 요약
    if snap.summary:
        lines.append(snap.summary)
        lines.append("")

    # 종가베팅 후보 짧게 (5개)
    if snap.candidate_picks:
        lines.append("🎯 *종가베팅 후보*")
        for i, p in enumerate(snap.candidate_picks[:5], 1):
            name = p.get("name", "?")
            ticker = p.get("ticker", "")
            theme = p.get("theme", "")
            theme_str = f" [{theme}]" if theme else ""
            lines.append(f"{i}. {name} ({ticker}){theme_str}")
        lines.append("")

    lines.append(f"📄 [전체 리포트 보기]({url})")
    lines.append("")
    lines.append("_※ 참고용 정보. 투자 판단·책임은 본인._")
    return "\n".join(lines)


def _format_post_summary(snap: MarketSnapshot) -> str:
    """마감 후 텔레그램 요약 메시지."""
    url = report_url(snap)
    date = snap.generated_at.strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append(f"🔵 *마감 후 리포트* — {date}")
    lines.append("")

    if snap.kospi or snap.kosdaq:
        idx_parts = []
        for idx in (snap.kospi, snap.kosdaq):
            if idx:
                sign = "+" if idx.change_pct >= 0 else ""
                idx_parts.append(f"{idx.market} {idx.value:,.1f}({sign}{idx.change_pct:.2f}%)")
        if idx_parts:
            lines.append("📊 " + "  ·  ".join(idx_parts))
            lines.append("")

    if snap.summary:
        lines.append(snap.summary)
        lines.append("")

    # 강세 테마 Top 3
    if snap.top_themes:
        lines.append("🔥 *강세 테마*")
        for t in snap.top_themes[:3]:
            sign = "+" if t.change_pct >= 0 else ""
            lines.append(f"  · {t.name} {sign}{t.change_pct:.2f}%")
        lines.append("")

    # 내일 관전 포인트
    if snap.candidate_picks:
        watchpoints = [p.get("watchpoint", "") for p in snap.candidate_picks if p.get("watchpoint")]
        if watchpoints:
            lines.append("🔭 *내일 관전 포인트*")
            for w in watchpoints[:3]:
                lines.append(f"  · {w}")
            lines.append("")

    lines.append(f"📄 [전체 리포트 보기]({url})")
    lines.append("")
    lines.append("_※ 참고용 정보. 투자 판단·책임은 본인._")
    return "\n".join(lines)


async def send_report(snap: MarketSnapshot) -> bool:
    """리포트 요약을 텔레그램으로 발송. 성공 여부 반환."""
    settings = get_settings()
    chat_ids = settings.allowed_chat_ids()
    if not chat_ids:
        logger.warning("telegram_no_chat_id — allowed_chat_ids 비어있음")
        return False

    text = (
        _format_pre_summary(snap) if snap.mode == "pre_close"
        else _format_post_summary(snap)
    )

    bot = Bot(token=settings.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)

    # 화이트리스트 첫 chat_id로 발송 (필요시 전체 broadcast로 확장 가능)
    chat_id = str(chat_ids[0])
    try:
        await notifier.send(chat_id, text)
        logger.info("telegram_sent mode=%s chat_id=%s", snap.mode, chat_id)
        return True
    except Exception as exc:
        logger.error("telegram_send_failed mode=%s error=%s", snap.mode, exc)
        return False
