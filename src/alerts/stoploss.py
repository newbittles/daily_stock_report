"""보유종목 손절 모니터 — KIS 계좌 보유종목이 손절선에 닿으면 알림.

손절 기준 (사용자 전략):
  - 20일선 이탈 (종가 기준) — B 전략 핵심 손절선, 전 전략 공통 적용
  - (옵션) 매수가 대비 -X% 고정 손절

장 마감 전(14:50) 스케줄 또는 /holdings 수동 호출로 실행.
KIS get_balance()로 보유종목·매입가·현재가를 파싱.
"""
from __future__ import annotations

import asyncio
import logging
import random

from src.indicators.core import moving_average

logger = logging.getLogger(__name__)

_DISCLAIMER = "※ 참고용 알림입니다. 투자 판단·책임은 본인에게 있습니다."

# 20일선 이탈 경고 임계 (20일선 대비 이 % 이내로 근접해도 '주의')
WARN_NEAR_PCT = 1.0      # 20일선 +1% 이내 접근 시 주의
FIXED_STOP_PCT = -7.0    # 매수가 대비 -7% 고정 손절 (옵션)


async def check_holdings(adapter, ma_period: int = 20) -> list[dict]:
    """보유종목 손절/주의 판정. 알림 대상 리스트 반환.

    각 dict: {ticker, name, status, price, ma20, gap_pct, profit_rate, reasons}
      status: 'STOP'(손절) | 'WARN'(주의) | None(정상)
    """
    holdings = await adapter.get_balance()
    if not holdings:
        return []

    results: list[dict] = []
    for h in holdings:
        ticker = h["ticker"]
        await asyncio.sleep(random.uniform(0.3, 0.8))  # §7 딜레이
        try:
            candles = await adapter.get_ohlcv(ticker, days=40)
        except Exception as exc:
            logger.warning("stoploss_ohlcv_failed ticker=%s error=%s", ticker, exc)
            continue
        if len(candles) < ma_period:
            continue

        closes = [c.close for c in candles]
        ma20 = moving_average(closes, ma_period)[-1]
        if ma20 is None:
            continue

        price = h.get("current_price") or closes[-1]
        gap = (price - ma20) / ma20 * 100
        profit = h.get("profit_rate", 0.0)

        reasons: list[str] = []
        status = None

        # 1. 20일선 이탈 → 손절
        if price < ma20:
            status = "STOP"
            reasons.append(f"20일선 이탈 ({gap:+.1f}%)")
        # 2. 20일선 근접 → 주의
        elif gap <= WARN_NEAR_PCT:
            status = "WARN"
            reasons.append(f"20일선 근접 ({gap:+.1f}%)")

        # 3. 고정 손절 (-X%)
        if profit <= FIXED_STOP_PCT:
            status = "STOP"
            reasons.append(f"매수가 대비 {profit:.1f}% (고정손절 {FIXED_STOP_PCT}%)")

        if status:
            results.append({
                "ticker": ticker, "name": h["name"], "status": status,
                "price": price, "ma20": ma20, "gap_pct": gap,
                "profit_rate": profit, "reasons": reasons,
            })

    return results


def format_stoploss_alert(alerts: list[dict]) -> str:
    """손절 알림 텔레그램 메시지."""
    if not alerts:
        return "✅ 보유종목 모두 손절선 위에 있습니다 (20일선 유지)."

    stops = [a for a in alerts if a["status"] == "STOP"]
    warns = [a for a in alerts if a["status"] == "WARN"]

    lines = ["⚠️ *보유종목 손절 알림*", ""]
    if stops:
        lines.append("🔴 *손절 검토*")
        for a in stops:
            sign = "+" if a["profit_rate"] >= 0 else ""
            lines.append(f"▪️ *{a['name']}* `{a['ticker']}`")
            lines.append(f"   {a['price']:,.0f}원 (수익률 {sign}{a['profit_rate']:.1f}%)")
            for r in a["reasons"]:
                lines.append(f"   └ {r}")
        lines.append("")
    if warns:
        lines.append("🟡 *주의 (손절선 근접)*")
        for a in warns:
            sign = "+" if a["profit_rate"] >= 0 else ""
            lines.append(f"▪️ *{a['name']}* `{a['ticker']}`  {a['price']:,.0f}원 ({sign}{a['profit_rate']:.1f}%)")
            for r in a["reasons"]:
                lines.append(f"   └ {r}")
        lines.append("")

    lines.append(_DISCLAIMER)
    return "\n".join(lines)


class StopLossMonitor:
    """보유종목 손절 모니터 — 스케줄러/명령에서 공통 사용."""

    def __init__(self, adapter, notifier, allowed_chat_ids: list[str]) -> None:
        self._adapter = adapter
        self._notifier = notifier
        self._chat_ids = allowed_chat_ids

    async def run_once(self) -> list[dict]:
        try:
            alerts = await check_holdings(self._adapter)
        except Exception as exc:
            logger.error("stoploss_monitor_error error=%s", exc)
            return []

        # 손절/주의 있을 때만 발송 (정상이면 스케줄 알림 생략)
        if not alerts:
            logger.info("stoploss_monitor_ok no_alerts")
            return []

        msg = format_stoploss_alert(alerts)
        for chat_id in self._chat_ids:
            await self._notifier.send(chat_id, msg)
        logger.info("stoploss_alert_sent count=%d", len(alerts))
        return alerts
