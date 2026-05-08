"""Router — happy path persists, failure path rolls back, trace gating."""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.core.types import MessageRole
from sanfuclaw.gateway.router import Router
from sanfuclaw.gateway.session_manager import SessionManager


# ---------- in-memory store -------------------------------------------------


class FakeStore:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.messages: list[Message] = []
        self.save_session_calls = 0

    async def save_session(self, session: Session) -> None:
        self.sessions[session.id] = session
        self.save_session_calls += 1

    async def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def find_session(self, channel_id: str, sender_id: str) -> Session | None:
        for s in self.sessions.values():
            if s.channel_id == channel_id and s.sender_id == sender_id:
                return s
        return None

    async def save_message(self, message: Message) -> None:
        self.messages.append(message)

    async def get_history(self, session_id, limit=None, before=None):
        return [m for m in self.messages if m.session_id == session_id]

    async def list_sessions(self, channel_id=None, limit=20):
        return []


# ---------- fakes -----------------------------------------------------------


class CapturingChannel:
    name = "cap"

    def __init__(self):
        self.streamed: list[str] = []
        self.done: list[str] = []
        self.trace: list[str] = []
        self.typing = 0

    async def send(
        self,
        sid: str,
        content: str,
        *,
        streaming: bool = False,
        done: bool = False,
    ) -> None:
        if done:
            self.done.append(content)
        elif streaming:
            self.streamed.append(content)

    async def send_typing(self, sid: str) -> None:
        self.typing += 1


class TraceChannel(CapturingChannel):
    name = "trace-ch"

    async def send_trace(self, sid: str, content: str) -> None:
        self.trace.append(content)


class ScriptedAgent:
    """Yields a scripted set of chunks; appends predetermined messages to history."""

    name = "scripted"

    def __init__(self, chunks: list[str], history_additions: list[Message] | None = None,
                 raise_after: int | None = None, last_trace: str = ""):
        self._chunks = chunks
        self._adds = history_additions or []
        self._raise_after = raise_after
        self.last_trace = last_trace

    async def process(self, envelope: Envelope, session: Session) -> AsyncIterator[str]:
        # Mirror real agent: incoming user msg goes into history first.
        session.add_message(envelope.message)
        for i, chunk in enumerate(self._chunks):
            if self._raise_after is not None and i >= self._raise_after:
                raise RuntimeError("agent exploded")
            yield chunk
        for msg in self._adds:
            session.add_message(msg)


def make_envelope(channel="cap", sid="sess-1") -> Envelope:
    msg = Message(
        role=MessageRole.USER,
        content="hello",
        channel_id=channel,
        session_id=sid,
        sender_id="u1",
    )
    return Envelope(message=msg, source_channel=channel)


# ---------- happy path ------------------------------------------------------


async def test_route_success_persists_only_new_messages():
    store = FakeStore()
    sm = SessionManager(store)
    # Pre-existing session with one historical message that should NOT be re-saved.
    pre = Session(id="sess-1", channel_id="cap", sender_id="u1")
    pre.history.append(Message(role=MessageRole.USER, content="old", session_id="sess-1"))
    store.sessions["sess-1"] = pre

    agent = ScriptedAgent(
        chunks=["hi ", "there"],
        history_additions=[Message(role=MessageRole.ASSISTANT,
                                    content="hi there", session_id="sess-1")],
        last_trace="trace-1",
    )
    channel = CapturingChannel()
    router = Router(sm)
    router.register_channel(channel)
    router.register_agent(agent, default=True)

    await router.route(make_envelope())

    assert "".join(channel.streamed) == "hi there"
    assert channel.done == ["hi there"]
    # Trace not delivered — channel doesn't define send_trace.
    assert channel.trace == []
    # Only the user msg + assistant reply (added during this turn) persisted.
    saved_contents = [m.content for m in store.messages]
    assert "old" not in saved_contents
    assert "hello" in saved_contents
    assert "hi there" in saved_contents


async def test_trace_delivered_only_to_opt_in_channel():
    store = FakeStore()
    sm = SessionManager(store)
    agent = ScriptedAgent(chunks=["x"], last_trace="step 1: …")

    cap = CapturingChannel()  # no send_trace method
    trc = TraceChannel()      # defines send_trace
    router = Router(sm)
    router.register_channel(cap)
    router.register_channel(trc)
    router.register_agent(agent, default=True)

    await router.route(make_envelope(channel="cap", sid="s-cap"))
    await router.route(make_envelope(channel="trace-ch", sid="s-trc"))

    assert cap.trace == []
    assert trc.trace == ["step 1: …"]


# ---------- failure rollback ------------------------------------------------


async def test_route_failure_rolls_back_in_memory_history():
    store = FakeStore()
    sm = SessionManager(store)
    pre = Session(id="sess-1", channel_id="cap", sender_id="u1")
    store.sessions["sess-1"] = pre

    # Yield "partial" then raise — simulates an LLM stream that errors mid-flight.
    agent = ScriptedAgent(chunks=["partial", "rest"], raise_after=1)
    channel = CapturingChannel()
    router = Router(sm)
    router.register_channel(channel)
    router.register_agent(agent, default=True)

    with pytest.raises(RuntimeError, match="agent exploded"):
        await router.route(make_envelope())

    # Session held by manager should have NO leftover user/assistant messages
    # from the failed turn — otherwise the next turn replays a phantom user
    # msg the LLM "remembers asking" but was never persisted.
    sess = await sm.get_session("sess-1")
    assert sess is not None
    assert sess.history == []
    # And no message rows persisted either.
    assert store.messages == []


async def test_route_unknown_channel_raises():
    store = FakeStore()
    sm = SessionManager(store)
    agent = ScriptedAgent(chunks=["x"])
    router = Router(sm)
    router.register_agent(agent, default=True)
    # No channel registered.

    with pytest.raises(ValueError, match="Unknown channel"):
        await router.route(make_envelope())
