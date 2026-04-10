"""Message and Envelope — the fundamental data units flowing through the system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .types import MessageRole


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid4())


@dataclass(frozen=True)
class Message:
    """An immutable chat message."""

    role: MessageRole
    content: str
    id: str = field(default_factory=_uuid)
    channel_id: str = ""
    session_id: str = ""
    sender_id: str = ""
    timestamp: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_llm_dict(self) -> dict:
        """Convert to the format expected by LLM APIs."""
        return {"role": self.role.value, "content": self.content}


@dataclass
class Envelope:
    """Wraps a Message with routing info for the gateway."""

    message: Message
    source_channel: str
    target_agent: str | None = None
    reply_to: str | None = None
