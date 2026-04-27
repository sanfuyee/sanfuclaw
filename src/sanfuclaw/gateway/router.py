"""Gateway Router — routes envelopes from channels to agents and back."""

from __future__ import annotations

import logging

from sanfuclaw.agents.base import Agent
from sanfuclaw.channels.base import Channel
from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.gateway.session_manager import SessionManager

logger = logging.getLogger(__name__)


class Router:
    """Central message router connecting channels to agents."""

    def __init__(self, session_manager: SessionManager):
        self._channels: dict[str, Channel] = {}
        self._agents: dict[str, Agent] = {}
        self._default_agent: str | None = None
        self._session_manager = session_manager

    def register_channel(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    def register_agent(self, agent: Agent, default: bool = False) -> None:
        self._agents[agent.name] = agent
        if default or self._default_agent is None:
            self._default_agent = agent.name

    async def get_or_create_session(self, envelope: Envelope) -> Session:
        """Resolve or create a session via the session manager."""
        return await self._session_manager.get_or_create(envelope)

    def resolve_agent(self, envelope: Envelope) -> Agent:
        """Determine which agent should handle this envelope."""
        agent_name = envelope.target_agent or self._default_agent
        if agent_name and agent_name in self._agents:
            return self._agents[agent_name]
        raise ValueError("No agent available to handle this envelope")

    async def route(self, envelope: Envelope) -> None:
        """Route an envelope: resolve session and agent, process, stream reply."""
        session = await self.get_or_create_session(envelope)
        agent = self.resolve_agent(envelope)
        channel = self._channels.get(envelope.source_channel)
        if not channel:
            logger.error("Unknown channel %r — dropping envelope", envelope.source_channel)
            raise ValueError(f"Unknown channel: {envelope.source_channel}")

        logger.debug(
            "Routing envelope from %s → agent=%s session=%s",
            envelope.source_channel, agent.name, session.id[:8],
        )

        # All downstream channel calls use the resolved session.id, not the
        # incoming envelope's session_id (which may be empty or a placeholder
        # the SessionManager remapped to a different ID).
        sid = session.id

        # Send typing indicator
        await channel.send_typing(sid)

        # Snapshot history length so we only persist messages added this turn,
        # and so we can roll back if the turn fails partway through.
        baseline = len(session.history)

        try:
            # Stream response back to channel
            full_response = ""
            async for chunk in agent.process(envelope, session):
                full_response += chunk
                await channel.send(sid, chunk, streaming=True)

            # Signal stream complete — flush buffered response
            await channel.send(sid, full_response, done=True)

            # Deliver the per-turn trace as a separate event, but only to
            # channels that opt in (CLI). User-facing channels skip it.
            trace = getattr(agent, "last_trace", "")
            if trace and getattr(channel, "wants_trace", False):
                await channel.send(sid, trace, trace=True)

            # Persist session metadata + only the messages added this turn.
            await self._session_manager.update_session(session)
            for msg in session.history[baseline:]:
                if msg.session_id != session.id:
                    msg = Message(
                        role=msg.role,
                        content=msg.content,
                        id=msg.id,
                        channel_id=msg.channel_id,
                        sender_id=msg.sender_id,
                        metadata=msg.metadata,
                        timestamp=msg.timestamp,
                        session_id=session.id,
                    )
                await self._session_manager.save_message(msg)
        except Exception:
            # Turn failed (LLM error, channel send error, persist error).
            # The in-memory session may now have unsaved messages — roll back
            # so DB and memory stay in sync. Otherwise the next turn's
            # model would see phantom user messages from this failed attempt
            # (e.g. "you asked X earlier" when X was never persisted).
            dropped = len(session.history) - baseline
            if dropped > 0:
                logger.warning(
                    "Turn failed for session=%s; rolling back %d unsaved in-memory message(s)",
                    session.id[:8], dropped,
                )
                del session.history[baseline:]
            raise
