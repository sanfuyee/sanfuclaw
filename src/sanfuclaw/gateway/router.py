"""Gateway Router — routes envelopes from channels to agents and back."""

from __future__ import annotations

from sanfuclaw.agents.base import Agent
from sanfuclaw.channels.base import Channel
from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.session import Session
from sanfuclaw.gateway.session_manager import SessionManager


class Router:
    """Central message router connecting channels to agents."""

    def __init__(self, session_manager: SessionManager | None = None):
        self._channels: dict[str, Channel] = {}
        self._agents: dict[str, Agent] = {}
        self._sessions: dict[str, Session] = {}
        self._default_agent: str | None = None
        self._session_manager = session_manager

    def register_channel(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    def register_agent(self, agent: Agent, default: bool = False) -> None:
        self._agents[agent.name] = agent
        if default or self._default_agent is None:
            self._default_agent = agent.name

    async def get_or_create_session(self, envelope: Envelope) -> Session:
        """Resolve or create a session, with optional persistence."""
        if self._session_manager:
            return await self._session_manager.get_or_create(envelope)

        # Fallback: in-memory only
        session_id = envelope.message.session_id
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(
                id=session_id,
                channel_id=envelope.source_channel,
                sender_id=envelope.message.sender_id,
            )
        return self._sessions[session_id]

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
            raise ValueError(f"Unknown channel: {envelope.source_channel}")

        # Send typing indicator
        await channel.send_typing(envelope.message.session_id)

        # Stream response back to channel
        full_response = ""
        async for chunk in agent.process(envelope, session):
            full_response += chunk
            await channel.send(envelope.message.session_id, chunk, streaming=True)

        # Signal stream complete — flush buffered response
        await channel.send(envelope.message.session_id, full_response, done=True)

        # Persist session and messages
        if self._session_manager:
            await self._session_manager.update_session(session)
            for msg in session.history:
                # Ensure message has the correct session_id
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
