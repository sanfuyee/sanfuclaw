"""Session manager — creates, resolves, and persists sessions."""

from __future__ import annotations

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.storage.base import Store


class SessionManager:
    """Manages session lifecycle with persistent storage."""

    def __init__(self, store: Store):
        self._store = store
        self._cache: dict[str, Session] = {}

    async def get_or_create(self, envelope: Envelope) -> Session:
        """Find an existing session or create a new one."""
        channel_id = envelope.source_channel
        sender_id = envelope.message.sender_id

        # Check in-memory cache first
        cache_key = f"{channel_id}:{sender_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Check persistent storage
        session = await self._store.find_session(channel_id, sender_id)
        if session:
            self._cache[cache_key] = session
            return session

        # Create new session
        session = Session(
            channel_id=channel_id,
            sender_id=sender_id,
        )
        await self._store.save_session(session)
        self._cache[cache_key] = session
        return session

    async def save_message(self, message: Message) -> None:
        """Persist a message."""
        await self._store.save_message(message)

    async def update_session(self, session: Session) -> None:
        """Persist session state."""
        await self._store.save_session(session)
