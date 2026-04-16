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
        """Find an existing session or create a new one.

        If the envelope's message has an explicit session_id, try to load that
        specific session first (supports ``--resume``).  Otherwise fall back to
        the channel+sender lookup.
        """
        channel_id = envelope.source_channel
        sender_id = envelope.message.sender_id
        explicit_id = envelope.message.session_id

        # If an explicit session_id is provided, try to resume it
        if explicit_id:
            if explicit_id in self._cache:
                return self._cache[explicit_id]
            session = await self._store.get_session(explicit_id)
            if session:
                self._cache[explicit_id] = session
                return session
            # Explicit ID not found — create with that ID
            session = Session(
                id=explicit_id,
                channel_id=channel_id,
                sender_id=sender_id,
            )
            await self._store.save_session(session)
            self._cache[explicit_id] = session
            return session

        # Fallback: find by channel + sender
        cache_key = f"{channel_id}:{sender_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

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

    async def get_session(self, session_id: str) -> Session | None:
        """Load a session by its ID."""
        session = await self._store.get_session(session_id)
        if session:
            cache_key = f"{session.channel_id}:{session.sender_id}"
            self._cache[cache_key] = session
        return session

    async def list_sessions(
        self, channel_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List recent sessions with summary info."""
        return await self._store.list_sessions(channel_id=channel_id, limit=limit)

    async def update_session(self, session: Session) -> None:
        """Persist session state."""
        await self._store.save_session(session)
