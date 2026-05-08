"""Factory that wires transport + tools + MCP + agent + router from settings.

Both the CLI (`sanfuclaw start`) and the gateway server (`sanfuclaw serve`)
need the same construction pipeline. Keeping it here avoids two parallel copies
drifting apart when the agent or tool wiring changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sanfuclaw.agents.llm_agent import LLMAgent
from sanfuclaw.agents.system_prompt import SystemPromptBuilder
from sanfuclaw.agents.transports.base import LLMTransport
from sanfuclaw.core.config import LLMConfigError, Settings
from sanfuclaw.core.schedule_service import ScheduleService
from sanfuclaw.gateway.router import Router
from sanfuclaw.gateway.scheduler import Scheduler
from sanfuclaw.gateway.session_manager import SessionManager
from sanfuclaw.mcp_client.manager import MCPManager
from sanfuclaw.mcp_client.tool_adapter import MCPToolAdapter
from sanfuclaw.memory.registry import MemoryRegistry
from sanfuclaw.skills.registry import SkillRegistry
from sanfuclaw.storage.base import Store
from sanfuclaw.tools.memory_loader import LoadMemoryTool
from sanfuclaw.tools.memory_writer import (
    ForgetMemoryTool,
    SaveMemoryTool,
    UpdateMemoryTool,
)
from sanfuclaw.tools.registry import ToolRegistry
from sanfuclaw.tools.schedule import (
    ScheduleCreateTool,
    ScheduleListTool,
    ScheduleRemoveTool,
    ScheduleSetEnabledTool,
)
from sanfuclaw.tools.clipboard import ClipboardReadTool, ClipboardWriteTool
from sanfuclaw.tools.code_search import CodeSearchTool
from sanfuclaw.tools.read_file import ReadFileTool
from sanfuclaw.tools.shell import ShellTool
from sanfuclaw.tools.speak import SpeakTool
from sanfuclaw.tools.skill_loader import LoadSkillTool
from sanfuclaw.tools.task import TaskWriteTool
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


PLANNING_GUIDANCE = """
Planning behavior:
- For multi-step tasks (3+ distinct steps, multiple tools/sources, or
  anything where a step might fail and force replanning), call
  `task_write` FIRST with a numbered plan. Mark exactly one task as
  'in_progress' and the rest as 'pending'.
- After each meaningful step (a tool call resolves an item, a research
  finding answers a sub-question), call `task_write` again with the
  full updated state: the just-finished task moves to 'completed' with
  a one-line `note` capturing what you learned, and the next task
  becomes 'in_progress'.
- When a tool returns an Error or observation contradicts the plan,
  REPLAN — call `task_write` to drop dead steps, add new ones, or
  reorder. Don't silently abandon the plan and improvise.
- The current plan is appended to your system prompt every turn. Trust
  it as the live source of truth — execute and revise it, don't
  re-derive it from history.
- Skip planning entirely for one-shot questions ('what's the weather',
  'show me the file X'). Planning overhead is worse than no plan.
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
    """Construct the LLM transport based on settings + env vars.

    Assumes `settings.llm.validate_startup()` has already run — the caller
    (`build_router`) does this so config errors surface before tools/MCP
    spawn. We still re-resolve the api key here in case env vars supply it.
    """
    api_key = settings.llm.resolved_api_key()
    if not api_key:
        # Defensive — validate_startup() should have caught this.
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
    """Wire tools, MCP servers, transport, agent, router, and scheduler.

    Validates LLM settings up front so config typos fail fast (before MCP
    servers fork or skills load). Errors propagate as `LLMConfigError`
    (or `MissingAPIKey` for the legacy api-key-only path); the CLI catches
    both and prints a friendly hint."""
    settings.llm.validate_startup()

    skill_registry = SkillRegistry(settings.skills.dir)
    memory_registry = MemoryRegistry(settings.memory.dir)

    tool_registry = ToolRegistry()
    tool_registry.register(ShellTool())
    tool_registry.register(ReadFileTool())
    tool_registry.register(CodeSearchTool())
    tool_registry.register(ClipboardReadTool())
    tool_registry.register(ClipboardWriteTool())
    tool_registry.register(SpeakTool())
    tool_registry.register(WebSearchTool())
    tool_registry.register(WebFetchTool())
    tool_registry.register(WeatherTool())
    tool_registry.register(TaskWriteTool())
    schedule_service = ScheduleService(store, default_timezone=settings.timezone)
    tool_registry.register(ScheduleCreateTool(schedule_service))
    tool_registry.register(ScheduleListTool(schedule_service))
    tool_registry.register(ScheduleSetEnabledTool(schedule_service))
    tool_registry.register(ScheduleRemoveTool(schedule_service))
    if len(skill_registry) > 0:
        tool_registry.register(LoadSkillTool(skill_registry))
    if len(memory_registry) > 0 or memory_registry.system_prompt_block():
        tool_registry.register(LoadMemoryTool(memory_registry))
    # Write tools registered unconditionally — the LLM can create the first
    # entry into an empty memory dir, which is exactly when curating helps most.
    tool_registry.register(SaveMemoryTool(memory_registry))
    tool_registry.register(UpdateMemoryTool(memory_registry))
    tool_registry.register(ForgetMemoryTool(memory_registry))

    mcp_manager = MCPManager(settings.mcp.servers)
    await mcp_manager.start()
    mcp_tool_count = 0
    for server_name, mcp_tool in mcp_manager.tools():
        tool_registry.register(MCPToolAdapter(server_name, mcp_tool, mcp_manager))
        mcp_tool_count += 1
    logger.info(
        "Tool registry ready: %d local + %d MCP tool(s) from %d skill(s), %d memory entr(ies)",
        len(tool_registry.list_names()) - mcp_tool_count,
        mcp_tool_count,
        len(skill_registry),
        len(memory_registry),
    )

    prompt_builder = (
        SystemPromptBuilder()
        .add("base", settings.llm.system_prompt)
        .add("schedule", SCHEDULE_PROMPT_GUIDANCE)
        .add("tool_efficiency", TOOL_EFFICIENCY_GUIDANCE)
        .add("planning", PLANNING_GUIDANCE)
        .add("web_research", WEB_RESEARCH_GUIDANCE)
        .add("memory", memory_registry.system_prompt_block())
    )
    prompt_builder.log_summary()

    transport = build_transport(settings)
    agent = LLMAgent(
        name="default",
        transport=transport,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        system_prompt=prompt_builder.render(),
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
