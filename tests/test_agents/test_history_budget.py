"""HistoryBudget — direct tests for the trim logic."""

from __future__ import annotations

from sanfuclaw.agents.history_budget import (
    HistoryBudget,
    estimate_message_tokens,
    estimate_tokens,
)
from sanfuclaw.core.message import Message
from sanfuclaw.core.types import MessageRole


def test_estimate_tokens_min_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("x" * 30) == 10


def test_estimate_tokens_counts_cjk_conservatively():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("abc你好") == 3


def test_no_context_window_means_no_trim():
    budget = HistoryBudget.from_components(
        context_window=None, max_tokens=100,
        input_safety_margin=0, system_prompt="x", tool_schemas=None,
    )
    history = [Message(role=MessageRole.USER, content="x" * 100, session_id="s")
               for _ in range(5)]
    assert budget.fit(history) is history


def test_budget_exhausted_returns_empty():
    budget = HistoryBudget.from_components(
        context_window=10, max_tokens=100,
        input_safety_margin=10, system_prompt="x", tool_schemas=None,
    )
    history = [Message(role=MessageRole.USER, content="hello", session_id="s")]
    assert budget.fit(history) == []


def test_trim_keeps_newest():
    budget = HistoryBudget.from_components(
        context_window=200, max_tokens=50, input_safety_margin=10,
        system_prompt="hi", tool_schemas=None,
    )
    history = [
        Message(role=MessageRole.USER, content=f"msg{i} " + "x" * 90, session_id="s")
        for i in range(8)
    ]
    fitted = budget.fit(history)
    assert len(fitted) < len(history)
    assert fitted[-1] is history[-1]


def test_trim_drops_orphan_tool_result_at_head():
    budget = HistoryBudget.from_components(
        context_window=120, max_tokens=10, input_safety_margin=5,
        system_prompt="x", tool_schemas=None,
    )
    # Force trim by including bulk in the assistant turn → first prefix dropped
    history = [
        Message(role=MessageRole.USER, content="u" * 200, session_id="s"),
        Message(
            role=MessageRole.ASSISTANT,
            content="",
            session_id="s",
            metadata={"tool_calls": [{"id": "x", "name": "t", "input": {}}]},
        ),
        Message(role=MessageRole.TOOL, content="result-x",
                session_id="s", metadata={"tool_call_id": "x"}),
        Message(role=MessageRole.USER, content="recent", session_id="s"),
    ]
    fitted = budget.fit(history)
    # If the assistant tool-call message was dropped, no leading TOOL row
    # should remain — orphan tool_results are 400s.
    if fitted and fitted[0].role == MessageRole.TOOL:
        raise AssertionError("orphan tool_result left at head")


def test_estimate_message_includes_tool_calls_metadata():
    plain = Message(role=MessageRole.ASSISTANT, content="hi", session_id="s")
    with_tools = Message(
        role=MessageRole.ASSISTANT, content="hi", session_id="s",
        metadata={"tool_calls": [{"id": "x", "name": "n", "input": {"k": "v" * 100}}]},
    )
    assert estimate_message_tokens(with_tools) > estimate_message_tokens(plain)
