"""LLM Agent — processes messages using an LLM transport with optional tool use."""

from __future__ import annotations

import json
import logging
import os
import platform
from datetime import date
from typing import AsyncIterator

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.core.types import MessageRole, StreamChunkType

from .history_budget import HistoryBudget
from .message_formatter import MessageFormatter, for_format
from .transports.base import LLMTransport, StreamChunk
from sanfuclaw.skills.registry import SkillRegistry
from sanfuclaw.tools.registry import ToolRegistry
from sanfuclaw.tools.task import format_plan

logger = logging.getLogger(__name__)


def _build_env_block() -> str:
    """Snapshot of the process's runtime environment, injected into the
    system prompt so the agent knows where it lives. Captured once at
    agent construction — pwd/platform/shell don't change mid-process."""
    return (
        "Process environment:\n"
        f"- Working directory: {os.getcwd()}\n"
        f"- User: {os.environ.get('USER', '(unknown)')}\n"
        f"- Platform: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- Shell: {os.environ.get('SHELL', '(unknown)')}"
    )


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
        input_safety_margin: int = 1000,
    ):
        self.name = name
        self._transport = transport
        self._tools = tool_registry or ToolRegistry()
        self._skills = skill_registry
        if skill_registry and len(skill_registry) > 0:
            system_prompt = system_prompt + "\n" + skill_registry.system_prompt_block()
        self._system_prompt = system_prompt
        self._env_block = _build_env_block()
        self._model = model
        self._max_tokens = max_tokens
        self._context_window = context_window
        self._max_tool_rounds = max_tool_rounds
        self._temperature = temperature
        self._input_safety_margin = input_safety_margin

        # Provider-specific message formatter selected once at construction —
        # the transport never changes shape mid-process.
        fmt = getattr(transport, "message_format", "anthropic")
        self._formatter: MessageFormatter = for_format(fmt)

        # Per-turn diagnostic info (LLM steps, token usage). Populated each
        # process() call. The router decides whether to surface this — only
        # interactive channels (CLI) want it; user-facing channels skip it.
        # NOTE: this is a single-slot field; concurrent process() calls on the
        # same agent would race here. The router serializes per-envelope today.
        self.last_trace: str = ""

    def _build_messages(self, session: Session) -> list[dict]:
        """Build messages in the format expected by the current transport."""
        budget = self._budget_for_current_call()
        recent = budget.fit(session.history)
        return self._formatter.build(recent)

    def _budget_for_current_call(self) -> HistoryBudget:
        """Compute the trim budget. Tool schemas can change at runtime (MCP
        servers reconnect, lazy-loaded skill registers a new tool), so we
        recompute the fixed overhead on each call."""
        try:
            schemas = self._tools.to_llm_schemas() or []
        except Exception:
            logger.debug("Could not enumerate tool schemas", exc_info=True)
            schemas = []
        return HistoryBudget.from_components(
            context_window=self._context_window,
            max_tokens=self._max_tokens,
            input_safety_margin=self._input_safety_margin,
            system_prompt=self._system_prompt,
            tool_schemas=schemas,
        )

    # --- back-compat shims for tests / external callers --------------------

    def _fit_history_to_budget(self, history: list[Message]) -> list[Message]:
        return self._budget_for_current_call().fit(history)

    def _build_anthropic_tool_messages(self, history: list[Message]) -> list[dict]:
        from .message_formatter import AnthropicMessageFormatter
        return AnthropicMessageFormatter().build(history)

    def _build_openai_tool_messages(self, history: list[Message]) -> list[dict]:
        from .message_formatter import OpenAIMessageFormatter
        return OpenAIMessageFormatter().build(history)

    # ------------------------------------------------------------------------

    def _system_prompt_for_now(self) -> str:
        """System prompt with today's date and process environment prepended.

        Without these the model has no idea what 'today' is or where shell
        commands run — it falls back to training-cutoff facts and guesses
        paths. The date piece is rebuilt per turn so a long-running process
        picks up the new day after midnight; the env block is captured
        once at __init__ since pwd/platform/shell don't change mid-run.
        """
        today = date.today()
        return (
            f"Today's date is {today.isoformat()} ({today.strftime('%A')}).\n\n"
            f"{self._env_block}\n\n"
            f"{self._system_prompt}"
        )

    async def _stream_llm(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        session: Session | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Thin wrapper around transport.complete.

        If the session has a non-empty plan in metadata, append it to the
        system prompt so the model sees current progress every turn
        without scrolling history.
        """
        system = self._system_prompt_for_now()
        plan = session.metadata.get("plan") if session else None
        if plan:
            system = f"{system}\n\nCurrent plan:\n{format_plan(plan)}"

        async for chunk in self._transport.complete(
            messages=messages,
            tools=tools,
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=system,
        ):
            yield chunk

    async def process(self, envelope: Envelope, session: Session) -> AsyncIterator[str]:
        """Process a message and stream back response chunks."""
        session.add_message(envelope.message)
        tools = self._tools.to_llm_schemas() or None

        # Short turn id — surfaces in every per-turn log line so JSON-mode log
        # consumers can correlate "Turn start", tool calls, and "Turn done" for
        # one envelope without parsing message text.
        turn_id = envelope.message.id[:8]
        log_ctx = {"session_id": session.id[:8], "turn_id": turn_id}

        logger.info(
            "Turn start: session=%s channel=%s history=%d msgs tools=%d",
            session.id[:8], envelope.source_channel, len(session.history),
            len(tools) if tools else 0,
            extra={**log_ctx, "channel": envelope.source_channel,
                   "history_len": len(session.history),
                   "tool_count": len(tools) if tools else 0},
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
            full_reasoning = ""  # accumulated thinking-mode content (DeepSeek-R1 etc.)
            tool_calls: list[StreamChunk] = []
            step_input = 0
            step_output = 0
            step_cached = 0
            step_reasoning = 0

            async for chunk in self._stream_llm(messages, tools, session):
                if chunk.type == StreamChunkType.TEXT_DELTA:
                    full_response += chunk.data
                    yield chunk.data
                elif chunk.type == StreamChunkType.REASONING_DELTA:
                    # Accumulate but don't yield — reasoning is internal scratch
                    # pad, not user-facing. We persist it so DeepSeek-style
                    # thinking models accept it back on the next turn.
                    full_reasoning += chunk.data
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

            # Build metadata for this assistant turn. Always include
            # reasoning_content if the model emitted any — DeepSeek's thinking
            # mode (and similar) require the original reasoning to come back
            # in subsequent turns or the API rejects the history with 400.
            assistant_metadata: dict = {}
            if full_reasoning:
                assistant_metadata["reasoning_content"] = full_reasoning

            if not tool_calls:
                # Save only the LLM's actual response, not the trace
                session.add_message(Message(
                    role=MessageRole.ASSISTANT,
                    content=full_response,
                    session_id=session.id,
                    metadata=assistant_metadata,
                ))
                break

            # Save assistant message with tool calls
            assistant_metadata["tool_calls"] = [
                {"name": tc.tool_name, "id": tc.tool_call_id, "input": tc.tool_input}
                for tc in tool_calls
            ]
            session.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=full_response,
                session_id=session.id,
                metadata=assistant_metadata,
            ))

            # Execute tools
            tool_round_count += 1
            for tc in tool_calls:
                input_summary = self._summarize_tool_input(tc.tool_name, tc.tool_input)
                trace.append(f"Tool `{tc.tool_name}`: {input_summary}")
                tool_ctx = {**log_ctx, "tool": tc.tool_name, "round": round_num}
                logger.debug(
                    "Tool call: %s(%s)", tc.tool_name, input_summary, extra=tool_ctx,
                )

                try:
                    result = await self._tools.execute(tc.tool_name, tc.tool_input, session)
                    result_str = result if isinstance(result, str) else json.dumps(result)
                except ToolError as e:
                    # Controlled failure — the LLM is meant to read the message
                    # and recover (try a different source / give up gracefully).
                    # No traceback: it's not a bug, just a tool-level outcome.
                    result_str = f"Error: {e}"
                    logger.warning("Tool %s failed: %s", tc.tool_name, e, extra=tool_ctx)
                except Exception as e:
                    result_str = f"Error: {e}"
                    logger.exception("Tool %s raised unexpectedly", tc.tool_name, extra=tool_ctx)

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
            exhaust_metadata: dict = {}
            if full_reasoning:
                exhaust_metadata["reasoning_content"] = full_reasoning
            session.add_message(Message(
                role=MessageRole.ASSISTANT,
                content=full_response + notice,
                session_id=session.id,
                metadata=exhaust_metadata,
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
            extra={
                **log_ctx,
                "tool_rounds": tool_round_count,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cached_tokens": total_cached_tokens,
                "reasoning_tokens": total_reasoning_tokens,
                "exhausted": exhausted,
            },
        )

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
        """Create a brief human-readable summary of a tool invocation."""
        if tool_name == "shell":
            return f"`{tool_input.get('command', '')}`"
        elif tool_name == "web_fetch":
            return tool_input.get("url", "")
        elif tool_name == "web_search":
            return tool_input.get("query", "")
        elif tool_name == "weather":
            return tool_input.get("location", "")
        elif tool_name == "task_write":
            tasks = tool_input.get("tasks") or []
            return f"{len(tasks)} tasks"
        else:
            args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
            return args[:150]
