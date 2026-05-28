from __future__ import annotations

import asyncio
import logging
import random

from telegram import Bot
from telegram.error import TelegramError

from src.bot.messages import format_signal_alert
from src.storage.repos import SignalRecord

logger = logging.getLogger(__name__)

MAX_RETRY = 3


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, chat_id: str, text: str, *, parse_mode: str = "Markdown"
    ) -> None:
        for attempt in range(MAX_RETRY):
            try:
                if attempt > 0:
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                await self._bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode=parse_mode,
                )
                return
            except TelegramError as exc:
                logger.warning(
                    "telegram_send_failed attempt=%d chat_id=%s error=%s",
                    attempt,
                    chat_id,
                    str(exc),
                )
        logger.error("telegram_send_exhausted chat_id=%s", chat_id)

    async def send_signal_alert(self, chat_id: str, record: SignalRecord) -> None:
        text = format_signal_alert(record)
        await self.send(chat_id, text)
