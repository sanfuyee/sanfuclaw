"""Anthropic Claude transport — streaming completions via the official SDK."""

from __future__ import annotations

from typing import Any, AsyncIterator

import anthropic

from sanfuclaw.core.types import StreamChunkType

from .base import StreamChunk


class AnthropicTransport:
    """LLM transport using the Anthropic Claude API."""

    message_format = "anthropic"

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream response chunks from Claude."""
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield StreamChunk(
                            type=StreamChunkType.TEXT_DELTA,
                            data=event.delta.text,
                        )
                    elif event.delta.type == "input_json_delta":
                        yield StreamChunk(
                            type=StreamChunkType.TOOL_USE,
                            data=event.delta.partial_json,
                        )
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        yield StreamChunk(
                            type=StreamChunkType.TOOL_USE,
                            tool_name=event.content_block.name,
                            tool_call_id=event.content_block.id,
                        )
                elif event.type == "message_stop":
                    yield StreamChunk(type=StreamChunkType.STOP)

            # After streaming, check if we need tool results
            response = await stream.get_final_message()
            if response.stop_reason == "tool_use":
                for block in response.content:
                    if block.type == "tool_use":
                        yield StreamChunk(
                            type=StreamChunkType.TOOL_USE,
                            tool_name=block.name,
                            tool_call_id=block.id,
                            tool_input=block.input,
                        )
