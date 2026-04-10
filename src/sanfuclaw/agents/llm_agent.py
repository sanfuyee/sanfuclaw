"""LLM Agent — processes messages using an LLM transport with optional tool use."""

from __future__ import annotations

import json
from typing import AsyncIterator

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.core.types import MessageRole, StreamChunkType

from .transports.base import LLMTransport, StreamChunk
from sanfuclaw.tools.registry import ToolRegistry


class LLMAgent:
    """An agent that uses an LLM to process messages, with tool support."""

    def __init__(
        self,
        name: str,
        transport: LLMTransport,
        tool_registry: ToolRegistry | None = None,
        system_prompt: str = "You are a helpful personal AI assistant called Sanfuclaw.",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        self.name = name
        self._transport = transport
        self._tools = tool_registry or ToolRegistry()
        self._system_prompt = system_prompt
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _build_messages(self, session: Session) -> list[dict]:
        """Build messages in the correct format for the current transport."""
        fmt = getattr(self._transport, "message_format", "anthropic")
        if fmt == "openai":
            return self._build_openai_tool_messages(session)
        else:
            return self._build_anthropic_tool_messages(session)

    async def process(self, envelope: Envelope, session: Session) -> AsyncIterator[str]:
        """Process a message and stream back response chunks."""
        # Add user message to session history
        session.add_message(envelope.message)

        # Build messages for LLM (format-aware for tool call history)
        messages = self._build_messages(session)

        # Get tool schemas if tools are registered
        tools = self._tools.to_llm_schemas() or None

        # Stream response from LLM
        full_response = ""
        tool_calls: list[StreamChunk] = []

        async for chunk in self._transport.complete(
            messages=messages,
            tools=tools,
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=self._system_prompt,
        ):
            if chunk.type == StreamChunkType.TEXT_DELTA:
                full_response += chunk.data
                yield chunk.data
            elif chunk.type == StreamChunkType.TOOL_USE and chunk.tool_input:
                # Complete tool call with input ready
                tool_calls.append(chunk)
            elif chunk.type == StreamChunkType.STOP:
                pass

        # Handle tool calls if any
        if tool_calls:
            # Add assistant message with tool use to history
            session.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=full_response,
                session_id=session.id,
                metadata={"tool_calls": [
                    {"name": tc.tool_name, "id": tc.tool_call_id, "input": tc.tool_input}
                    for tc in tool_calls
                ]},
            ))

            # Execute each tool and collect results
            for tc in tool_calls:
                try:
                    result = await self._tools.execute(tc.tool_name, tc.tool_input, session)
                    result_str = result if isinstance(result, str) else json.dumps(result)
                except Exception as e:
                    result_str = f"Error: {e}"

                # Add tool result to session
                session.add_message(Message(
                    role=MessageRole.TOOL,
                    content=result_str,
                    session_id=session.id,
                    metadata={"tool_call_id": tc.tool_call_id},
                ))

                yield f"\n[Tool: {tc.tool_name}] "

            # Continue conversation with tool results
            follow_up_messages = self._build_messages(session)

            async for chunk in self._transport.complete(
                messages=follow_up_messages,
                tools=tools,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=self._system_prompt,
            ):
                if chunk.type == StreamChunkType.TEXT_DELTA:
                    full_response += chunk.data
                    yield chunk.data
        else:
            # No tool calls — save the assistant response
            session.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=full_response,
                session_id=session.id,
            ))

    def _build_anthropic_tool_messages(self, session: Session) -> list[dict]:
        """Build messages with tool use/results in Anthropic format."""
        messages = []
        for msg in session.history:
            if msg.role == MessageRole.ASSISTANT and "tool_calls" in msg.metadata:
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.metadata["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    })
                messages.append({"role": "assistant", "content": content})
            elif msg.role == MessageRole.TOOL:
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.metadata.get("tool_call_id", ""),
                        "content": msg.content,
                    }],
                })
            else:
                messages.append(msg.to_llm_dict())
        return messages

    def _build_openai_tool_messages(self, session: Session) -> list[dict]:
        """Build messages with tool use/results in OpenAI format."""
        messages = []
        for msg in session.history:
            if msg.role == MessageRole.ASSISTANT and "tool_calls" in msg.metadata:
                tool_calls = []
                for tc in msg.metadata["tool_calls"]:
                    tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"]),
                        },
                    })
                m: dict = {"role": "assistant", "tool_calls": tool_calls}
                if msg.content:
                    m["content"] = msg.content
                messages.append(m)
            elif msg.role == MessageRole.TOOL:
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.metadata.get("tool_call_id", ""),
                    "content": msg.content,
                })
            else:
                messages.append(msg.to_llm_dict())
        return messages
