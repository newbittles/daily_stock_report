"""보유종목 A/B/C 종합 상태 리포트 — 매일 보유종목을 진단해 발송.

KIS 계좌 보유종목 각각을 patterns.diagnose_holding으로 진단해
홀딩/손절/추가매수 상태로 분류한 일일 리포트를 텔레그램으로 보낸다.

기존 stoploss.py(단순 20선 손절)와 별개로, 전략 종합 관점의 풍부한 리포트.
/holdings 명령 + 스케줄러(마감 후 16:30)에서 공통 사용.
"""
from __future__ import annotations

import asyncio
import logging
import random

from src.patterns.core import diagnose_holding

logger = logging.getLogger(__name__)

_DISCLAIMER = "※ 참고용 알림입니다. 매매 판단·책임은 본인에게 있습니다."

# 상태 코드 → (정렬 우선순위, 그룹 제목, 라인 머리표)
_STATE_META = {
    "BREAKDOWN": (0, "🔴 추세 붕괴 (손절 검토)", "🔴"),
    "STOP60":    (1, "🔴 60선 이탈 (추세 손절)", "🔴"),
    "STOP20":    (2, "⚠️ 20선 이탈 (단기 손절)", "⚠️"),
    "ADD":       (3, "🟢 추가매수 후보 (눌림목)", "🟢"),
    "HOLD":      (4, "✅ 홀딩 (추세 양호)", "✅"),
    "NEUTRAL":   (5, "➖ 관망", "➖"),
    "UNKNOWN":   (6, "❔ 데이터 부족", "❔"),
}


async def diagnose_holdings(adapter, holdings: list[dict] | None = None) -> list[dict]:
    """보유종목 전체 진단. 각 dict: {ticker, name, state, reason, profit_rate, ...}.

    holdings=None이면 KIS 계좌(get_balance) 사용. 수동 보유종목 리스트를 주입하면
    (다른 증권사 보유 등) 그 종목들을 진단 — 항목: {ticker, name, avg_price, quantity}.
    profit_rate 없고 avg_price 있으면 현재가 대비 수익률을 계산한다.
    """
    if holdings is None:
        holdings = await adapter.get_balance()
    if not holdings:
        return []

    results: list[dict] = []
    for h in holdings:
        ticker = h["ticker"]
        await asyncio.sleep(random.uniform(0.3, 0.8))  # 전역 §7 랜덤 딜레이
        try:
            candles = await adapter.get_ohlcv(ticker, days=180)  # 120선 계산 여유
        except Exception as exc:
            logger.warning("holdings_ohlcv_failed ticker=%s error=%s", ticker, exc)
            continue
        if len(candles) < 25:
            continue

        r = diagnose_holding(candles)
        state = str(r.metrics.get("state", "UNKNOWN"))
        price = h.get("current_price") or candles[-1].close
        avg = h.get("avg_price")
        profit = h.get("profit_rate")
        if profit is None:
            profit = (price - avg) / avg * 100 if avg else 0.0
        qty = h.get("quantity")
        eval_pl = (price - avg) * qty if (avg and qty) else None
        results.append({
            "ticker": ticker,
            "name": h["name"],
            "state": state,
            "reason": r.reason,
            "price": price,
            "avg_price": avg,
            "quantity": qty,
            "eval_pl": eval_pl,
            "profit_rate": profit,
            "gap20_pct": r.metrics.get("gap20_pct"),
            "gap60_pct": r.metrics.get("gap60_pct"),
            "endstage": bool(r.metrics.get("endstage")),
        })
    return results


def format_holdings_report(rows: list[dict]) -> str:
    """보유종목 종합 리포트 텔레그램 메시지 (상태별 그룹)."""
    if not rows:
        return "📭 보유종목이 없습니다 (KIS 계좌 잔고 0)."

    rows = sorted(rows, key=lambda r: _STATE_META.get(r["state"], (9,))[0])
    lines = ["📋 *보유종목 전략 상태 리포트*", ""]

    cur = None
    for r in rows:
        meta = _STATE_META.get(r["state"], _STATE_META["UNKNOWN"])
        if r["state"] != cur:
            cur = r["state"]
            lines.append(f"*{meta[1]}*")
        sign = "+" if r["profit_rate"] >= 0 else ""
        warn = " ⚠️끝물" if r["endstage"] else ""
        lines.append(f"{meta[2]} *{r['name']}* `{r['ticker']}` {r['price']:,.0f}원 "
                     f"({sign}{r['profit_rate']:.1f}%){warn}")
        # 평단/수량/평가손익 (수동 보유종목 등 제공 시)
        if r.get("avg_price") and r.get("quantity"):
            pl = r.get("eval_pl")
            pl_str = f" · 평가손익 {pl:+,.0f}원" if pl is not None else ""
            lines.append(f"   평단 {r['avg_price']:,.0f} × {r['quantity']}주{pl_str}")
        lines.append(f"   └ {r['reason']}")
    lines.append("")

    # 요약 카운트
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    summ = []
    for st in ("BREAKDOWN", "STOP60", "STOP20", "ADD", "HOLD", "NEUTRAL"):
        if counts.get(st):
            summ.append(f"{_STATE_META[st][2]}{counts[st]}")
    if summ:
        lines.append("· ".join(summ))
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


class HoldingsReporter:
    """보유종목 종합 리포트 — 스케줄러/명령 공통."""

    def __init__(self, adapter, notifier, allowed_chat_ids: list[str]) -> None:
        self._adapter = adapter
        self._notifier = notifier
        self._chat_ids = allowed_chat_ids

    async def run_once(self) -> list[dict]:
        try:
            rows = await diagnose_holdings(self._adapter)
        except Exception as exc:
            logger.error("holdings_reporter_error error=%s", exc)
            return []
        msg = format_holdings_report(rows)
        for chat_id in self._chat_ids:
            await self._notifier.send(chat_id, msg)
        logger.info("holdings_report_sent count=%d", len(rows))
        return rows


async def run_holdings_report() -> list[dict]:
    """단일 진입점 — 설정에서 어댑터/노티파이어 조립 후 발송. 스케줄러/CLI 공용."""
    from telegram import Bot

    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter
    from src.notify.telegram.adapter import TelegramNotifier

    s = get_settings()
    chat_ids = [str(c) for c in s.allowed_chat_ids()]
    if not chat_ids:
        logger.warning("holdings_report_no_chat_id")
        return []
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    notifier = TelegramNotifier(bot=Bot(token=s.telegram_bot_token))
    return await HoldingsReporter(adapter, notifier, chat_ids).run_once()
