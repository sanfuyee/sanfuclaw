"""Store protocol — the interface all storage backends must satisfy."""

from __future__ import annotations

from typing import Protocol

from sanfuclaw.core.message import Message
from sanfuclaw.core.schedule import Schedule
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

    async def get_history(
        self,
        session_id: str,
        limit: int | None = None,
        before: str | None = None,
    ) -> list[Message]:
        """Return messages for a session in chronological order.

        ``limit=None`` (default) returns the full history — required when
        rehydrating a Session for an agent, since the agent's own
        token-budget logic decides what to send to the LLM.

        With an explicit ``limit`` (UI/API path), returns the most recent
        ``limit`` messages, still in chronological order. Pass ``before``
        (an ISO timestamp) to page further back: returns up to ``limit``
        messages strictly older than that cursor.
        """
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

    # --- Schedules ---

    async def add_schedule(self, schedule: Schedule) -> None:
        ...

    async def get_schedule(self, schedule_id: str) -> Schedule | None:
        ...

    async def list_schedules(self, enabled_only: bool = False) -> list[Schedule]:
        ...

    async def update_schedule(self, schedule: Schedule) -> None:
        ...

    async def remove_schedule(self, schedule_id: str) -> None:
        ...
