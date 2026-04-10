"""Agent protocol — the interface all agents must satisfy."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from sanfuclaw.core.message import Envelope
from sanfuclaw.core.session import Session


@runtime_checkable
class Agent(Protocol):
    """Processes envelopes and produces streamed responses."""

    name: str

    async def process(self, envelope: Envelope, session: Session) -> AsyncIterator[str]:
        """Stream response chunks for the given envelope."""
        ...
