"""텔레그램 요약 발송 — 리포트 URL 동봉.

기존 src/notify/telegram/adapter.py 재사용.
"""
from __future__ import annotations

import logging

from telegram import Bot

from src.alerts.holdings_report import cross_badge
from src.config.settings import get_settings
from src.market_report.models import MarketSnapshot
from src.market_report.publisher import report_url
from src.notify.telegram.adapter import TelegramNotifier

logger = logging.getLogger(__name__)


_STATE_EMOJI = {"BREAKDOWN": "🔴", "STOP60": "🔴", "STOP20": "⚠️", "ADD": "🟢",
                "HOLD": "✅", "NEUTRAL": "➖", "UNKNOWN": "❔"}

# 미국 종목 cross_signal 배지 (앞 공백 포함)
_US_CROSS = {"PULLBACK": " 🟢단기눌림", "CORRECTION": " ⚠️조정시작"}


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
        if getattr(snap, "holdings_summary", ""):
            lines.append(f"🤖 {snap.holdings_summary}")
        for h in snap.holdings_status:
            em = _STATE_EMOJI.get(h.get("state", "UNKNOWN"), "•")
            sign = "+" if h.get("profit_rate", 0) >= 0 else ""
            badge = cross_badge(h.get("cross_signal"))
            lines.append(f"  {em} {_naver_link(h['name'], h['ticker'])} "
                         f"({sign}{h.get('profit_rate', 0):.1f}%){badge} — {h['reason']}")
        lines.append("")
    return lines


def _format_index_lines(snap: MarketSnapshot) -> list[str]:
    """주요 지수 4개 — 모바일 줄바꿈 자연스럽게 각각 한 줄씩."""
    lines: list[str] = []
    for idx in (snap.kospi, snap.kosdaq):
        if idx:
            mk = "코스피" if idx.market == "KOSPI" else "코스닥"
            lines.append(f"📊 {mk} {idx.value:,.1f} ({idx.change_pct:+.2f}%)")
    if snap.fx:
        lines.append(f"💱 원/달러 {snap.fx['value']:,.1f} ({snap.fx['change_pct']:+.2f}%)")
    if snap.wti:
        lines.append(f"🛢 WTI ${snap.wti['value']:,.1f} ({snap.wti['change_pct']:+.2f}%)")
    if lines:
        lines.append("")
    return lines


def _format_market_flows(snap: MarketSnapshot) -> list[str]:
    """투자자 수급 — 당일 순매수 + (전일) 병기, 시장별 1줄 (억)."""
    hist = snap.market_flows_history
    if not hist:
        return []
    today = hist[0]
    prev = hist[1] if len(hist) > 1 else None
    d = str(today.get("date", ""))
    head = f"💰 *투자자 수급* ({d[4:6]}/{d[6:8]} · 억"
    head += " · 괄호=전일)" if prev else ")"
    lines = [head]
    for mk, label in (("kospi", "코스피"), ("kosdaq", "코스닥")):
        f = today.get(mk) or {}
        if not f:
            continue

        def _cell(key: str, f=f, mk=mk) -> str:
            v = int(f.get(key, 0))
            s = f"{v:+,}"
            if prev and prev.get(mk):
                s += f"({int(prev[mk].get(key, 0)):+,})"
            return s

        lines.append(f"  {label} 개인 {_cell('personal')} · 외인 {_cell('foreign')} · 기관 {_cell('institution')}")
    lines.append("")
    return lines


def _format_pre_summary(snap: MarketSnapshot) -> str:
    """마감 전 텔레그램 요약 메시지 (Markdown)."""
    url = report_url(snap)
    date = snap.generated_at.strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append(f"🟡 *마감 전 리포트* — {date}")
    lines.append("")

    # 지수 (각 줄 1개 — 모바일 줄바꿈 자연스럽게)
    lines.extend(_format_index_lines(snap))

    # 투자자 수급 (당일 + 전일 병기, 억)
    lines.extend(_format_market_flows(snap))

    # AI 한줄 요약
    if snap.summary:
        lines.append(snap.summary)
        lines.append("")

    # 주도 테마 (오늘 상위종목 — 라벨 아래 줄바꿈)
    if snap.leading_themes:
        lines.append("🚀 *주도 테마* (오늘 상위종목):")
        lines.append(" · ".join(snap.leading_themes[:5]))
        lines.append("")

    # 강세 테마 Top 3 (테마 평균 등락률)
    if snap.top_themes:
        lines.append("🔥 *강세 테마*")
        for t in snap.top_themes[:3]:
            sign = "+" if t.change_pct >= 0 else ""
            lines.append(f"  · {t.name} {sign}{t.change_pct:.2f}%")
            if getattr(t, "description", ""):
                lines.append(f"    💡 {t.description}")
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

    # 지수 (각 줄 1개 — 모바일 줄바꿈 자연스럽게)
    lines.extend(_format_index_lines(snap))

    # 투자자 수급 (당일 + 전일 병기)
    lines.extend(_format_market_flows(snap))

    if snap.summary:
        lines.append(snap.summary)
        lines.append("")

    # 왜 움직였나 (마감 후 핵심 AI 산출물 — 기존 텔레그램에 누락되어 있던 항목)
    if snap.why_moved:
        lines.append(f"💡 {snap.why_moved}")
        lines.append("")

    # 주도 테마 (오늘 상위종목 — 라벨 아래 줄바꿈)
    if snap.leading_themes:
        lines.append("🚀 *주도 테마* (오늘 상위종목):")
        lines.append(" · ".join(snap.leading_themes[:5]))
        lines.append("")

    # 강세 테마 Top 3
    if snap.top_themes:
        lines.append("🔥 *강세 테마*")
        for t in snap.top_themes[:3]:
            sign = "+" if t.change_pct >= 0 else ""
            lines.append(f"  · {t.name} {sign}{t.change_pct:.2f}%")
            if getattr(t, "description", ""):
                lines.append(f"    💡 {t.description}")
        lines.append("")

    lines.extend(_format_strategy_holdings(snap))

    # 내일 관전 포인트 (post_close: candidate_picks에 watchpoint로 보관됨 — 텔레그램에 누락됐던 항목)
    watchpoints = [w.get("watchpoint") for w in (snap.candidate_picks or []) if w.get("watchpoint")]
    if watchpoints:
        lines.append("🔭 *내일 관전 포인트*")
        for w in watchpoints:
            lines.append(f"  · {w}")
        lines.append("")

    lines.append(f"📄 [전체 리포트 보기]({url})")
    lines.append("")
    lines.append("_※ 참고용 정보. 투자 판단·책임은 본인._")
    return "\n".join(lines)


def _format_hot_stocks(hot: list[dict]) -> list[str]:
    """핫종목 텔레그램 라인 — 거래대금 금액(전일대비:%) + 아래줄 수급현황 + 테마.

    종목 줄 / 거래대금 X억 (전일대비:+Y%) / 수급: 기관N·외인N·개인N / 테마. 모바일 가독성."""
    from src.datasource.market_cap import format_marcap

    lines: list[str] = ["🔥 *핫 종목* (상승률 상위)"]
    for h in hot:
        sign = "+" if h.get("change_pct", 0) >= 0 else ""
        lines.append(f"  · {_naver_link(h['name'], h['ticker'])} "
                     f"{h['price']:,.0f}원 ({sign}{h.get('change_pct', 0):.1f}%)")
        # 거래대금 금액 (전일대비:%)
        amt = h.get("tv_today")
        if amt:
            line = f"거래대금 {format_marcap(amt)}"
            tv = h.get("tv_change")
            if tv is not None:
                line += f" (전일대비:{'+' if tv >= 0 else ''}{tv:.0f}%)"
            lines.append("    " + line)
        # 수급현황 (기관/외인/개인 순매수 연속일) — 아래 줄
        st = h.get("streak") or {}
        streak = [f"{lbl}{st[k]}일" for k, lbl in (("orgn", "기관"), ("frgn", "외인"), ("prsn", "개인"))
                  if st.get(k, 0) > 0]
        if streak:
            lines.append("    수급: " + "·".join(streak) + " 순매수")
        if h.get("theme"):
            lines.append(f"    테마: {h['theme']}")
    lines.append("")
    return lines


def _format_midday_summary(snap: MarketSnapshot) -> str:
    """장중 리포트(정오) — 지수·수급(전일대비)·강세테마·핫종목·전날 추천 Top3 현황.

    텔레그램 전용(웹 없음). 모바일 가독성 위해 항목마다 줄바꿈.
    """
    date = snap.generated_at.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [f"🟢 *장중 리포트* — {date}", ""]

    # 지수 (코스피·코스닥·환율·유가 각 1줄)
    lines.extend(_format_index_lines(snap))

    # 투자자 수급 (당일 + 전일 병기, 억)
    lines.extend(_format_market_flows(snap))

    # AI 한줄 장중 코멘트 (실패 시 결정론 폴백이 summary에 들어있음)
    if snap.summary:
        lines.append(f"💡 {snap.summary}")
        lines.append("")

    # 주도 테마 (오늘 상위종목이 속한 테마)
    if snap.leading_themes:
        lines.append("🚀 *주도 테마* (오늘 상위종목):")
        lines.append(" · ".join(snap.leading_themes[:5]))
        lines.append("")

    # 강세 테마 Top 3 (테마 평균 등락률)
    if snap.top_themes:
        lines.append("🔥 *강세 테마*")
        for t in snap.top_themes[:3]:
            sign = "+" if t.change_pct >= 0 else ""
            lines.append(f"  · {t.name} {sign}{t.change_pct:.2f}%")
            if getattr(t, "description", ""):
                lines.append(f"    💡 {t.description}")
        lines.append("")

    # 핫 종목 (거래대금 상위 5, 시총 5000억↑) + 거래대금 전일대비·순매수 연속일·소속테마
    if getattr(snap, "hot_stocks", None):
        lines.extend(_format_hot_stocks(snap.hot_stocks))
    elif snap.top_gainers:  # 폴백(수집 실패 시 상승률 상위)
        lines.append("🔥 *핫 종목* (상승률 상위)")
        for s in snap.top_gainers[:5]:
            lines.append(f"  · {_naver_link(s.name, s.ticker)} "
                         f"{s.price:,.0f}원 (+{s.change_pct:.1f}%)")
        lines.append("")

    # 전날 추천 Top3 현황 (추천가 대비 + 오늘 등락 둘 다)
    if snap.prev_top3_status:
        d = snap.prev_top3_date
        head = f"🏆 *전날 추천 Top3 현황*"
        if d:
            head += f" ({d[5:]} 추천)"
        lines.append(head)
        for t in snap.prev_top3_status:
            rp = t.get("return_pct", 0.0)
            tp = t.get("today_pct", 0.0)
            rs = "+" if rp >= 0 else ""
            ts = "+" if tp >= 0 else ""
            lines.append(f"  · {_naver_link(t['name'], t['ticker'])} "
                         f"추천가대비 {rs}{rp:.1f}% (오늘 {ts}{tp:.1f}%)")
        lines.append("")

    lines.append(f"📄 [전체 리포트 보기]({report_url(snap)})")
    lines.append("")
    lines.append("_※ 참고용 정보. 투자 판단·책임은 본인._")
    return "\n".join(lines)


def _format_us_morning_summary(snap: MarketSnapshot) -> str:
    """미국장 요약 메시지 (us_morning=마감 / us_premarket=프리장) — 지수·AI·섹터·종목·시사점."""
    url = report_url(snap)
    date = snap.generated_at.strftime("%Y-%m-%d %H:%M")
    if snap.mode == "us_premarket":
        lines: list[str] = [f"🌅 *미국장 장전(프리장) 리포트* — {date}",
                            "_종목 등락률은 프리장 기준 · ABCD는 직전 마감 일봉_", ""]
    else:
        lines = [f"🌎 *미국 증시 마감 요약* — {date}", ""]

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

    if getattr(snap, "us_news", None):
        lines.append("📰 *미국 시장 뉴스*")
        for n in snap.us_news[:5]:
            src = f" _{n['source']}_" if n.get("source") else ""
            lines.append(f"  · {n['title']}{src}")
        lines.append("")

    if snap.us_sectors:
        lines.append("🔥 *강세 섹터* (상승률 상위)")
        for q in snap.us_sectors[:4]:
            sign = "+" if q.get("change_pct", 0) >= 0 else ""
            lines.append(f"  · {q['name']} {sign}{q.get('change_pct', 0):.2f}%")
        lines.append("")
        weak = sorted(snap.us_sectors, key=lambda x: x.get("change_pct", 0))[:4]
        if weak:
            lines.append("🔻 *약세 섹터* (하락률 상위)")
            for q in weak:
                sign = "+" if q.get("change_pct", 0) >= 0 else ""
                lines.append(f"  · {q['name']} {sign}{q.get('change_pct', 0):.2f}%")
            lines.append("")

    if getattr(snap, "us_sector_leaders", None):
        lines.append("📈 *주요 종목* (섹터 대장)")
        for t in snap.us_sector_leaders:
            sign = "+" if t.get("change_pct", 0) >= 0 else ""
            lines.append(f"  · {t['name']} `{t['symbol']}` ({t['sector']}) "
                         f"${t['price']:,.2f} ({sign}{t.get('change_pct', 0):.1f}%)")
        lines.append("")
    if getattr(snap, "us_theme_leaders", None):
        lines.append("🧬 *관심 테마 대장* (양자·우주·AI 등)")
        for t in snap.us_theme_leaders:
            sign = "+" if t.get("change_pct", 0) >= 0 else ""
            badge = _US_CROSS.get(t.get("cross_signal"), "")
            theme = f" ({t['sector']})" if t.get("sector") else ""
            lines.append(f"• *{t['name']}* `{t['symbol']}`{theme} "
                         f"${t['price']:,.2f} ({sign}{t.get('change_pct', 0):.1f}%){badge}")
            meta = []
            if t.get("strategies"):
                meta.append(f"전략 {'/'.join(t['strategies'])}")
            if t.get("marcap_str"):
                meta.append(f"시총 {t['marcap_str']}")
            if t.get("turnover_str"):
                meta.append(f"거래대금 {t['turnover_str']}")
            if meta:
                lines.append("   " + " · ".join(meta))
        lines.append("")

    if snap.theme_commentary:
        lines.append(f"🌏 *한국장 시사점*\n{snap.theme_commentary}")
        lines.append("")

    # 미국 추천 Top3 (미국 종목 — 한국 종목 아님)
    if getattr(snap, "us_top3", None):
        lines.append("🏆 *미국 추천 Top 3*")
        for i, t in enumerate(snap.us_top3, 1):
            sign = "+" if t.get("change_pct", 0) >= 0 else ""
            badge = _US_CROSS.get(t.get("cross_signal"), "")
            lines.append(f"{i}. *{t['name']}* `{t['symbol']}` "
                         f"${t['price']:,.2f} ({sign}{t.get('change_pct', 0):.1f}%){badge}")
            meta = []
            if t.get("strategies"):
                meta.append(f"전략 {'/'.join(t['strategies'])}")  # #4 A/B/C/D
            if t.get("marcap_str"):
                meta.append(f"시총 {t['marcap_str']}")           # #2 원화
            if t.get("turnover_str"):
                meta.append(f"거래대금 {t['turnover_str']}")
            if meta:
                lines.append("   " + " · ".join(meta))
            if t.get("sector"):
                lines.append(f"   테마: {t['sector']}")           # #6 줄바꿈 (GICS Industry 세분)
            if t.get("reason"):
                lines.append(f"   └ {t['reason']}")
        lines.append("")

    # 미국 종목 스크리닝 A/B/C/D (전략별, A→D 순)
    if getattr(snap, "us_screen_groups", None):
        lines.append("🇺🇸 *미국 종목 스크리닝* (A/B/C/D)")
        for g in snap.us_screen_groups:
            picks = g.get("picks", [])
            if not picks:
                continue
            show_gap = g.get("initial") in ("B", "C")  # B·C 전략에 20MA 괴리 표시
            lines.append(f"*{g.get('label', '')}*")
            for p in picks[:5]:
                sign = "+" if p.get("change_pct", 0) >= 0 else ""
                badge = _US_CROSS.get(p.get("cross_signal"), "")
                lines.append(f"  • `{p['symbol']}` {p['name'][:20]} "
                             f"${p['price']:,.2f} {sign}{p.get('change_pct', 0):.1f}%{badge}")
                meta = []
                if p.get("marcap_str"):
                    meta.append(f"시총 {p['marcap_str']}")
                if p.get("turnover_str"):
                    meta.append(f"거래대금 {p['turnover_str']}")
                if show_gap:  # B·C 전략: 20일선 괴리 형광볼드(#11, #137)
                    g20 = p.get("gap20", 0)
                    meta.append(f"20MA괴리 *{'+' if g20 >= 0 else ''}{g20:.1f}%*")
                if meta:
                    lines.append("     " + " · ".join(meta))
                if p.get("sector"):
                    lines.append(f"     테마: {p['sector']}")  # #6 줄바꿈 (GICS Industry 세분)
        lines.append("")

    lines.append(f"📄 [전체 리포트 보기]({url})")
    lines.append("")
    lines.append("_※ 미국 A/B/C/D는 참고용 시그널(백테스트 엣지 약함). 매수 추천 아님, 판단·책임은 본인._")
    return "\n".join(lines)


async def send_report(snap: MarketSnapshot) -> bool:
    """리포트 요약을 텔레그램으로 발송. 성공 여부 반환."""
    settings = get_settings()
    chat_ids = settings.allowed_chat_ids()
    if not chat_ids:
        logger.warning("telegram_no_chat_id — allowed_chat_ids 비어있음")
        return False

    if snap.mode in ("us_morning", "us_premarket"):
        text = _format_us_morning_summary(snap)
    elif snap.mode == "midday":
        text = _format_midday_summary(snap)
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
