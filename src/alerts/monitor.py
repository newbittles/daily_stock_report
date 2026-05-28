from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

from src.datasource.base import MarketDataSource, Quote
from src.notify.base import Notifier
from src.storage.repos import AlertHistoryRepo, WatchItem, WatchlistRepo

logger = logging.getLogger(__name__)

_DISCLAIMER = "※ 참고용 알림입니다. 투자 책임은 본인에게 있습니다."


class WatchlistMonitor:
    """Evaluates watchlist conditions against live quotes and sends alerts."""

    def __init__(
        self,
        datasource: MarketDataSource,
        notifier: Notifier,
        watchlist_repo: WatchlistRepo,
        alert_repo: AlertHistoryRepo,
        allowed_chat_ids: list[str],
    ) -> None:
        self._ds = datasource
        self._notifier = notifier
        self._wl = watchlist_repo
        self._alert = alert_repo
        self._chat_ids = allowed_chat_ids

    async def run_once(self) -> None:
        items = self._wl.get_all()
        if not items:
            return

        logger.info("watchlist_monitor_start count=%d", len(items))

        for item in items:
            await asyncio.sleep(random.uniform(0.5, 1.5))  # §7 inter-request delay
            try:
                quote = await self._ds.get_quote(item.ticker)
                await self._evaluate(item, quote)
            except Exception as exc:
                logger.error("watchlist_monitor_error ticker=%s error=%s", item.ticker, exc)

    async def _evaluate(self, item: WatchItem, quote: Quote) -> None:
        triggered: list[str] = []

        change_threshold = float(item.conditions.get("change_pct", 5.0))
        if abs(quote.change_pct) >= change_threshold:
            sign = "+" if quote.change_pct >= 0 else ""
            triggered.append(
                f"등락률 {sign}{quote.change_pct:.2f}% (임계치 ±{change_threshold:.1f}%)"
            )

        vol_multiplier = float(item.conditions.get("vol_surge", 0.0))
        if vol_multiplier > 0:
            # vol_surge comparison requires average volume — placeholder for module-2 indicators
            pass

        if not triggered:
            return

        now = datetime.now().strftime("%H:%M")
        msg = (
            f"🔔 *관심종목 알림* {item.name} ({item.ticker})\n"
            f"현재가: {quote.price:,.0f}원\n"
            + "\n".join(f" • {t}" for t in triggered)
            + f"\n{_DISCLAIMER}\n({now} 기준)"
        )

        for chat_id in self._chat_ids:
            await self._notifier.send(chat_id, msg)
            self._alert.insert(
                chat_id=chat_id,
                alert_type="watch",
                ticker=item.ticker,
                message=msg,
            )

        logger.info("watchlist_alert_sent ticker=%s triggers=%s", item.ticker, triggered)
