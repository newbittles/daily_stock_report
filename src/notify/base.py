from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.storage.repos import SignalRecord


@runtime_checkable
class Notifier(Protocol):
    async def send(
        self, chat_id: str, text: str, *, parse_mode: str = "Markdown"
    ) -> None: ...

    async def send_signal_alert(self, chat_id: str, record: "SignalRecord") -> None: ...
