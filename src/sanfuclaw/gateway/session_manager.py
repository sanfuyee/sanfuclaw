"""Session manager — creates, resolves, and persists sessions."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.storage.base import Store


_DEFAULT_CACHE_SIZE = 1000
_DEFAULT_TTL = timedelta(hours=1)


class SessionManager:
    """Manages session lifecycle with persistent storage.

    Cache layout:
    - primary: ``session.id -> (Session, last_access_ts)`` as an LRU
      (``OrderedDict``), bounded by ``cache_size``
    - secondary: ``"channel:sender" -> session.id`` lookup index so the
      implicit path (no explicit id) can resolve to the same cached object
      the explicit path would

    TTL is refreshed on every access, so hot sessions stay resident and
    cold ones fall out after ``ttl`` of inactivity (and get reloaded from
    the store on the next hit — which also catches external DB edits).
    """

    def __init__(
        self,
        store: Store,
        cache_size: int = _DEFAULT_CACHE_SIZE,
        ttl: timedelta = _DEFAULT_TTL,
    ):
        self._store = store
        self._cache: OrderedDict[str, tuple[Session, datetime]] = OrderedDict()
        self._channel_index: dict[str, str] = {}
        self._cache_size = cache_size
        self._ttl = ttl

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _cache_get(self, session_id: str) -> Session | None:
        entry = self._cache.get(session_id)
        if entry is None:
            return None
        session, stored_at = entry
        if self._now() - stored_at > self._ttl:
            self._cache_drop(session_id)
            return None
        self._cache[session_id] = (session, self._now())
        self._cache.move_to_end(session_id)
        return session

    def _cache_put(self, session: Session) -> None:
        self._cache[session.id] = (session, self._now())
        self._cache.move_to_end(session.id)
        while len(self._cache) > self._cache_size:
            evicted_id, _ = self._cache.popitem(last=False)
            self._drop_index_for(evicted_id)

    def _cache_drop(self, session_id: str) -> None:
        self._cache.pop(session_id, None)
        self._drop_index_for(session_id)

    def _drop_index_for(self, session_id: str) -> None:
        stale = [k for k, v in self._channel_index.items() if v == session_id]
        for k in stale:
            self._channel_index.pop(k, None)

    def _index_key(self, channel_id: str, sender_id: str) -> str:
        return f"{channel_id}:{sender_id}"

    def _index_put(self, channel_id: str, sender_id: str, session_id: str) -> None:
        self._channel_index[self._index_key(channel_id, sender_id)] = session_id

    async def get_or_create(self, envelope: Envelope) -> Session:
        """Find an existing session or create a new one.

        If the envelope's message has an explicit session_id, try to load that
        specific session first (supports ``--resume``).  Otherwise fall back to
        the channel+sender lookup.
        """
        channel_id = envelope.source_channel
        sender_id = envelope.message.sender_id
        explicit_id = envelope.message.session_id

        if explicit_id:
            cached = self._cache_get(explicit_id)
            if cached is not None:
                return cached
            session = await self._store.get_session(explicit_id)
            if session is None:
                session = Session(
                    id=explicit_id,
                    channel_id=channel_id,
                    sender_id=sender_id,
                )
                await self._store.save_session(session)
            self._cache_put(session)
            self._index_put(session.channel_id, session.sender_id, session.id)
            return session

        index_key = self._index_key(channel_id, sender_id)
        indexed_id = self._channel_index.get(index_key)
        if indexed_id is not None:
            cached = self._cache_get(indexed_id)
            if cached is not None:
                return cached

        session = await self._store.find_session(channel_id, sender_id)
        if session is None:
            session = Session(channel_id=channel_id, sender_id=sender_id)
            await self._store.save_session(session)
        self._cache_put(session)
        self._index_put(channel_id, sender_id, session.id)
        return session

    async def save_message(self, message: Message) -> None:
        """Persist a message."""
        await self._store.save_message(message)

    async def get_session(self, session_id: str) -> Session | None:
        """Load a session by its ID."""
        cached = self._cache_get(session_id)
        if cached is not None:
            return cached
        session = await self._store.get_session(session_id)
        if session:
            self._cache_put(session)
            self._index_put(session.channel_id, session.sender_id, session.id)
        return session

    async def list_sessions(
        self, channel_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List recent sessions with summary info."""
        return await self._store.list_sessions(channel_id=channel_id, limit=limit)

    async def update_session(self, session: Session) -> None:
        """Persist session state."""
        await self._store.save_session(session)
