"""OpenAI-compatible transport — works with any OpenAI-compatible API (HPC-AI, etc.)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from openai import APIError, APIConnectionError, AsyncOpenAI

from sanfuclaw.core.types import StreamChunkType

from .base import StreamChunk

logger = logging.getLogger(__name__)


class OpenAICompatTransport:
    """LLM transport using any OpenAI-compatible API."""

    message_format = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.hpc-ai.com/inference/v1",
        default_model: str = "minimax/minimax-m2.5",
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
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
        """Stream response chunks from an OpenAI-compatible API."""
        # Prepend system message if provided
        all_messages = list(messages)
        if system:
            all_messages.insert(0, {"role": "system", "content": system})

        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": all_messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # Convert tool schemas from Anthropic format to OpenAI format if needed
        if tools:
            openai_tools = []
            for tool in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", tool.get("parameters", {})),
                    },
                })
            kwargs["tools"] = openai_tools

        max_retries = 3
        for attempt in range(max_retries):
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                break
            except (APIError, APIConnectionError) as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"LLM API error (attempt {attempt + 1}/{max_retries}), retrying in {wait}s: {e}")
                    await asyncio.sleep(wait)
                else:
                    raise

        tool_calls_accumulator: dict[int, dict] = {}

        last_usage = None

        async for chunk in stream:
            # Track latest usage (HPC-AI sends it on every chunk; we only want the final one)
            if hasattr(chunk, "usage") and chunk.usage:
                last_usage = chunk.usage

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Text content
            if delta.content:
                yield StreamChunk(
                    type=StreamChunkType.TEXT_DELTA,
                    data=delta.content,
                )

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_accumulator:
                        tool_calls_accumulator[idx] = {
                            "id": tc.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc.id:
                        tool_calls_accumulator[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_accumulator[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_accumulator[idx]["arguments"] += tc.function.arguments

            # Check for finish
            if chunk.choices[0].finish_reason == "stop":
                yield StreamChunk(type=StreamChunkType.STOP)
            elif chunk.choices[0].finish_reason == "tool_calls":
                # Emit accumulated tool calls
                for tc_data in tool_calls_accumulator.values():
                    try:
                        tool_input = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_input = {}
                    yield StreamChunk(
                        type=StreamChunkType.TOOL_USE,
                        tool_name=tc_data["name"],
                        tool_call_id=tc_data["id"],
                        tool_input=tool_input,
                    )
                yield StreamChunk(type=StreamChunkType.STOP)

        # Emit final usage after stream ends
        if last_usage:
            yield StreamChunk(
                type=StreamChunkType.USAGE,
                input_tokens=last_usage.prompt_tokens or 0,
                output_tokens=last_usage.completion_tokens or 0,
            )
