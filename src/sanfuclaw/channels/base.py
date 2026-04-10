"""Channel protocol — the interface all platform adapters must satisfy."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from sanfuclaw.core.message import Envelope


@runtime_checkable
class Channel(Protocol):
    """Abstract platform adapter.

    Any class with these methods is a valid Channel — no subclassing needed.
    """

    name: str

    async def start(self) -> None:
        """Connect to the platform and begin listening."""
        ...

    async def stop(self) -> None:
        """Gracefully disconnect."""
        ...

    async def send(self, session_id: str, content: str, **kwargs) -> None:
        """Send a message back to the platform."""
        ...

    async def receive(self) -> AsyncIterator[Envelope]:
        """Yield inbound envelopes as they arrive."""
        ...

    async def send_typing(self, session_id: str) -> None:
        """Optional: send typing indicator."""
        ...
