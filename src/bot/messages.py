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


def format_screening_result(picks: list, mode_label: str = "조건 검색") -> str:
    """조건 검색 결과 → 텔레그램 메시지.

    picks: list[StockPick] (src.screener.pipeline)
    """
    if not picks:
        return (
            f"🔍 *{mode_label}*\n\n"
            f"조건을 충족하는 종목이 없습니다.\n"
            f"_{_DISCLAIMER}_"
        )

    lines = [f"🔍 *{mode_label}* — {len(picks)}종목 포착", ""]
    for p in picks:
        sign = "+" if p.change_pct >= 0 else ""
        # 매칭 전략·의견
        opinions = " / ".join(dict.fromkeys(p.opinions))  # 중복 제거, 순서 유지
        lines.append(f"*{p.name}* ({p.ticker})")
        lines.append(f"  {p.price:,.0f}원 ({sign}{p.change_pct:.2f}%)  → _{opinions}_")
        # 전략별 근거
        for m in p.matches:
            reasons = ", ".join(m.reasons[:3])
            lines.append(f"  ▸ [{m.strategy_name}] {reasons}")
        lines.append("")

    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def format_screening_by_strategy(picks: list, strategies: list | None = None) -> str:
    """조건검색 결과를 전략별로 그룹핑 → 볼드체 텔레그램 메시지.

    picks: list[StockPick]
    strategies: list[Strategy] (전략 순서·설명 표시용, 선택)
    """
    from datetime import datetime

    if not picks:
        return (
            "🔍 *조건검색 결과*\n\n"
            "오늘 조건을 충족하는 종목이 없습니다.\n"
            f"_{_DISCLAIMER}_"
        )

    # 전략명 → [(pick, match)] 그룹핑
    by_strategy: dict[str, list] = {}
    for p in picks:
        for m in p.matches:
            by_strategy.setdefault(m.strategy_name, []).append((p, m))

    # 전략 순서 (config 순서 유지, 없으면 등장 순)
    order = [s.name for s in strategies] if strategies else list(by_strategy.keys())
    for name in by_strategy:
        if name not in order:
            order.append(name)

    now = datetime.now().strftime("%m/%d %H:%M")
    lines = [f"🔍 *조건검색 결과* ({now})", ""]

    for sname in order:
        items = by_strategy.get(sname)
        if not items:
            continue
        lines.append(f"━━━━━━━━━━━━━━━")
        lines.append(f"📌 *{sname}*  ({len(items)}종목)")
        lines.append("")
        for p, m in items:
            sign = "+" if p.change_pct >= 0 else ""
            lines.append(f"▪️ *{p.name}* `{p.ticker}`  {p.price:,.0f}원 ({sign}{p.change_pct:.2f}%)")
            # 섹터 (주도섹터면 강조)
            if getattr(p, "sector", ""):
                tag = "🔥 주도섹터" if getattr(p, "is_leading_sector", False) else "섹터"
                lines.append(f"   🏷 {tag}: {p.sector}")
            for r in m.reasons:
                lines.append(f"   └ {r}")
            # 뉴스 링크
            if getattr(p, "news_title", "") and getattr(p, "news_url", ""):
                lines.append(f"   📰 [{p.news_title[:45]}]({p.news_url})")
        lines.append("")

    # 복붙용 종목코드 리스트 (한투 앱 관심종목 일괄등록용)
    all_tickers = list(dict.fromkeys(p.ticker for p in picks))
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📋 *관심종목 복사용* (한투 앱에 붙여넣기)")
    lines.append(f"`{' '.join(all_tickers)}`")
    lines.append("")
    lines.append(f"_{_DISCLAIMER}_")
    return "\n".join(lines)


def format_pick_caption(pick, match) -> str:
    """차트 이미지에 붙일 캡션 (종목명/전략/섹터/근거/뉴스)."""
    sign = "+" if pick.change_pct >= 0 else ""
    lines = [
        f"*{pick.name}* `{pick.ticker}`",
        f"{pick.price:,.0f}원 ({sign}{pick.change_pct:.2f}%) · {match.strategy_name}",
    ]
    if getattr(pick, "sector", ""):
        tag = "🔥 주도섹터" if getattr(pick, "is_leading_sector", False) else "섹터"
        lines.append(f"🏷 {tag}: {pick.sector}")
    for r in match.reasons:
        lines.append(f"└ {r}")
    if getattr(pick, "news_title", "") and getattr(pick, "news_url", ""):
        lines.append(f"📰 [{pick.news_title[:45]}]({pick.news_url})")
    return "\n".join(lines)


def format_error(reason: str, attempts: int, last_success: str | None = None) -> str:
    return (
        f"⚠️ 분석 실패: {reason}\n"
        f"- 시도: {attempts}회 / 마지막 성공: {last_success or '없음'}\n"
        f"- 조치: 잠시 후 다시 시도하거나 /help 참고"
    )
