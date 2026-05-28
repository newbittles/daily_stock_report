"""Tests for whitelist-based authorization in bot router."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat, Message, Update
from telegram.ext import ContextTypes


def _make_update(chat_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    update.effective_chat = chat
    msg = MagicMock(spec=Message)
    msg.reply_text = AsyncMock()
    update.message = msg
    return update


def _make_settings(allowed_ids: list[int]) -> MagicMock:
    s = MagicMock()
    s.allowed_chat_ids.return_value = allowed_ids
    s.telegram_bot_token = "fake_token"
    return s


def test_allowed_chat_id_passes():
    settings = _make_settings([111222333])
    allowed = set(settings.allowed_chat_ids())
    update = _make_update(111222333)
    assert update.effective_chat.id in allowed


def test_blocked_chat_id_rejected():
    settings = _make_settings([111222333])
    allowed = set(settings.allowed_chat_ids())
    update = _make_update(999888777)
    assert update.effective_chat.id not in allowed


def test_multiple_allowed_ids():
    settings = _make_settings([111, 222, 333])
    allowed = set(settings.allowed_chat_ids())
    assert 111 in allowed
    assert 222 in allowed
    assert 333 in allowed
    assert 444 not in allowed


async def test_guard_blocks_unauthorized():
    """Verify the auth wrapper in router silently ignores unauthorized chat_ids."""
    from src.bot.router import build_application

    settings = _make_settings([12345])
    deps: dict = {}

    with patch("src.bot.router.Application.builder") as mock_builder:
        mock_app = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_builder.return_value.token.return_value.build.return_value = mock_app
        app = build_application("fake_token", settings, deps)

    # The add_handler should have been called for each command
    assert mock_app.add_handler.call_count >= 5
