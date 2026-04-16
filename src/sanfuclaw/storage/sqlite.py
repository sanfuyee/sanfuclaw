"""SQLite storage backend."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from sanfuclaw.core.message import Message
from sanfuclaw.core.session import Session
from sanfuclaw.core.types import MessageRole

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class SQLiteStore:
    """SQLite-based persistent storage."""

    def __init__(self, db_path: str = "sanfuclaw.db"):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._run_migrations()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _ensure_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteStore not initialized — call init() first")
        return self._db

    async def _run_migrations(self) -> None:
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            sql = sql_file.read_text()
            await self._ensure_db().executescript(sql)

    async def save_session(self, session: Session) -> None:
        db = self._ensure_db()
        await db.execute(
            """INSERT INTO sessions (id, channel_id, sender_id, agent_name, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   metadata = excluded.metadata,
                   updated_at = excluded.updated_at""",
            (
                session.id,
                session.channel_id,
                session.sender_id,
                session.agent_name,
                json.dumps(session.metadata),
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )
        await db.commit()

    async def get_session(self, session_id: str) -> Session | None:
        db = self._ensure_db()
        async with db.execute(
            "SELECT id, channel_id, sender_id, agent_name, metadata, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            session = Session(
                id=row[0],
                channel_id=row[1],
                sender_id=row[2],
                agent_name=row[3],
                metadata=json.loads(row[4]),
                created_at=datetime.fromisoformat(row[5]),
                updated_at=datetime.fromisoformat(row[6]),
            )
            session.history = await self.get_history(session_id)
            return session

    async def find_session(self, channel_id: str, sender_id: str) -> Session | None:
        db = self._ensure_db()
        async with db.execute(
            "SELECT id FROM sessions WHERE channel_id = ? AND sender_id = ? ORDER BY updated_at DESC LIMIT 1",
            (channel_id, sender_id),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return await self.get_session(row[0])

    async def save_message(self, message: Message) -> None:
        db = self._ensure_db()
        await db.execute(
            """INSERT INTO messages (id, session_id, role, content, channel_id, sender_id, metadata, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO NOTHING""",
            (
                message.id,
                message.session_id,
                message.role.value,
                message.content,
                message.channel_id,
                message.sender_id,
                json.dumps(message.metadata),
                message.timestamp.isoformat(),
            ),
        )
        await db.commit()

    async def list_sessions(
        self, channel_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """List recent sessions with summary info."""
        db = self._ensure_db()
        if channel_id:
            query = """
                SELECT s.id, s.channel_id, s.sender_id, s.created_at, s.updated_at,
                       COUNT(m.id) as message_count,
                       (SELECT content FROM messages m2
                        WHERE m2.session_id = s.id AND m2.content != '' AND m2.role IN ('user', 'assistant')
                        ORDER BY m2.timestamp DESC LIMIT 1) as last_message
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.channel_id = ?
                GROUP BY s.id
                ORDER BY s.updated_at DESC LIMIT ?
            """
            params: tuple = (channel_id, limit)
        else:
            query = """
                SELECT s.id, s.channel_id, s.sender_id, s.created_at, s.updated_at,
                       COUNT(m.id) as message_count,
                       (SELECT content FROM messages m2
                        WHERE m2.session_id = s.id AND m2.content != '' AND m2.role IN ('user', 'assistant')
                        ORDER BY m2.timestamp DESC LIMIT 1) as last_message
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC LIMIT ?
            """
            params = (limit,)

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "channel_id": row[1],
                    "sender_id": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                    "message_count": row[5],
                    "last_message": row[6] or "",
                }
                for row in rows
            ]

    async def get_history(self, session_id: str, limit: int = 50) -> list[Message]:
        db = self._ensure_db()
        async with db.execute(
            "SELECT id, role, content, channel_id, sender_id, metadata, timestamp, session_id FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
            (session_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Message(
                    role=MessageRole(row[1]),
                    content=row[2],
                    id=row[0],
                    channel_id=row[3],
                    sender_id=row[4],
                    metadata=json.loads(row[5]),
                    timestamp=datetime.fromisoformat(row[6]),
                    session_id=row[7],
                )
                for row in rows
            ]
