from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from src.bot import commands
from src.config.settings import Settings

logger = logging.getLogger(__name__)


def build_application(
    token: str, settings: Settings, deps: dict[str, Any]
) -> Application:
    """Assembles the Telegram Application with whitelist-guarded command handlers."""
    app = Application.builder().token(token).build()
    allowed_ids = set(settings.allowed_chat_ids())

    def guarded(
        fn: Callable[..., Coroutine[Any, Any, None]]
    ) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]:
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            cid = update.effective_chat.id if update.effective_chat else None
            if cid not in allowed_ids:
                logger.warning("unauthorized_blocked chat_id=%s", cid)
                return  # silently ignore — do not reply to unauthorized users
            await fn(update, context, deps=deps)

        wrapper.__name__ = fn.__name__
        return wrapper

    handlers = [
        ("start", commands.cmd_start),
        ("help", commands.cmd_help),
        ("watch", commands.cmd_watch),
        ("unwatch", commands.cmd_unwatch),
        ("watchlist", commands.cmd_watchlist),
        ("settings", commands.cmd_settings),
        ("sync_trades", commands.cmd_sync_trades),
        ("history", commands.cmd_history),
        ("whatif", commands.cmd_whatif),
    ]
    for name, fn in handlers:
        app.add_handler(CommandHandler(name, guarded(fn)))

    return app
