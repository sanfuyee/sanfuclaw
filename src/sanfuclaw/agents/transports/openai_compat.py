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
        reasoning_chunk_count = 0  # fallback if usage doesn't report reasoning_tokens

        last_usage = None

        async for chunk in stream:
            # Track latest usage (HPC-AI sends it on every chunk; we only want the final one)
            if hasattr(chunk, "usage") and chunk.usage:
                last_usage = chunk.usage

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # Reasoning content (Kimi, DeepSeek-R1, QwQ, etc.)
            # Forward as a REASONING_DELTA chunk so the agent can persist the
            # full text on the assistant message — DeepSeek requires the
            # original `reasoning_content` to be replayed in history on every
            # subsequent turn or it returns 400.
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_chunk_count += 1
                yield StreamChunk(
                    type=StreamChunkType.REASONING_DELTA,
                    data=reasoning,
                )

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
                # Emit accumulated tool calls, deduped by id.
                # DeepSeek (and possibly other OpenAI-compatible providers)
                # has been observed emitting the same tool_call twice with
                # different `index` values but identical `id` and arguments.
                # Persisting both would crash the next turn — DeepSeek itself
                # rejects messages arrays with duplicate tool_call_id (400).
                seen_ids: set[str] = set()
                for tc_data in tool_calls_accumulator.values():
                    tc_id = tc_data["id"]
                    if tc_id and tc_id in seen_ids:
                        logger.warning(
                            "Dropping duplicate tool_call from upstream stream: "
                            "id=%s name=%s",
                            tc_id, tc_data["name"],
                        )
                        continue
                    if tc_id:
                        seen_ids.add(tc_id)
                    try:
                        tool_input = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_input = {}
                    yield StreamChunk(
                        type=StreamChunkType.TOOL_USE,
                        tool_name=tc_data["name"],
                        tool_call_id=tc_id,
                        tool_input=tool_input,
                    )
                yield StreamChunk(type=StreamChunkType.STOP)

        # Emit final usage after stream ends.
        #
        # OpenAI-compatible providers apply prompt caching *automatically* —
        # there is no request-side flag. What varies is how (and whether) the
        # usage response reports cache hits. We try the common field names:
        #
        #   OpenAI / Azure:  usage.prompt_tokens_details.cached_tokens
        #   DeepSeek:        usage.prompt_cache_hit_tokens
        #   Moonshot / Kimi: usage.cached_tokens
        #
        # Billed prompt_tokens already reflects caching, so we leave input_tokens
        # alone and surface cached_tokens separately for visibility in the trace.
        if last_usage:
            cached = 0
            details = getattr(last_usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            if not cached:
                cached = getattr(last_usage, "prompt_cache_hit_tokens", 0) or 0
            if not cached:
                cached = getattr(last_usage, "cached_tokens", 0) or 0

            # Reasoning tokens: prefer the provider's count, fall back to our chunk count
            reasoning = 0
            completion_details = getattr(last_usage, "completion_tokens_details", None)
            if completion_details is not None:
                reasoning = getattr(completion_details, "reasoning_tokens", 0) or 0
            if not reasoning:
                reasoning = reasoning_chunk_count

            yield StreamChunk(
                type=StreamChunkType.USAGE,
                input_tokens=last_usage.prompt_tokens or 0,
                output_tokens=last_usage.completion_tokens or 0,
                cached_tokens=cached,
                reasoning_tokens=reasoning,
            )
