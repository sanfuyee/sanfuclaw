"""LLM Agent — processes messages using an LLM transport with optional tool use."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.core.types import MessageRole, StreamChunkType

from .transports.base import LLMTransport, StreamChunk
from sanfuclaw.skills.registry import SkillRegistry
from sanfuclaw.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class LLMAgent:
    """An agent that uses an LLM to process messages, with tool support."""

    def __init__(
        self,
        name: str,
        transport: LLMTransport,
        tool_registry: ToolRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        system_prompt: str = "You are a helpful personal AI assistant called Sanfuclaw.",
        model: str | None = None,
        max_tokens: int = 4096,
        context_window: int | None = None,
        max_tool_rounds: int = 10,
        temperature: float = 0.7,
        max_history: int = 20,
        input_safety_margin: int = 1000,
    ):
        self.name = name
        self._transport = transport
        self._tools = tool_registry or ToolRegistry()
        self._skills = skill_registry
        if skill_registry and len(skill_registry) > 0:
            system_prompt = system_prompt + "\n" + skill_registry.system_prompt_block()
        self._system_prompt = system_prompt
        self._model = model
        self._max_tokens = max_tokens
        self._context_window = context_window
        self._max_tool_rounds = max_tool_rounds
        self._temperature = temperature
        self._max_history = max_history
        self._input_safety_margin = input_safety_margin
        # Per-turn diagnostic info (LLM steps, token usage). Populated each
        # process() call. The router decides whether to surface this — only
        # interactive channels (CLI) want it; user-facing channels skip it.
        self.last_trace: str = ""

    def _build_messages(self, session: Session) -> list[dict]:
        """Build messages in the correct format for the current transport."""
        recent = session.history[-self._max_history:]
        recent = self._fit_history_to_budget(recent)

        fmt = getattr(self._transport, "message_format", "anthropic")
        if fmt == "openai":
            return self._build_openai_tool_messages(recent)
        else:
            return self._build_anthropic_tool_messages(recent)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # 3 chars/token: overestimates pure English (~4 chars/tok), about
        # right for mixed CJK (~1.5 chars/tok). Overestimating is safe —
        # it just trims slightly more aggressively.
        return max(1, len(text) // 3)

    def _estimate_message_tokens(self, msg: Message) -> int:
        total = self._estimate_tokens(msg.content or "")
        if "tool_calls" in msg.metadata:
            total += self._estimate_tokens(json.dumps(msg.metadata["tool_calls"]))
        return total + 4  # small per-message structural overhead

    def _fit_history_to_budget(self, history: list[Message]) -> list[Message]:
        """Drop oldest messages until estimated tokens fit the input budget.

        Preserves tool_use/tool_result pairs: an orphaned tool_result is
        rejected by both Anthropic and OpenAI, so if an assistant message
        with tool_calls is dropped, its trailing TOOL messages go too.
        """
        if not self._context_window:
            return history

        fixed = self._estimate_tokens(self._system_prompt)
        try:
            schemas = self._tools.to_llm_schemas() or []
            if schemas:
                fixed += self._estimate_tokens(json.dumps(schemas))
        except Exception:
            logger.debug("Could not estimate tool schema tokens", exc_info=True)
        fixed += self._max_tokens + self._input_safety_margin

        budget = self._context_window - fixed
        if budget <= 0:
            logger.warning(
                "Input budget exhausted by fixed overhead "
                "(context_window=%d, max_tokens=%d, margin=%d). Sending empty history.",
                self._context_window, self._max_tokens, self._input_safety_margin,
            )
            return []

        sizes = [self._estimate_message_tokens(m) for m in history]
        total = sum(sizes)
        if total <= budget:
            return history

        i = 0
        n = len(history)
        while total > budget and i < n:
            total -= sizes[i]
            i += 1
            # A leading TOOL message would be an orphaned tool_result — drop it.
            while i < n and history[i].role == MessageRole.TOOL:
                total -= sizes[i]
                i += 1

        logger.info(
            "Trimmed %d/%d history messages to fit input budget (%d tokens est.)",
            i, n, budget,
        )
        return history[i:]

    async def _stream_llm(
        self, messages: list[dict], tools: list[dict] | None,
    ) -> AsyncIterator[StreamChunk]:
        """Thin wrapper around transport.complete."""
        async for chunk in self._transport.complete(
            messages=messages,
            tools=tools,
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=self._system_prompt,
        ):
            yield chunk

    async def process(self, envelope: Envelope, session: Session) -> AsyncIterator[str]:
        """Process a message and stream back response chunks."""
        session.add_message(envelope.message)
        tools = self._tools.to_llm_schemas() or None

        logger.info(
            "Turn start: session=%s channel=%s history=%d msgs tools=%d",
            session.id[:8], envelope.source_channel, len(session.history),
            len(tools) if tools else 0,
        )

        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_reasoning_tokens = 0
        trace: list[str] = []  # collect step info for final summary
        tool_round_count = 0   # actual rounds that issued tool calls
        max_tool_rounds = self._max_tool_rounds
        exhausted = False

        for round_num in range(max_tool_rounds + 1):
            messages = self._build_messages(session)
            step_label = "LLM" if round_num == 0 else "LLM (follow-up)"

            full_response = ""
            tool_calls: list[StreamChunk] = []
            step_input = 0
            step_output = 0
            step_cached = 0
            step_reasoning = 0

            async for chunk in self._stream_llm(messages, tools):
                if chunk.type == StreamChunkType.TEXT_DELTA:
                    full_response += chunk.data
                    yield chunk.data
                elif chunk.type == StreamChunkType.TOOL_USE and chunk.tool_input is not None:
                    tool_calls.append(chunk)
                elif chunk.type == StreamChunkType.USAGE:
                    step_input = chunk.input_tokens
                    step_output = chunk.output_tokens
                    step_cached = chunk.cached_tokens
                    step_reasoning = chunk.reasoning_tokens
                    total_input_tokens += step_input
                    total_output_tokens += step_output
                    total_cached_tokens += step_cached
                    total_reasoning_tokens += step_reasoning

            notes = []
            if step_cached:
                notes.append(f"{step_cached} cached")
            if step_reasoning:
                notes.append(f"{step_reasoning} reasoning")
            note_str = f" ({', '.join(notes)})" if notes else ""
            trace.append(f"{step_label}: {step_input} in / {step_output} out{note_str}")

            if not tool_calls:
                # Save only the LLM's actual response, not the trace
                session.add_message(Message(
                    role=MessageRole.ASSISTANT,
                    content=full_response,
                    session_id=session.id,
                ))
                break

            # Save assistant message with tool calls
            session.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=full_response,
                session_id=session.id,
                metadata={"tool_calls": [
                    {"name": tc.tool_name, "id": tc.tool_call_id, "input": tc.tool_input}
                    for tc in tool_calls
                ]},
            ))

            # Execute tools
            tool_round_count += 1
            for tc in tool_calls:
                input_summary = self._summarize_tool_input(tc.tool_name, tc.tool_input)
                trace.append(f"Tool `{tc.tool_name}`: {input_summary}")
                logger.debug("Tool call: %s(%s)", tc.tool_name, input_summary)

                try:
                    result = await self._tools.execute(tc.tool_name, tc.tool_input, session)
                    result_str = result if isinstance(result, str) else json.dumps(result)
                except Exception as e:
                    result_str = f"Error: {e}"
                    logger.exception("Tool %s raised during execute()", tc.tool_name)

                session.add_message(Message(
                    role=MessageRole.TOOL,
                    content=result_str,
                    session_id=session.id,
                    metadata={"tool_call_id": tc.tool_call_id},
                ))
        else:
            # for-loop exhausted without break — tool rounds used up
            exhausted = True
            notice = f"\n\n[Reached max tool rounds ({max_tool_rounds}). Send another message to continue.]"
            yield notice
            session.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=full_response + notice,
                session_id=session.id,
            ))

        # Stash the per-turn trace for the router to deliver out-of-band.
        steps = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(trace))
        history_count = len(session.history)
        totals = []
        if total_cached_tokens:
            totals.append(f"{total_cached_tokens} cached")
        if total_reasoning_tokens:
            totals.append(f"{total_reasoning_tokens} reasoning")
        totals_str = f" ({', '.join(totals)})" if totals else ""
        self.last_trace = (
            f"{steps}\n"
            f"  History: {history_count} msgs | "
            f"Total: {total_input_tokens} in / {total_output_tokens} out{totals_str}"
        )

        logger.info(
            "Turn done: session=%s tool_rounds=%d tokens=%d/%d%s%s",
            session.id[:8], tool_round_count,
            total_input_tokens, total_output_tokens,
            f" cached={total_cached_tokens}" if total_cached_tokens else "",
            " (max rounds reached)" if exhausted else "",
        )

    def _build_anthropic_tool_messages(self, history: list[Message]) -> list[dict]:
        """Build messages with tool use/results in Anthropic format."""
        messages = []
        for msg in history:
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

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
        """Create a brief human-readable summary of a tool invocation."""
        if tool_name == "shell":
            return f"`{tool_input.get('command', '')}`"
        elif tool_name == "web_fetch":
            return tool_input.get("url", "")
        else:
            args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
            return args[:150]

    def _build_openai_tool_messages(self, history: list[Message]) -> list[dict]:
        """Build messages with tool use/results in OpenAI format."""
        messages = []
        for msg in history:
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
