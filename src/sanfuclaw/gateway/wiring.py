"""Factory that wires transport + tools + MCP + agent + router from settings.

Both the CLI (`sanfuclaw start`) and the gateway server (`sanfuclaw serve`)
need the same construction pipeline. Keeping it here avoids two parallel copies
drifting apart when the agent or tool wiring changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sanfuclaw.agents.llm_agent import LLMAgent
from sanfuclaw.agents.transports.base import LLMTransport
from sanfuclaw.core.config import Settings
from sanfuclaw.gateway.router import Router
from sanfuclaw.gateway.scheduler import Scheduler
from sanfuclaw.gateway.session_manager import SessionManager
from sanfuclaw.mcp_client.manager import MCPManager
from sanfuclaw.mcp_client.tool_adapter import MCPToolAdapter
from sanfuclaw.skills.registry import SkillRegistry
from sanfuclaw.storage.base import Store
from sanfuclaw.tools.registry import ToolRegistry
from sanfuclaw.tools.shell import ShellTool
from sanfuclaw.tools.skill_loader import LoadSkillTool
from sanfuclaw.tools.web_fetch import WebFetchTool


class MissingAPIKey(RuntimeError):
    """Raised when no LLM API key is configured."""


@dataclass
class Wiring:
    router: Router
    agent: LLMAgent
    mcp_manager: MCPManager
    tool_registry: ToolRegistry
    skill_registry: SkillRegistry
    scheduler: Scheduler

    async def start_runtime(self) -> None:
        """Start runtime services that depend on channels being registered.

        Call this AFTER all channels have been registered on the router —
        the scheduler routes envelopes through them, so they need to exist.
        """
        await self.scheduler.start()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        await self.mcp_manager.stop()


def build_transport(settings: Settings) -> LLMTransport:
    """Construct the LLM transport based on settings + env vars."""
    api_key = (
        settings.llm.api_key
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("LLM_API_KEY", "")
    )
    if not api_key:
        raise MissingAPIKey(
            "No API key found. Set llm.api_key in ~/.sanfuclaw/config.json or LLM_API_KEY env var"
        )

    if settings.llm.provider == "anthropic":
        from sanfuclaw.agents.transports.anthropic import AnthropicTransport
        return AnthropicTransport(api_key=api_key, default_model=settings.llm.model)

    from sanfuclaw.agents.transports.openai_compat import OpenAICompatTransport
    return OpenAICompatTransport(
        api_key=api_key,
        base_url=settings.llm.base_url,
        default_model=settings.llm.model,
    )


async def build_router(
    settings: Settings, store: Store, session_manager: SessionManager
) -> Wiring:
    """Wire tools, MCP servers, transport, agent, router, and scheduler."""
    skill_registry = SkillRegistry(settings.skills.dir)

    tool_registry = ToolRegistry()
    tool_registry.register(ShellTool())
    tool_registry.register(WebFetchTool())
    if len(skill_registry) > 0:
        tool_registry.register(LoadSkillTool(skill_registry))

    mcp_manager = MCPManager(settings.mcp.servers)
    await mcp_manager.start()
    for server_name, mcp_tool in mcp_manager.tools():
        mcp_session = mcp_manager.get_session(server_name)
        tool_registry.register(MCPToolAdapter(server_name, mcp_tool, mcp_session))

    transport = build_transport(settings)
    agent = LLMAgent(
        name="default",
        transport=transport,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        system_prompt=settings.llm.system_prompt,
        model=settings.llm.model,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
    )

    router = Router(session_manager=session_manager)
    router.register_agent(agent, default=True)

    scheduler = Scheduler(store=store, router=router)

    return Wiring(
        router=router,
        agent=agent,
        mcp_manager=mcp_manager,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        scheduler=scheduler,
    )
