"""Session state model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .message import Message


@dataclass
class Session:
    """Represents a conversation session."""

    id: str = field(default_factory=lambda: str(uuid4()))
    channel_id: str = ""
    sender_id: str = ""
    agent_name: str = "default"
    history: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_message(self, message: Message) -> None:
        self.history.append(message)
        self.updated_at = datetime.now(timezone.utc)

    def get_llm_messages(self, system_prompt: str | None = None) -> list[dict]:
        """Build the messages list for LLM API calls."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for msg in self.history:
            messages.append(msg.to_llm_dict())
        return messages
