"""Provider-specific message formatters.

Each LLM provider has its own way of stitching tool calls and tool
results into a chat-completion request:

- Anthropic nests tool_use blocks inside an assistant ``content`` array,
  and tool_results inside a user ``content`` array.
- OpenAI puts ``tool_calls`` on the assistant message and ``role=tool``
  messages for results, plus an optional ``reasoning_content`` for
  thinking-mode models (DeepSeek-R1, Kimi reasoning, QwQ).

Both formats also need *defensive deduplication*: providers have been
observed emitting the same tool_call_id twice in one stream (DeepSeek),
and replaying that as-is causes a 400. Each formatter dedups on a
stream-wide ``called_ids`` and ``resulted_ids`` set.

Pick a formatter by transport.message_format: ``anthropic`` or
``openai``.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from sanfuclaw.core.message import Message
from sanfuclaw.core.types import MessageRole

logger = logging.getLogger(__name__)


class MessageFormatter(Protocol):
    """Builds provider-shaped messages from sanfuclaw history."""

    def build(self, history: list[Message]) -> list[dict]:
        ...


class AnthropicMessageFormatter:
    def build(self, history: list[Message]) -> list[dict]:
        messages: list[dict] = []
        called_ids: set[str] = set()      # tool_use ids emitted
        resulted_ids: set[str] = set()    # tool_result ids emitted

        for msg in history:
            if msg.role == MessageRole.ASSISTANT and "tool_calls" in msg.metadata:
                content: list[dict] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.metadata["tool_calls"]:
                    if tc["id"] in called_ids:
                        logger.warning(
                            "Skipping duplicate tool_call id=%s name=%s in history",
                            tc["id"], tc["name"],
                        )
                        continue
                    called_ids.add(tc["id"])
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    })
                if content:
                    messages.append({"role": "assistant", "content": content})

            elif msg.role == MessageRole.TOOL:
                tcid = msg.metadata.get("tool_call_id", "")
                if not tcid or tcid in resulted_ids:
                    if tcid:
                        logger.warning(
                            "Skipping duplicate tool_result id=%s in history", tcid,
                        )
                    continue
                resulted_ids.add(tcid)
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tcid,
                        "content": msg.content,
                    }],
                })

            else:
                messages.append(msg.to_llm_dict())
        return messages


class OpenAIMessageFormatter:
    def build(self, history: list[Message]) -> list[dict]:
        messages: list[dict] = []
        called_ids: set[str] = set()
        resulted_ids: set[str] = set()

        for msg in history:
            if msg.role == MessageRole.ASSISTANT and "tool_calls" in msg.metadata:
                tool_calls: list[dict] = []
                for tc in msg.metadata["tool_calls"]:
                    if tc["id"] in called_ids:
                        logger.warning(
                            "Skipping duplicate tool_call id=%s name=%s in history",
                            tc["id"], tc["name"],
                        )
                        continue
                    called_ids.add(tc["id"])
                    tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"]),
                        },
                    })
                # An assistant turn with empty tool_calls + empty content is
                # rejected by OpenAI providers; drop it entirely.
                if not tool_calls and not msg.content:
                    continue
                m: dict = {"role": "assistant"}
                if tool_calls:
                    m["tool_calls"] = tool_calls
                if msg.content:
                    m["content"] = msg.content
                if msg.metadata.get("reasoning_content"):
                    m["reasoning_content"] = msg.metadata["reasoning_content"]
                messages.append(m)

            elif msg.role == MessageRole.ASSISTANT and msg.metadata.get("reasoning_content"):
                # Plain text assistant turn that happens to carry reasoning.
                # DeepSeek requires the original reasoning_content to come
                # back on every subsequent turn or it returns 400.
                if not msg.content:
                    logger.warning(
                        "Skipping empty assistant message with reasoning_content in history"
                    )
                    continue
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "reasoning_content": msg.metadata["reasoning_content"],
                })

            elif msg.role == MessageRole.TOOL:
                tcid = msg.metadata.get("tool_call_id", "")
                if not tcid or tcid in resulted_ids:
                    if tcid:
                        logger.warning(
                            "Skipping duplicate tool_result id=%s in history", tcid,
                        )
                    continue
                resulted_ids.add(tcid)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tcid,
                    "content": msg.content,
                })

            else:
                if msg.role == MessageRole.ASSISTANT and not msg.content:
                    logger.warning("Skipping empty assistant message in history")
                    continue
                messages.append(msg.to_llm_dict())
        return messages


def for_format(message_format: str) -> MessageFormatter:
    """Select a formatter by transport.message_format."""
    if message_format == "openai":
        return OpenAIMessageFormatter()
    return AnthropicMessageFormatter()
