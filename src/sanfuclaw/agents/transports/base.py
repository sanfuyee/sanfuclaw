"""LLM Transport protocol — abstracts differences between LLM providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from sanfuclaw.core.types import StreamChunkType


@dataclass
class StreamChunk:
    """A chunk of data from an LLM streaming response."""

    type: StreamChunkType
    data: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    # Token usage (populated on USAGE chunks)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMTransport(Protocol):
    """Adapter for a specific LLM provider."""

    @property
    def message_format(self) -> str:
        """Return 'anthropic' or 'openai' to indicate message format."""
        ...

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream completion chunks from the LLM."""
        ...
