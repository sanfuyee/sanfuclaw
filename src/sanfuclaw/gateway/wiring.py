"""Factory that wires transport + tools + MCP + agent + router from settings.

Both the CLI (`sanfuclaw start`) and the gateway server (`sanfuclaw serve`)
need the same construction pipeline. Keeping it here avoids two parallel copies
drifting apart when the agent or tool wiring changes.
"""

from __future__ import annotations

import logging
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
from sanfuclaw.memory.registry import MemoryRegistry
from sanfuclaw.skills.registry import SkillRegistry
from sanfuclaw.storage.base import Store
from sanfuclaw.tools.memory_loader import LoadMemoryTool
from sanfuclaw.tools.registry import ToolRegistry
from sanfuclaw.tools.schedule import (
    ScheduleCreateTool,
    ScheduleListTool,
    ScheduleRemoveTool,
    ScheduleSetEnabledTool,
)
from sanfuclaw.tools.shell import ShellTool
from sanfuclaw.tools.skill_loader import LoadSkillTool
from sanfuclaw.tools.weather import WeatherTool
from sanfuclaw.tools.web_fetch import WebFetchTool
from sanfuclaw.tools.web_search import WebSearchTool


logger = logging.getLogger(__name__)


class MissingAPIKey(RuntimeError):
    """Raised when no LLM API key is configured."""


SCHEDULE_PROMPT_GUIDANCE = """
Scheduling behavior:
- If a user asks to create/update/delete/list reminders or recurring tasks, use schedule tools.
- Interpret natural language times and convert them to cron for `schedule_create`.
- Interpret times in the configured default timezone unless the user explicitly provides one.
- Default schedule target to the current conversation unless the user asks for a different channel/session.
- After mutations, report the schedule id and next run time.
""".strip()


TOOL_EFFICIENCY_GUIDANCE = """
Tool-use efficiency:
- Each tool round costs a full LLM turn. Minimize rounds by batching.
- When exploring files, combine reads into ONE `shell` call:
  `cat f1 f2 f3` or `for f in a b c; do echo "=== $f ==="; cat "$f"; done`.
- Chain related shell steps with `&&` or `;` in a single command instead of
  issuing them over several rounds.
- When independent operations are needed, emit MULTIPLE tool calls in the
  SAME round (parallel) rather than one per round (serial).
- Prefer `find ... -exec cat {} +` or `head -n N f1 f2 f3` over many small reads.
""".strip()


WEB_RESEARCH_GUIDANCE = """
Web research:
- For time-sensitive content (today's news, current prices, recent
  releases, latest docs), call `web_search` FIRST to discover real,
  current URLs. Do not guess URLs from training-time memory — site
  paths change, and stale URLs return 404s or wrong content.
- After search, fetch a few of the top results in parallel with
  `web_fetch`. If `web_fetch` returns an Error (interstitial, soft
  404, JS-rendered shell), drop that source and try the next result
  instead of retrying the same URL.
- Prefer aggregator/RSS-friendly sources (Google News, Reuters,
  TechCrunch, HN) over heavy SPAs (36kr, sina.com.cn) — the SPAs
  often need a headless browser we don't have.
""".strip()


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
        logger.info("LLM transport: anthropic model=%s", settings.llm.model)
        return AnthropicTransport(api_key=api_key, default_model=settings.llm.model)

    from sanfuclaw.agents.transports.openai_compat import OpenAICompatTransport
    logger.info(
        "LLM transport: openai_compat model=%s base_url=%s",
        settings.llm.model, settings.llm.base_url,
    )
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
    memory_registry = MemoryRegistry(settings.memory.dir)

    tool_registry = ToolRegistry()
    tool_registry.register(ShellTool())
    tool_registry.register(WebSearchTool())
    tool_registry.register(WebFetchTool())
    tool_registry.register(WeatherTool())
    tool_registry.register(ScheduleCreateTool(store, default_timezone=settings.timezone))
    tool_registry.register(ScheduleListTool(store))
    tool_registry.register(ScheduleSetEnabledTool(store, default_timezone=settings.timezone))
    tool_registry.register(ScheduleRemoveTool(store))
    if len(skill_registry) > 0:
        tool_registry.register(LoadSkillTool(skill_registry))
    if len(memory_registry) > 0 or memory_registry.system_prompt_block():
        tool_registry.register(LoadMemoryTool(memory_registry))

    mcp_manager = MCPManager(settings.mcp.servers)
    await mcp_manager.start()
    mcp_tool_count = 0
    for server_name, mcp_tool in mcp_manager.tools():
        mcp_session = mcp_manager.get_session(server_name)
        tool_registry.register(MCPToolAdapter(server_name, mcp_tool, mcp_session))
        mcp_tool_count += 1
    logger.info(
        "Tool registry ready: %d local + %d MCP tool(s) from %d skill(s), %d memory entr(ies)",
        len(tool_registry.list_names()) - mcp_tool_count,
        mcp_tool_count,
        len(skill_registry),
        len(memory_registry),
    )

    memory_block = memory_registry.system_prompt_block()
    memory_suffix = f"\n\n{memory_block}" if memory_block else ""

    transport = build_transport(settings)
    agent = LLMAgent(
        name="default",
        transport=transport,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        system_prompt=(
            f"{settings.llm.system_prompt}\n\n"
            f"{SCHEDULE_PROMPT_GUIDANCE}\n\n"
            f"{TOOL_EFFICIENCY_GUIDANCE}\n\n"
            f"{WEB_RESEARCH_GUIDANCE}"
            f"{memory_suffix}"
        ),
        model=settings.llm.model,
        max_tokens=settings.llm.max_tokens,
        context_window=settings.llm.context_window,
        max_tool_rounds=settings.llm.max_tool_rounds,
        temperature=settings.llm.temperature,
    )

    router = Router(session_manager=session_manager)
    router.register_agent(agent, default=True)

    scheduler = Scheduler(store=store, router=router, timezone_name=settings.timezone)

    return Wiring(
        router=router,
        agent=agent,
        mcp_manager=mcp_manager,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        scheduler=scheduler,
    )
