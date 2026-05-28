from __future__ import annotations

from datetime import datetime

from src.storage.repos import SignalRecord, TradeRecord, WatchItem

_DISCLAIMER = "※ 참고용 시그널입니다. 투자 판단·책임은 본인에게 있습니다."


def format_signal_alert(record: SignalRecord) -> str:
    reasons_text = "\n".join(f" • {r}" for r in record.reasons) if record.reasons else " • (근거 없음)"
    now = datetime.now().strftime("%H:%M")
    return (
        f"🔔 *[매수 시그널]* {record.ticker}\n"
        f"패턴: {record.pattern} (score {record.score:.2f})\n"
        f"근거:\n{reasons_text}\n"
        f"{_DISCLAIMER}\n"
        f"(데이터 기준 {now})"
    )


def format_watchlist(items: list[WatchItem]) -> str:
    if not items:
        return "📋 관심종목이 없습니다."
    lines = ["📋 *관심종목 목록*"]
    for item in items:
        lines.append(f"• {item.ticker} — {item.name}")
    return "\n".join(lines)


def format_analysis_card(
    ticker: str,
    name: str,
    price: float,
    change_pct: float,
    pattern: str,
    reasons: list[str],
    formula_text: str,
    llm_comment: str | None = None,
) -> str:
    sign = "+" if change_pct >= 0 else ""
    reasons_text = "\n".join(f" • {r}" for r in reasons) if reasons else " • (해당 없음)"
    parts = [
        f"📊 *{name} ({ticker})*",
        f"현재가: {price:,.0f}원 ({sign}{change_pct:.2f}%)",
        f"패턴: {pattern or '해당 패턴 없음'}",
        f"근거:\n{reasons_text}",
        f"검색식: `{formula_text}`",
    ]
    if llm_comment:
        parts.append(f"AI 코멘트: {llm_comment}")
    parts.append(_DISCLAIMER)
    return "\n".join(parts)


def format_trade_history(records: list[TradeRecord]) -> str:
    if not records:
        return "📋 매매 내역이 없습니다.\n/sync_trades 로 키움 체결내역을 동기화하세요."
    lines = ["📋 *최근 매매 내역*"]
    for r in records:
        icon = "🔴 매도" if r.trade_type == "SELL" else "🟢 매수"
        amount = int(r.price * r.quantity)
        lines.append(
            f"{icon} {r.name or r.ticker} ({r.ticker})\n"
            f"  {r.price:,.0f}원 × {r.quantity:,}주 = {amount:,}원  ({r.trade_date})"
        )
    return "\n\n".join(lines)


def format_whatif(results: list[dict]) -> str:
    """results: [{record, current_price, return_pct, gain}]"""
    if not results:
        return "💭 매도 내역이 없습니다."
    lines = ["💭 *안 팔았다면?*", ""]
    for item in results:
        r: TradeRecord = item["record"]
        cur = item["current_price"]
        pct = item["return_pct"]
        gain = item["gain"]
        sign = "+" if pct >= 0 else ""
        lines.append(
            f"*{r.name or r.ticker}* ({r.ticker})\n"
            f"  매도가: {r.price:,.0f}원  ({r.trade_date})\n"
            f"  현재가: {cur:,.0f}원\n"
            f"  보유했다면: {sign}{pct:.2f}%  ({sign}{gain:,.0f}원)\n"
        )
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def format_error(reason: str, attempts: int, last_success: str | None = None) -> str:
    return (
        f"⚠️ 분석 실패: {reason}\n"
        f"- 시도: {attempts}회 / 마지막 성공: {last_success or '없음'}\n"
        f"- 조치: 잠시 후 다시 시도하거나 /help 참고"
    )
