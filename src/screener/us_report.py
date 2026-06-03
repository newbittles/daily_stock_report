"""미국 스크리닝 결과 → 리포트 메시지 빌더 + 텔레그램 발송 (P4).

⚠️ DEPRECATED(2026-06-04, 사용자 결정 A): 텍스트 전용 standalone 발송 경로.
   미국 종목의 정식 리포트(웹 -us.html + 텔레그램 링크)는 **us_morning**(07:30)이
   담당한다(pipeline._collect_us_screening + report.html 미국 섹션). 본 모듈은 개발
   미리보기/스모크용으로만 유지(자동 발송 경로 아님). 신규 기능은 us_morning에 추가.

us_screening 전용 **독립 모듈** — 기존 `market_report` 코어(models/pipeline/analyzer)는
UI 담당 작업 영역이라 건드리지 않고, 텔레그램 발송 경로(settings·Bot·Notifier)만 재사용.
build는 순수(값→문자열, 테스트 가능), send만 외부.

design: docs/02-design/features/us-screening.design.md §13 P4
"""
from __future__ import annotations

import logging
from datetime import date

from src.datasource.us.names_ko import korean_name
from src.screener.us_pipeline import DISCLAIMER, USStockPick

logger = logging.getLogger(__name__)

# 백테스트 우위순(§12: B 방어적·C 추세) → 표기 순서
STRATEGY_ORDER: list[tuple[str, str]] = [
    ("C", "📈 C 추세추종"),
    ("B", "🔄 B 20일선 눌림목"),
    ("A", "📊 A 수렴 후 상승"),
    ("D", "🔃 D 추세 반전"),
]

BACKTEST_NOTE = "_※ 백테스트(750일): 기계적 엣지 약함 — 시그널 보조용, 진입·청산·판단은 본인._"

# cross_signal(5<10 데드+20이격) 배지 — 대세상승주 매매 단기조정/고점 판단 보조
CROSS_BADGE = {"PULLBACK": " 🟢단기눌림", "CORRECTION": " ⚠️조정시작"}


def _turnover(p: USStockPick) -> float:
    return p.price * (p.candles[-1].volume if p.candles else 0)


def _fmt_usd_turnover(value: float) -> str:
    """달러 거래대금 → 사람이 읽는 표기($1.2B/$340M/$5.0M). 미국은 '억'(원화) 부적합."""
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value / 1e3:.0f}K"


def _reason_for(p: USStockPick, initial: str) -> str:
    """매칭 전략(initial)의 근거 중 '통화 무관'한 것을 고른다.

    engine의 거래대금 reason은 '억'(원화) 포맷이라 미국 달러엔 부적합 → 회피.
    거래대금은 메인 줄에 달러로 따로 표기하므로 여기선 제외한다(중복·오표기 방지).
    """
    reasons: list[str] = []
    for m in p.matches:
        if m.strategy_name[:1] == initial:
            reasons.extend(m.reasons)
    non_won = [r for r in reasons if "억" not in r and "거래대금" not in r]
    return non_won[0] if non_won else ""


def build_us_screening_report(
    picks: list[USStockPick], top_n: int = 5, as_of: str | None = None,
) -> str:
    """전략별(C·B·A·D) 거래대금 상위 top_n 종목 → 텔레그램 Markdown 메시지.

    근거 수치 + 면책 동반(CLAUDE.md §2). picks 비면 '포착 없음' 메시지.
    """
    as_of = as_of or date.today().isoformat()
    header = [
        f"🇺🇸 *미국 종목 스크리닝* — {as_of}",
        "_S&P500 ∪ 나스닥 거래대금상위 · A/B/C/D 참고용 시그널_",
        "",
    ]
    if not picks:
        return "\n".join(header + ["오늘 포착된 시그널이 없습니다.", "", DISCLAIMER])

    body: list[str] = []
    for initial, label in STRATEGY_ORDER:
        grp = [p for p in picks if any(m.strategy_name[:1] == initial for m in p.matches)]
        if not grp:
            continue
        grp.sort(key=_turnover, reverse=True)
        shown = min(top_n, len(grp))
        body.append(f"*{label}* ({len(grp)}종목 중 Top {shown})")
        for p in grp[:top_n]:
            sector = (p.sector[:14]) if p.sector else "-"
            badge = CROSS_BADGE.get(p.cross_signal, "")
            turnover = _fmt_usd_turnover(_turnover(p))
            name_ko = korean_name(p.symbol, p.name)  # 한국어(티커) — 아는 종목만 한국어
            body.append(f"• `{p.symbol}` {name_ko[:20]} ({sector}) "
                        f"${p.price:,.1f} {p.change_pct:+.1f}% · 거래대금 {turnover}{badge}")
            reason = _reason_for(p, initial)
            if reason:
                body.append(f"   └ {reason}")
        body.append("")

    total = len({p.symbol for p in picks})
    footer = [f"총 {total}종목 포착.", "", DISCLAIMER, BACKTEST_NOTE]
    return "\n".join(header + body + footer)


async def send_us_screening_report(picks: list[USStockPick], top_n: int = 5) -> bool:
    """리포트를 텔레그램 화이트리스트로 발송. 성공 여부 반환 (외부 행동).

    기존 send_report 패턴 재사용 (settings.allowed_chat_ids + Bot + TelegramNotifier).
    """
    from telegram import Bot

    from src.config.settings import get_settings
    from src.notify.telegram.adapter import TelegramNotifier

    settings = get_settings()
    chat_ids = settings.allowed_chat_ids()
    if not chat_ids:
        logger.warning("us_report_no_chat_id — allowed_chat_ids 비어있음")
        return False

    text = build_us_screening_report(picks, top_n=top_n)
    bot = Bot(token=settings.telegram_bot_token)
    notifier = TelegramNotifier(bot=bot)

    ok_any = False
    for cid in chat_ids:
        cid = str(cid)
        try:
            await notifier.send(cid, text)
            logger.info("us_report_sent chat_id=%s", cid)
            ok_any = True
        except Exception as exc:  # noqa: BLE001
            logger.error("us_report_send_failed chat_id=%s error=%s", cid, exc)
    return ok_any
