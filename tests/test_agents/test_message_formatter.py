"""MessageFormatter — direct tests for both provider formats."""

from __future__ import annotations

from sanfuclaw.agents.message_formatter import (
    AnthropicMessageFormatter,
    OpenAIMessageFormatter,
    for_format,
)
from sanfuclaw.core.message import Message
from sanfuclaw.core.types import MessageRole


def test_for_format_selects_provider():
    assert isinstance(for_format("anthropic"), AnthropicMessageFormatter)
    assert isinstance(for_format("openai"), OpenAIMessageFormatter)
    # Unknown format defaults to anthropic shape.
    assert isinstance(for_format("unknown"), AnthropicMessageFormatter)


def test_anthropic_pairs_tool_use_and_result():
    history = [
        Message(role=MessageRole.USER, content="hi", session_id="s"),
        Message(role=MessageRole.ASSISTANT, content="working",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {"q": 1}}]}),
        Message(role=MessageRole.TOOL, content="r1",
                session_id="s", metadata={"tool_call_id": "x"}),
    ]
    out = AnthropicMessageFormatter().build(history)
    asst = next(m for m in out if m["role"] == "assistant")
    assert any(b["type"] == "text" and b["text"] == "working" for b in asst["content"])
    assert any(b["type"] == "tool_use" and b["id"] == "x" for b in asst["content"])
    user_with_result = [m for m in out if m["role"] == "user" and isinstance(m["content"], list)]
    assert user_with_result[0]["content"][0]["type"] == "tool_result"
    assert user_with_result[0]["content"][0]["content"] == "r1"


def test_anthropic_dedups_repeat_tool_call_id():
    history = [
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [
                    {"id": "x", "name": "t", "input": {}},
                    {"id": "x", "name": "t", "input": {}},
                ]}),
    ]
    out = AnthropicMessageFormatter().build(history)
    blocks = [b for m in out for b in m.get("content", []) if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert len(blocks) == 1


def test_anthropic_drops_assistant_when_only_dups_left():
    history = [
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
        Message(role=MessageRole.TOOL, content="r1", session_id="s",
                metadata={"tool_call_id": "x"}),
        # Second assistant: only a dup id, no text — must be dropped entirely.
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
    ]
    out = AnthropicMessageFormatter().build(history)
    assistants = [m for m in out if m["role"] == "assistant"]
    assert len(assistants) == 1


def test_openai_serializes_tool_args_as_json_string():
    history = [
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {"k": [1, 2]}}]}),
    ]
    out = OpenAIMessageFormatter().build(history)
    asst = next(m for m in out if m["role"] == "assistant")
    args = asst["tool_calls"][0]["function"]["arguments"]
    assert args == '{"k": [1, 2]}'


def test_openai_skips_assistant_with_only_dup_calls():
    history = [
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
        Message(role=MessageRole.ASSISTANT, content="",
                session_id="s",
                metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]}),
    ]
    out = OpenAIMessageFormatter().build(history)
    assert len([m for m in out if m["role"] == "assistant"]) == 1


def test_openai_carries_reasoning_for_plain_assistant():
    history = [
        Message(role=MessageRole.ASSISTANT, content="answer", session_id="s",
                metadata={"reasoning_content": "thought"}),
    ]
    out = OpenAIMessageFormatter().build(history)
    assert out[0]["reasoning_content"] == "thought"
    assert out[0]["content"] == "answer"


def test_openai_drops_orphan_tool_result_with_no_id():
    history = [
        Message(role=MessageRole.TOOL, content="orphan",
                session_id="s", metadata={}),
    ]
    out = OpenAIMessageFormatter().build(history)
    assert out == []
