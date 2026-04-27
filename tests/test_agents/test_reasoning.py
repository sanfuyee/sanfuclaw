"""Reasoning-mode (DeepSeek-R1, Kimi reasoning, QwQ) end-to-end tests.

The bug these tests pin: thinking-mode providers return a `reasoning_content`
field alongside `content` on each turn, and require the original
`reasoning_content` to be replayed verbatim in the assistant message on
every subsequent turn — omit it and the API returns 400 with
"The `reasoning_content` in the thinking mode must be passed back to the API."

We need:
  1. The transport to surface reasoning text as REASONING_DELTA chunks.
  2. The agent to accumulate them and stash on the assistant `Message.metadata`.
  3. The OpenAI-compat message builder to re-emit the field on subsequent turns.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sanfuclaw.agents.llm_agent import LLMAgent
from sanfuclaw.agents.transports.openai_compat import OpenAICompatTransport
from sanfuclaw.core.message import Message
from sanfuclaw.core.types import MessageRole, StreamChunkType


# ---------------------------------------------------------------------------
# Transport: REASONING_DELTA emission
# ---------------------------------------------------------------------------

def _make_chunk(*, content: str = "", reasoning: str = "", finish: str | None = None):
    """Build a SimpleNamespace shaped like an OpenAI streaming chunk choice."""
    delta = SimpleNamespace(content=content or None, reasoning_content=reasoning or None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=None)


class _FakeAsyncStream:
    """Async iterator that yields a fixed list of pre-built chunks."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


class _FakeChatCompletions:
    def __init__(self, stream):
        self._stream = stream

    async def create(self, **kwargs):
        return self._stream


class _FakeAsyncClient:
    def __init__(self, stream):
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(stream))


@pytest.mark.asyncio
async def test_transport_emits_reasoning_delta(monkeypatch):
    """Fake an OpenAI stream with reasoning_content fields and verify the
    transport surfaces them as REASONING_DELTA chunks (not silently dropped)."""
    chunks = [
        _make_chunk(reasoning="Let me think... "),
        _make_chunk(reasoning="the user wants the time."),
        _make_chunk(content="It's 10am."),
        _make_chunk(finish="stop"),
    ]
    fake = _FakeAsyncStream(chunks)

    t = OpenAICompatTransport(api_key="x", base_url="x", default_model="m")
    monkeypatch.setattr(t, "_client", _FakeAsyncClient(fake))

    out = []
    async for c in t.complete(messages=[{"role": "user", "content": "hi"}]):
        out.append(c)

    reasoning_chunks = [c for c in out if c.type == StreamChunkType.REASONING_DELTA]
    text_chunks = [c for c in out if c.type == StreamChunkType.TEXT_DELTA]

    assert len(reasoning_chunks) == 2
    assert "".join(c.data for c in reasoning_chunks) == "Let me think... the user wants the time."
    assert len(text_chunks) == 1
    assert text_chunks[0].data == "It's 10am."


# ---------------------------------------------------------------------------
# Agent: roundtrip — reasoning_content survives in metadata and is replayed
# ---------------------------------------------------------------------------

def _agent() -> LLMAgent:
    """Bare-bones agent for testing the message-construction helpers.
    Uses a stub transport to satisfy the constructor — `complete()` is
    never called in these tests."""

    class _StubTransport:
        message_format = "openai"

        async def complete(self, **kwargs):  # pragma: no cover — never invoked
            yield  # type: ignore[misc]

    return LLMAgent(name="t", transport=_StubTransport())


def test_openai_message_builder_replays_reasoning_content():
    """An assistant message with reasoning_content metadata must surface
    the field in the rebuilt API request — that's the whole bug fix."""
    history = [
        Message(role=MessageRole.USER, content="hi"),
        Message(
            role=MessageRole.ASSISTANT,
            content="hello!",
            metadata={"reasoning_content": "user said hi, answer briefly"},
        ),
        Message(role=MessageRole.USER, content="how are you?"),
    ]
    out = _agent()._build_openai_tool_messages(history)

    assert out[0] == {"role": "user", "content": "hi"}
    assert out[1]["role"] == "assistant"
    assert out[1]["content"] == "hello!"
    assert out[1]["reasoning_content"] == "user said hi, answer briefly"
    assert out[2] == {"role": "user", "content": "how are you?"}


def test_openai_message_builder_skips_field_when_absent():
    """Non-reasoning models never emit reasoning_content; the rebuilt
    assistant dict must not carry an empty `reasoning_content` key
    (some providers may reject unknown empty fields)."""
    history = [
        Message(role=MessageRole.USER, content="hi"),
        Message(role=MessageRole.ASSISTANT, content="hello!"),
    ]
    out = _agent()._build_openai_tool_messages(history)
    assert "reasoning_content" not in out[1]


def test_openai_message_builder_carries_reasoning_alongside_tool_calls():
    """Thinking-mode model performs a tool call: the assistant message
    has BOTH tool_calls AND reasoning_content, and both must replay."""
    history = [
        Message(role=MessageRole.USER, content="what time is it?"),
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            metadata={
                "reasoning_content": "I need to call the time tool",
                "tool_calls": [{"id": "call_1", "name": "time", "input": {}}],
            },
        ),
        Message(
            role=MessageRole.TOOL,
            content="10:00",
            metadata={"tool_call_id": "call_1"},
        ),
    ]
    out = _agent()._build_openai_tool_messages(history)

    assert out[1]["role"] == "assistant"
    assert out[1]["tool_calls"][0]["function"]["name"] == "time"
    assert out[1]["reasoning_content"] == "I need to call the time tool"
    assert out[2]["role"] == "tool"
    assert out[2]["tool_call_id"] == "call_1"
