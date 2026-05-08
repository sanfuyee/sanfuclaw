"""Channel protocol — the interface all platform adapters must satisfy."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from sanfuclaw.core.message import Envelope


@runtime_checkable
class Channel(Protocol):
    """Abstract platform adapter.

    Any class with these methods is a valid Channel — no subclassing needed.

    Channels MAY additionally define:
      `async def send_trace(self, session_id: str, content: str) -> None`
    The router calls it (when present) to deliver per-turn diagnostic info
    out-of-band. Channels without it just don't see the trace.
    """

    name: str

    async def start(self) -> None:
        """Connect to the platform and begin listening."""
        ...

    async def stop(self) -> None:
        """Gracefully disconnect."""
        ...

    async def send(
        self,
        session_id: str,
        content: str,
        *,
        streaming: bool = False,
        done: bool = False,
    ) -> None:
        """Send a message back to the platform.

        Called in two phases per turn:
          - streaming=True for each chunk as the LLM streams.
          - done=True with the full accumulated text when the turn ends.
        Channels are free to render incrementally (CLI), or buffer and emit
        only on done (Telegram / WeChat which prefer one final message).
        """
        ...

    async def receive(self) -> AsyncIterator[Envelope]:
        """Yield inbound envelopes as they arrive."""
        ...

    async def send_typing(self, session_id: str) -> None:
        """Optional: send typing indicator."""
        ...
