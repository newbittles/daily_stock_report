from __future__ import annotations

import asyncio
import logging
import random

from telegram import Bot
from telegram.error import TelegramError

from src.bot.messages import format_signal_alert
from src.logging_setup import TELEGRAM_LOGGER
from src.storage.repos import SignalRecord

logger = logging.getLogger(__name__)
tg_log = logging.getLogger(TELEGRAM_LOGGER)  # 발송 전용 로그 → logs/telegram.log

MAX_RETRY = 3


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, chat_id: str, text: str, *, parse_mode: str = "Markdown"
    ) -> bool:
        """메시지 발송. 성공 시 True. 발송 결과를 logs/telegram.log에 기록."""
        preview = text.replace("\n", " ")[:50]
        for attempt in range(MAX_RETRY):
            try:
                if attempt > 0:
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                msg = await self._bot.send_message(
                    chat_id=int(chat_id),
                    text=text,
                    parse_mode=parse_mode,
                )
                tg_log.info(
                    "SENT ok chat_id=%s message_id=%s len=%d preview=%r",
                    chat_id, msg.message_id, len(text), preview,
                )
                return True
            except TelegramError as exc:
                tg_log.warning(
                    "SEND_FAIL attempt=%d/%d chat_id=%s error=%s",
                    attempt + 1, MAX_RETRY, chat_id, str(exc),
                )
        tg_log.error("SEND_EXHAUSTED chat_id=%s len=%d preview=%r", chat_id, len(text), preview)
        return False

    async def send_photo(
        self, chat_id: str, photo_path: str, caption: str = "", *, parse_mode: str = "Markdown"
    ) -> bool:
        """차트 이미지 등 사진 발송. caption은 1024자 제한 → 초과 시 잘림."""
        preview = caption.replace("\n", " ")[:50]
        cap = caption[:1024]
        for attempt in range(MAX_RETRY):
            try:
                if attempt > 0:
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                with open(photo_path, "rb") as f:
                    msg = await self._bot.send_photo(
                        chat_id=int(chat_id), photo=f, caption=cap, parse_mode=parse_mode,
                    )
                tg_log.info(
                    "PHOTO ok chat_id=%s message_id=%s file=%s preview=%r",
                    chat_id, msg.message_id, photo_path, preview,
                )
                return True
            except (TelegramError, OSError) as exc:
                tg_log.warning(
                    "PHOTO_FAIL attempt=%d/%d chat_id=%s file=%s error=%s",
                    attempt + 1, MAX_RETRY, chat_id, photo_path, str(exc),
                )
        tg_log.error("PHOTO_EXHAUSTED chat_id=%s file=%s", chat_id, photo_path)
        return False

    async def send_signal_alert(self, chat_id: str, record: SignalRecord) -> None:
        text = format_signal_alert(record)
        await self.send(chat_id, text)
