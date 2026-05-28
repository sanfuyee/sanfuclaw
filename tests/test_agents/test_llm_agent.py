"""LLMAgent.process — multi-round tool loop, trim, dedup, exhaustion."""

from __future__ import annotations

from typing import AsyncIterator

from sanfuclaw.agents.llm_agent import LLMAgent
from sanfuclaw.agents.transports.base import StreamChunk
from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.core.types import MessageRole, StreamChunkType
from sanfuclaw.tools.registry import ToolRegistry


# ---------- helpers ---------------------------------------------------------


class FakeTransport:
    """Yields scripted lists of chunks per call. One list = one LLM turn."""

    def __init__(self, scripts: list[list[StreamChunk]], fmt: str = "anthropic"):
        self._scripts = list(scripts)
        self.message_format = fmt
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> AsyncIterator[StreamChunk]:
        self.calls.append(kwargs)
        if not self._scripts:
            raise AssertionError("FakeTransport ran out of scripted turns")
        script = self._scripts.pop(0)
        for chunk in script:
            yield chunk


class RecordingTool:
    name = "record"
    description = "Records calls."
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def __init__(self, result: str = "ok"):
        self._result = result
        self.calls: list[dict] = []

    async def execute(self, params, session):
        self.calls.append(params)
        return self._result


class FailingTool:
    name = "boom"
    description = "Always raises."
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    async def execute(self, params, session):
        raise ToolError("intentional failure")


def text(s: str) -> StreamChunk:
    return StreamChunk(type=StreamChunkType.TEXT_DELTA, data=s)


def tool_use(name: str, call_id: str, **inp) -> StreamChunk:
    return StreamChunk(
        type=StreamChunkType.TOOL_USE,
        tool_name=name,
        tool_call_id=call_id,
        tool_input=dict(inp),
    )


def usage(in_tok: int = 0, out_tok: int = 0, cached: int = 0, reasoning: int = 0) -> StreamChunk:
    return StreamChunk(
        type=StreamChunkType.USAGE,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached,
        reasoning_tokens=reasoning,
    )


def stop() -> StreamChunk:
    return StreamChunk(type=StreamChunkType.STOP)


def reasoning(s: str) -> StreamChunk:
    return StreamChunk(type=StreamChunkType.REASONING_DELTA, data=s)


def make_envelope(content: str = "hi", session_id: str = "test-sess") -> Envelope:
    msg = Message(
        role=MessageRole.USER,
        content=content,
        channel_id="cli",
        session_id=session_id,
        sender_id="u1",
    )
    return Envelope(message=msg, source_channel="cli")


def make_session(sid: str = "test-sess") -> Session:
    return Session(id=sid, channel_id="cli", sender_id="u1")


async def collect(it: AsyncIterator[str]) -> str:
    out = []
    async for x in it:
        out.append(x)
    return "".join(out)


# ---------- single-turn -----------------------------------------------------


async def test_single_turn_text_passthrough():
    transport = FakeTransport([[text("hello "), text("world"), usage(10, 5), stop()]])
    agent = LLMAgent(name="t", transport=transport)
    session = make_session()
    out = await collect(agent.process(make_envelope("hi"), session))
    assert out == "hello world"
    # User msg + assistant final = 2 history rows
    assert len(session.history) == 2
    assert session.history[-1].role == MessageRole.ASSISTANT
    assert session.history[-1].content == "hello world"


async def test_usage_recorded_in_trace():
    transport = FakeTransport([[text("ok"), usage(100, 20, cached=80, reasoning=5), stop()]])
    agent = LLMAgent(name="t", transport=transport)
    await collect(agent.process(make_envelope(), make_session()))
    assert "100 in / 20 out" in agent.last_trace
    assert "80 cached" in agent.last_trace
    assert "5 reasoning" in agent.last_trace


async def test_empty_llm_turn_yields_visible_notice():
    transport = FakeTransport([[stop()]])
    agent = LLMAgent(name="t", transport=transport)
    session = make_session()
    out = await collect(agent.process(make_envelope("hi"), session))
    assert "no visible response" in out
    assert session.history[-1].role == MessageRole.ASSISTANT
    assert session.history[-1].content == out
    assert session.history[-1].metadata["empty_response"] is True


# ---------- tool loop -------------------------------------------------------


async def test_multi_round_tool_loop():
    transport = FakeTransport([
        # Round 0: ask for a tool
        [text("looking… "), tool_use("record", "tc-1", q="x"), usage(50, 10), stop()],
        # Round 1: final answer
        [text("done"), usage(60, 15), stop()],
    ])
    tools = ToolRegistry()
    rec = RecordingTool(result="tool-said-hi")
    tools.register(rec)
    agent = LLMAgent(name="t", transport=transport, tool_registry=tools, max_tool_rounds=5)
    session = make_session()
    out = await collect(agent.process(make_envelope(), session))
    assert out == "looking… done"
    assert rec.calls == [{"q": "x"}]
    # user, assistant(tool_use), tool, assistant(final) = 4 rows
    roles = [m.role for m in session.history]
    assert roles == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert session.history[2].metadata["tool_call_id"] == "tc-1"
    assert session.history[2].content == "tool-said-hi"


async def test_tool_error_becomes_user_visible_message_and_continues():
    transport = FakeTransport([
        [tool_use("boom", "tc-1"), usage(10, 5), stop()],
        [text("recovered"), usage(20, 5), stop()],
    ])
    tools = ToolRegistry()
    tools.register(FailingTool())
    agent = LLMAgent(name="t", transport=transport, tool_registry=tools)
    session = make_session()
    out = await collect(agent.process(make_envelope(), session))
    assert out == "recovered"
    tool_msg = session.history[2]
    assert tool_msg.role == MessageRole.TOOL
    assert "intentional failure" in tool_msg.content
    assert tool_msg.content.startswith("Error:")


async def test_max_tool_rounds_exhaustion():
    # max_tool_rounds=2 → agent allows up to 2 tool rounds, then yields the
    # exhaustion notice on the 3rd attempt (range(max+1) = 3 iterations).
    scripts = [
        [tool_use("record", f"tc-{i}", n=i), usage(5, 2), stop()]
        for i in range(3)
    ]
    transport = FakeTransport(scripts)
    tools = ToolRegistry()
    tools.register(RecordingTool())
    agent = LLMAgent(name="t", transport=transport, tool_registry=tools, max_tool_rounds=2)
    session = make_session()
    out = await collect(agent.process(make_envelope(), session))
    assert "Reached max tool rounds (2)" in out
    # Final assistant message persisted with the notice baked in.
    last = session.history[-1]
    assert last.role == MessageRole.ASSISTANT
    assert "Reached max tool rounds" in last.content


# ---------- reasoning -------------------------------------------------------


async def test_reasoning_content_persisted_not_yielded():
    transport = FakeTransport([
        [reasoning("thinking…"), text("answer"), usage(10, 5), stop()],
    ])
    agent = LLMAgent(name="t", transport=transport)
    session = make_session()
    out = await collect(agent.process(make_envelope(), session))
    # Reasoning is NOT yielded to the channel.
    assert out == "answer"
    last = session.history[-1]
    assert last.metadata.get("reasoning_content") == "thinking…"


# ---------- history budget --------------------------------------------------


async def test_history_trim_drops_oldest_to_fit_budget():
    # context_window=200, max_tokens=50, margin=10. _estimate_tokens is len//3.
    # Give a system prompt of ~3 tokens, then craft a session with several
    # large messages.
    transport = FakeTransport([[text("ok"), usage(0, 0), stop()]])
    agent = LLMAgent(
        name="t",
        transport=transport,
        system_prompt="hi",
        max_tokens=50,
        context_window=200,
        input_safety_margin=10,
    )
    session = make_session()
    # Each message is 90 chars → ~30 estimated tokens. Budget after fixed
    # overhead: 200 - (1 + 50 + 10) ≈ 139 tokens; ~4 messages fit.
    for i in range(10):
        session.history.append(Message(
            role=MessageRole.USER,
            content="x" * 90,
            session_id=session.id,
        ))
    fitted = agent._fit_history_to_budget(session.history)
    assert len(fitted) < 10
    # Oldest dropped, newest kept.
    assert fitted[-1] is session.history[-1]


async def test_history_trim_drops_orphan_tool_result_at_head():
    transport = FakeTransport([[text("ok"), usage(0, 0), stop()]])
    agent = LLMAgent(
        name="t",
        transport=transport,
        max_tokens=10,
        context_window=80,
        input_safety_margin=5,
    )
    history = [
        Message(role=MessageRole.USER, content="u1", session_id="s"),
        Message(role=MessageRole.ASSISTANT, content="a1", session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
        Message(role=MessageRole.TOOL, content="t-result", session_id="s",
                metadata={"tool_call_id": "x"}),
        Message(role=MessageRole.USER, content="u2 long " * 5, session_id="s"),
    ]
    fitted = agent._fit_history_to_budget(history)
    # If trim has to drop the assistant tool-call message, it must also drop
    # the trailing TOOL row so we don't ship an orphan tool_result.
    if len(fitted) < len(history):
        for msg in fitted:
            if msg.role == MessageRole.TOOL:
                # Find the matching assistant tool_call earlier in fitted
                ids_called = set()
                for m in fitted:
                    if m.role == MessageRole.ASSISTANT and "tool_calls" in m.metadata:
                        for tc in m.metadata["tool_calls"]:
                            ids_called.add(tc["id"])
                assert msg.metadata.get("tool_call_id") in ids_called


# ---------- message builders & dedup ----------------------------------------


def test_anthropic_builder_dedups_duplicate_tool_call_ids():
    transport = FakeTransport([], fmt="anthropic")
    agent = LLMAgent(name="t", transport=transport)
    history = [
        Message(role=MessageRole.USER, content="hi", session_id="s"),
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            session_id="s",
            metadata={"tool_calls": [
                {"id": "dup", "name": "x", "input": {}},
                {"id": "dup", "name": "x", "input": {}},
                {"id": "fresh", "name": "x", "input": {}},
            ]},
        ),
    ]
    out = agent._build_anthropic_tool_messages(history)
    assistant_msg = next(m for m in out if m["role"] == "assistant")
    tool_use_blocks = [b for b in assistant_msg["content"] if b.get("type") == "tool_use"]
    ids = [b["id"] for b in tool_use_blocks]
    assert ids == ["dup", "fresh"]


def test_anthropic_builder_drops_duplicate_tool_results():
    transport = FakeTransport([], fmt="anthropic")
    agent = LLMAgent(name="t", transport=transport)
    history = [
        Message(role=MessageRole.ASSISTANT, content="", session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
        Message(role=MessageRole.TOOL, content="r1", session_id="s",
                metadata={"tool_call_id": "x"}),
        Message(role=MessageRole.TOOL, content="r2-dup", session_id="s",
                metadata={"tool_call_id": "x"}),
    ]
    out = agent._build_anthropic_tool_messages(history)
    tool_results = [
        b for m in out if m["role"] == "user"
        for b in m["content"] if b.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["content"] == "r1"


def test_openai_builder_carries_reasoning_and_dedups_tool_calls():
    transport = FakeTransport([], fmt="openai")
    agent = LLMAgent(name="t", transport=transport)
    history = [
        Message(role=MessageRole.USER, content="hi", session_id="s"),
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            session_id="s",
            metadata={
                "tool_calls": [
                    {"id": "a", "name": "x", "input": {"k": 1}},
                    {"id": "a", "name": "x", "input": {"k": 1}},  # dup
                ],
                "reasoning_content": "deep thought",
            },
        ),
    ]
    out = agent._build_openai_tool_messages(history)
    asst = next(m for m in out if m["role"] == "assistant")
    assert len(asst["tool_calls"]) == 1
    assert asst["reasoning_content"] == "deep thought"


def test_openai_builder_skips_assistant_with_only_dup_tool_calls():
    transport = FakeTransport([], fmt="openai")
    agent = LLMAgent(name="t", transport=transport)
    history = [
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
        Message(role=MessageRole.TOOL, content="r", session_id="s",
                metadata={"tool_call_id": "x"}),
        # second assistant turn re-asserts the same id with no text — must be skipped
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
    ]
    out = agent._build_openai_tool_messages(history)
    # Only the first assistant + tool_result; the duplicate was skipped.
    assert sum(1 for m in out if m["role"] == "assistant") == 1
