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


_STATE_EMOJI = {"BREAKDOWN": "🔴", "STOP60": "🔴", "STOP20": "⚠️", "ADD": "🟢",
                "HOLD": "✅", "NEUTRAL": "➖", "UNKNOWN": "❔"}


def _naver_link(name: str, ticker: str) -> str:
    """텔레그램 Markdown 종목 링크 → 네이버 금융 개별 페이지."""
    return f"[{name}](https://finance.naver.com/item/main.naver?code={ticker})"


def _format_strategy_holdings(snap: MarketSnapshot) -> list[str]:
    """Top3 + 보유종목 상태 요약 (종목명=네이버링크, 상승률·손절 병기).

    A/B/C/D 전략 스크린은 텔레그램에서 제외 — 웹 '전체 리포트 보기'에서만 표시(메시지 간결화).
    """
    lines: list[str] = []
    # ★ 오늘의 추천 Top3
    if getattr(snap, "top3", None):
        lines.append("🏆 *오늘의 추천 Top 3*")
        for i, t in enumerate(snap.top3, 1):
            sign = "+" if t.get("change_pct", 0) >= 0 else ""
            mc = f" · 시총 {t['marcap_str']}" if t.get("marcap_str") else ""
            lines.append(f"{i}. {_naver_link(t['name'], t['ticker'])} "
                         f"{t['price']:,.0f}원 ({sign}{t.get('change_pct', 0):.1f}%){mc}")
            lines.append(f"   └ {t['reason']}")
            if t.get("supply_str"):
                lines.append(f"   💰 {t['supply_str']}")
            g = t.get("gap20", 0)
            lines.append(f"   📊 20일선 {'+' if g >= 0 else ''}{g:.1f}%"
                         + (" 🔥과열" if t.get("overheat") else ""))
        lines.append("")
    if snap.holdings_status:
        lines.append("📋 *보유종목 상태*")
        for h in snap.holdings_status:
            em = _STATE_EMOJI.get(h.get("state", "UNKNOWN"), "•")
            sign = "+" if h.get("profit_rate", 0) >= 0 else ""
            lines.append(f"  {em} {_naver_link(h['name'], h['ticker'])} "
                         f"({sign}{h.get('profit_rate', 0):.1f}%) — {h['reason']}")
        lines.append("")
    return lines


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
        mac = []
        if snap.fx:
            mac.append(f"원/달러 {snap.fx['value']:,.0f}({snap.fx['change_pct']:+.1f}%)")
        if snap.wti:
            mac.append(f"WTI ${snap.wti['value']:,.1f}({snap.wti['change_pct']:+.1f}%)")
        if mac:
            lines.append("💱 " + "  ·  ".join(mac))
        if idx_parts or mac:
            lines.append("")

    # AI 한줄 요약
    if snap.summary:
        lines.append(snap.summary)
        lines.append("")

    # 주도 테마 (오늘 상위종목이 속한 테마 — 모멘텀)
    if snap.leading_themes:
        lines.append("🚀 *주도 테마* (오늘 상위종목): " + " · ".join(snap.leading_themes[:5]))
        lines.append("")

    # 강세 테마 Top 3 (테마 평균 등락률)
    if snap.top_themes:
        lines.append("🔥 *강세 테마*")
        for t in snap.top_themes[:3]:
            sign = "+" if t.change_pct >= 0 else ""
            lines.append(f"  · {t.name} {sign}{t.change_pct:.2f}%")
        lines.append("")

    lines.extend(_format_strategy_holdings(snap))
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
        mac = []
        if snap.fx:
            mac.append(f"원/달러 {snap.fx['value']:,.0f}({snap.fx['change_pct']:+.1f}%)")
        if snap.wti:
            mac.append(f"WTI ${snap.wti['value']:,.1f}({snap.wti['change_pct']:+.1f}%)")
        if mac:
            lines.append("💱 " + "  ·  ".join(mac))
        if idx_parts or mac:
            lines.append("")

    if snap.summary:
        lines.append(snap.summary)
        lines.append("")

    # 주도 테마 (오늘 상위종목이 속한 테마 — 모멘텀)
    if snap.leading_themes:
        lines.append("🚀 *주도 테마* (오늘 상위종목): " + " · ".join(snap.leading_themes[:5]))
        lines.append("")

    # 강세 테마 Top 3
    if snap.top_themes:
        lines.append("🔥 *강세 테마*")
        for t in snap.top_themes[:3]:
            sign = "+" if t.change_pct >= 0 else ""
            lines.append(f"  · {t.name} {sign}{t.change_pct:.2f}%")
        lines.append("")

    lines.extend(_format_strategy_holdings(snap))
    lines.append(f"📄 [전체 리포트 보기]({url})")
    lines.append("")
    lines.append("_※ 참고용 정보. 투자 판단·책임은 본인._")
    return "\n".join(lines)


def _format_us_morning_summary(snap: MarketSnapshot) -> str:
    """미국장 아침 요약 메시지 (us_morning) — 지수·AI요약·강세섹터·주요종목·한국 시사점."""
    url = report_url(snap)
    date = snap.generated_at.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [f"🌎 *미국 증시 마감 요약* — {date}", ""]

    if snap.us_indices:
        parts = []
        for q in snap.us_indices[:2]:  # S&P500·나스닥
            sign = "+" if q.get("change_pct", 0) >= 0 else ""
            parts.append(f"{q['name']} {q['price']:,.0f}({sign}{q.get('change_pct', 0):.2f}%)")
        if snap.gold:
            parts.append(f"금 ${snap.gold['value']:,.0f}({snap.gold['change_pct']:+.1f}%)")
        if snap.wti:
            parts.append(f"WTI ${snap.wti['value']:,.1f}({snap.wti['change_pct']:+.1f}%)")
        lines.append("📊 " + "  ·  ".join(parts))
        lines.append("")

    if snap.summary:
        lines.append(snap.summary)
        lines.append("")
    if snap.why_moved:
        lines.append(f"💡 {snap.why_moved}")
        lines.append("")

    if snap.us_sectors:
        lines.append("🔥 *강세 섹터*")
        for q in snap.us_sectors[:5]:
            sign = "+" if q.get("change_pct", 0) >= 0 else ""
            lines.append(f"  · {q['name']} {sign}{q.get('change_pct', 0):.2f}%")
        lines.append("")

    if snap.us_bigtech:
        lines.append("📈 *주요 상승 종목*")
        for q in snap.us_bigtech[:5]:
            sign = "+" if q.get("change_pct", 0) >= 0 else ""
            lines.append(f"  · {q['name']} {sign}{q.get('change_pct', 0):.2f}%")
        lines.append("")

    if snap.theme_commentary:
        lines.append(f"🌏 *한국장 시사점*\n{snap.theme_commentary}")
        lines.append("")

    # 미국 강세테마 연동 한국 시초 매수 Top3
    if getattr(snap, "top3", None):
        lines.append("🏆 *오늘 시초 매수 Top 3* (미국 강세테마 연동)")
        for i, t in enumerate(snap.top3, 1):
            sign = "+" if t.get("change_pct", 0) >= 0 else ""
            mc = f" · 시총 {t['marcap_str']}" if t.get("marcap_str") else ""
            lines.append(f"{i}. {_naver_link(t['name'], t['ticker'])} "
                         f"{t['price']:,.0f}원 ({sign}{t.get('change_pct', 0):.1f}%){mc}")
            lines.append(f"   └ {t['reason']}")
            if t.get("supply_str"):
                lines.append(f"   💰 {t['supply_str']}")
            g = t.get("gap20", 0)
            lines.append(f"   📊 20일선 {'+' if g >= 0 else ''}{g:.1f}%"
                         + (" 🔥과열" if t.get("overheat") else ""))
        lines.append("")

    lines.append(f"📄 [전체 리포트 보기]({url})")
    lines.append("")
    lines.append("_※ 시초 매수는 갭·변동성 위험이 큽니다. 참고용 정보, 판단·책임은 본인._")
    return "\n".join(lines)


async def send_report(snap: MarketSnapshot) -> bool:
    """리포트 요약을 텔레그램으로 발송. 성공 여부 반환."""
    settings = get_settings()
    chat_ids = settings.allowed_chat_ids()
    if not chat_ids:
        logger.warning("telegram_no_chat_id — allowed_chat_ids 비어있음")
        return False

    if snap.mode == "us_morning":
        text = _format_us_morning_summary(snap)
    elif snap.mode == "pre_close":
        text = _format_pre_summary(snap)
    else:
        text = _format_post_summary(snap)

    bot = Bot(token=settings.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)

    # 화이트리스트 전체 수신자에게 발송 (여러 명 가능)
    ok_any = False
    for cid in chat_ids:
        cid = str(cid)
        try:
            await notifier.send(cid, text)
            logger.info("telegram_sent mode=%s chat_id=%s", snap.mode, cid)
            ok_any = True
        except Exception as exc:
            logger.error("telegram_send_failed mode=%s chat_id=%s error=%s", snap.mode, cid, exc)
    return ok_any
