"""Store protocol — the interface all storage backends must satisfy."""

from __future__ import annotations

from typing import Protocol

from sanfuclaw.core.message import Message
from sanfuclaw.core.session import Session


class Store(Protocol):
    async def init(self) -> None:
        """Initialize the store (run migrations, etc.)."""
        ...

    async def close(self) -> None:
        """Close the store connection."""
        ...

    async def save_message(self, message: Message) -> None:
        ...

    async def get_history(self, session_id: str, limit: int = 50) -> list[Message]:
        ...

    async def save_session(self, session: Session) -> None:
        ...

    async def get_session(self, session_id: str) -> Session | None:
        ...

    async def find_session(self, channel_id: str, sender_id: str) -> Session | None:
        """Find an existing session by channel + sender."""
        ...

    async def list_sessions(
        self, channel_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List recent sessions with summary info (id, channel, updated_at, message_count, last_message)."""
        ...
