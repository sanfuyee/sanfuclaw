"""Event hook system — register callbacks for gateway events."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class Event(str, Enum):
    MESSAGE_RECEIVED = "message_received"
    RESPONSE_START = "response_start"
    RESPONSE_CHUNK = "response_chunk"
    RESPONSE_END = "response_end"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SESSION_CREATED = "session_created"
    ERROR = "error"


HookFn = Callable[..., Coroutine[Any, Any, None]]


class HookRegistry:
    """Register and fire event hooks."""

    def __init__(self):
        self._hooks: dict[Event, list[HookFn]] = {}

    def on(self, event: Event, fn: HookFn) -> None:
        """Register a hook for an event."""
        self._hooks.setdefault(event, []).append(fn)

    async def emit(self, event: Event, **kwargs) -> None:
        """Fire all hooks for an event."""
        for fn in self._hooks.get(event, []):
            try:
                await fn(**kwargs)
            except Exception as e:
                logger.error(f"Hook error on {event.value}: {e}")
