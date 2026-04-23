# Sanfuclaw Architecture

> Design rationale and internals. For install/usage instructions see the
> top-level [README](../README.md).

## Overview

Sanfuclaw is a **local-first personal AI agent** built in Python, inspired
by [OpenClaw](https://github.com/openclaw/openclaw). It runs on your own
machine, bridges messaging platforms to an LLM, and extends the LLM with
local tools, markdown skills, and any MCP server you wire up.

The architecture optimizes for three things, in order:

1. **Decoupling** — you can add a channel, tool, or LLM provider without
   touching any other component. Every extension point is a
   `typing.Protocol`, so there is no base class to inherit and no registry
   to import into.
2. **Locality** — sessions, history, and credentials live on disk. The
   default storage is a single SQLite file. No cloud dependency is
   required to run the agent.
3. **Streaming** — responses flow as async chunks end-to-end, from the LLM
   transport through the agent through the channel. Channels that cannot
   render partial tokens (Telegram, WeChat) buffer at the channel layer,
   not at the agent, so the agent loop stays simple.

## Architecture

### Hub and spoke

```
    ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
    │   CLI    │  │ Telegram │  │  WeChat  │  │ WebChat  │    ← Channels
    └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
         │             │             │             │
         └─────────────┴──────┬──────┴─────────────┘
                              │  Envelope
                       ┌──────▼───────┐
                       │    Router    │  ← Gateway
                       │  + Sessions  │
                       └──────┬───────┘
                              │
                       ┌──────▼───────┐
                       │  LLM Agent   │  ← streams chunks back to channel
                       └──┬────────┬──┘
                          │        │
                  ┌───────▼──┐  ┌──▼──────────┐
                  │Transport │  │ToolRegistry │
                  │(Anthro/  │  │             │
                  │ OpenAI)  │  │ shell       │
                  └──────────┘  │ web_fetch   │
                                │ load_skill ─┼─→ SkillRegistry (*.md)
                                │ mcp_* ──────┼─→ MCPManager → stdio/SSE
                                └─────────────┘
                                      │
                               ┌──────▼──────┐
                               │   SQLite    │  ← sessions + messages
                               └─────────────┘
```

Channels, tools, skills, and MCP servers all plug into a single
`ToolRegistry` so the LLM sees one unified surface. There is no separate
"skill runtime" or "MCP runtime" — `load_skill` is a normal tool, and MCP
tools are normal tools behind a thin adapter.

### Tech stack

| Layer | Dependency | Why this one |
|---|---|---|
| Runtime | Python 3.12+ | `asyncio.TaskGroup`, improved typing, `tomllib` |
| HTTP / WS server | `fastapi` + `uvicorn` | Native async, built-in WebSocket |
| LLM SDKs | `anthropic`, `openai` | Official streaming clients |
| HTTP client | `httpx` | Async, used by WeChat iLink API and `web_fetch` tool |
| Storage | `aiosqlite` | Zero-config local DB, async |
| Config | `pydantic` + `pydantic-settings` + `tomllib` | Typed, env-overridable |
| CLI | `typer` + `rich` | Subcommand routing + pretty output |
| Telegram | `python-telegram-bot` (optional) | Only channel adapter we don't own |
| WeChat | `qrcode` (optional) | Terminal QR for iLink Bot login |
| MCP | `mcp` (optional) | Official Model Context Protocol SDK |
| Testing | `pytest` + `pytest-asyncio` | Standard |

Intentionally *not* in the stack: no ORM (there are 3 tables; raw SQL is
clearer), no scheduler (there is no recurring work), no dependency
injection framework (wiring happens explicitly in `gateway/wiring.py`,
shared by both the CLI and gateway entry points).

## Core abstractions

All extension points are `typing.Protocol` — structural subtyping, no
inheritance required. A class is a Channel because it has the right
methods, not because it imports one.

### Data types

- **`Message`** — immutable dataclass: `id`, `role` (`user` / `assistant`
  / `system` / `tool`), `content`, `channel_id`, `session_id`,
  `sender_id`, `timestamp`, `metadata`.
- **`Envelope`** — routing wrapper around a `Message`: `source_channel`,
  `target_agent` (optional), `reply_to` (optional).
- **`Session`** — stateful conversation: `id`, `channel_id`, `sender_id`,
  `history: list[Message]`, plus arbitrary `metadata`.
- **`StreamChunk`** — a single event from a transport, tagged with
  `StreamChunkType`: `TEXT_DELTA`, `TOOL_USE`, `TOOL_RESULT`, `USAGE`,
  `STOP`, `ERROR`. The `USAGE` variant carries `input_tokens` and
  `output_tokens`.

### Protocols

**Channel.** `name: str`, `start()`, `stop()`, `send(session_id, content,
**kwargs)`, `send_typing(session_id)`, `receive() -> AsyncIterator[Envelope]`.
A platform adapter pushes envelopes onto an internal queue from whatever
callback the platform SDK gives it, and yields them on `receive()`.

**Agent.** `name: str`, `process(envelope, session) -> AsyncIterator[str]`.
`LLMAgent` is the only current implementation; a rules-based or
keyword-routing agent would implement the same interface.

**LLMTransport.** `complete(messages, tools, model, ...) ->
AsyncIterator[StreamChunk]` plus a `message_format` attribute
(`"anthropic"` or `"openai"`) so the agent knows how to build the
provider-specific message list. Two implementations ship:
`AnthropicTransport` and `OpenAICompatTransport`.

**Tool.** `name: str`, `description: str`, `parameters_schema: dict`
(JSON Schema), `execute(params, session) -> Any`. Schemas are handed
verbatim to the LLM as tool definitions.

**Store.** `init()`, `close()`, `save_message()`, `get_history()`,
`save_session()`, `get_session()`. Only `SQLiteStore` exists today;
swapping to Postgres or DuckDB means writing one class.

### Composition, not protocols

Two components are *not* protocols because there is exactly one sensible
implementation and they're pure registry/dispatch code:

- **`SkillRegistry`** — scans a directory for `*.md` files with
  frontmatter, exposes `system_prompt_block()` and `get(name)`. Owned by
  the agent.
- **`MCPManager`** — holds an `AsyncExitStack` of MCP client sessions,
  exposes `tools()` and `get_session(name)`. Owned by the CLI / gateway.

## Data flow

### Normal message round-trip

1. **Channel receives input** from its platform (Telegram callback,
   WeChat long-poll, stdin, WebSocket frame) and yields an `Envelope`
   on its `receive()` iterator.
2. **Router resolves a `Session`** via the `SessionManager` — loaded
   from SQLite on first hit, cached in memory afterward.
3. **Router picks an agent** (`envelope.target_agent` or the default)
   and calls `agent.process(envelope, session)`.
4. **Agent slices history** to the `max_history` (default 20) most
   recent messages for the prompt — without mutating `session.history`,
   so the persisted record stays complete. Long sessions never inflate
   token cost unboundedly.
5. **Agent builds provider-specific messages.** Anthropic format nests
   tool calls inside an assistant `content` array; OpenAI format uses a
   separate `tool_calls` field. `_build_anthropic_tool_messages` /
   `_build_openai_tool_messages` handle both.
6. **Transport streams chunks.** The agent forwards `TEXT_DELTA` chunks
   to the caller, accumulates `TOOL_USE` chunks, and updates token
   counters on `USAGE` chunks.
7. **Channel streams chunks to the platform** via
   `channel.send(session_id, chunk, streaming=True)`.

### Multi-round tool loop

After the first LLM turn, if any `TOOL_USE` chunks were emitted:

1. The assistant message (with its tool-call metadata) is appended to
   `session.history`.
2. Each tool is dispatched through the `ToolRegistry` — whether it's
   local (`shell`, `web_fetch`, `load_skill`) or a wrapped MCP tool is
   transparent to this loop.
3. Each tool result becomes a `Message(role=TOOL, ...)` on the history.
4. The agent re-enters the loop and calls the transport again.

The loop caps at `max_tool_rounds` (default 5) to prevent runaway
call chains. On the final turn with no more tool calls, the agent:

- saves the final assistant message to the session,
- stashes a **trace summary** — per-round token counts, the list of
  tools invoked with their summarized inputs, total input/output, and
  current history depth — on `agent.last_trace`.

The router delivers this trace out-of-band via `channel.send(sid, trace,
trace=True)`, but only to channels that opt in by setting
`wants_trace = True`. Today only `CLIChannel` opts in. User-facing
channels (Telegram, WeChat, WebChat) do not see the trace, so platform
users only see the model's actual response.

### Channel streaming vs. buffering

Real-time token streaming works for CLI and WebChat. Telegram and WeChat
cannot render partial messages without rate-limit pain, so they buffer.

The agent and router don't know or care. They always:

```python
async for chunk in agent.process(...):
    await channel.send(session_id, chunk, streaming=True)
await channel.send(session_id, full_response, done=True)
```

Each channel decides what `streaming=True` means:

- **CLI / WSChannel**: write the chunk immediately.
- **Telegram / WeChat**: accumulate in a per-session buffer.

And what `done=True` means:

- **CLI / WSChannel**: finalize the stream (newline, `type: "done"`).
- **Telegram / WeChat**: flush the buffer as a single platform message.

`done=True` replaced an older sentinel-based flush (`send("")` or
`send("\n")`), which was brittle because trace and model output both
contain newlines — one question sometimes produced three WeChat messages.

### Session persistence

After each successful `route()`:

1. `SessionManager.update_session(session)` writes the updated
   `updated_at` and any changed metadata.
2. **Only the messages added during this turn** are `save_message()`'d.
   The router snapshots `len(session.history)` before invoking the agent
   and writes `history[baseline:]` afterwards, so old rows aren't
   re-INSERTed every turn. The router also re-stamps `session_id` on any
   message whose id drifted during processing — a safety net for tool
   messages generated mid-loop.

Messages are append-only; history trimming happens in memory at the
agent layer (via a local slice in `_build_messages`) and does not mutate
`session.history` or delete rows. The SQLite file is the source of
truth for restarts.

## Subsystems

### Skills (`src/sanfuclaw/skills/`)

A skill is a markdown file with frontmatter:

```markdown
---
name: my-skill
description: One-line hook the LLM sees in the system prompt.
---

Full instructions here — only loaded when needed.
```

On startup, `SkillRegistry(settings.skills.dir)` walks the directory,
parses each file's frontmatter (`_parse_frontmatter` handles simple
`key: value` lines — no YAML dependency), and builds two things:

- a **system-prompt block** listing `name: description` for every
  discovered skill, injected into `LLMAgent._system_prompt`;
- a built-in **`load_skill` tool** that takes a skill name and returns
  the full body.

The LLM sees the summary up front and calls `load_skill("my-skill")`
when it decides the request matches. Context stays small even with
dozens of skills installed — we only pay for a skill's full body when
it's actually used.

Skills compose with other tools. A `release-notes` skill can instruct
the LLM to call `shell` with `git log`; the `weather-report` skill tells
it to use `web_fetch` on `wttr.in`. No skill runtime, no helper scripts,
no imports — they're just instructions.

### MCP (`src/sanfuclaw/mcp_client/`)

Configured under `[mcp.servers.<name>]` in `sanfuclaw.toml`. Each server
can be spawned over stdio (`command` + `args` + `env`) or reached over
SSE (`url`).

`MCPManager.start()`:

1. Enters a shared `AsyncExitStack`.
2. For each enabled server, enters either `stdio_client(params)` or
   `sse_client(url)` as a managed context, getting back `(read, write)`
   streams.
3. Wraps them in an `mcp.ClientSession`, calls `initialize()`, then
   `list_tools()`.
4. Records `(server_name, mcp.Tool)` tuples.

A per-server failure is logged but does not crash startup — the other
servers still come up.

`MCPToolAdapter` wraps each discovered tool in the sanfuclaw `Tool`
protocol. The adapter:

- sanitizes names to `mcp_<server>_<tool>` (Anthropic/OpenAI accept only
  `[A-Za-z0-9_-]` in tool names),
- hands the MCP server's `inputSchema` verbatim as `parameters_schema`,
- forwards `execute(params)` to `session.call_tool(mcp_tool.name, params)`
  and converts the content blocks to text.

All MCP tools are registered into the same `ToolRegistry` the local
tools use. The agent's tool-calling loop is oblivious.

Shutdown is a single `await self._stack.aclose()` — every transport and
session is closed in reverse registration order.

### Scheduler (`gateway/scheduler.py`)

A schedule entry is just "at this cron time, send this prompt as a user
message into `<target_channel>`". The `Scheduler`:

1. Loads enabled `Schedule` rows from SQLite (`schedules` table).
2. Runs a single async loop. Each tick: find rows whose `next_run_at`
   has passed, fire them, recompute `next_run_at` via `croniter`, and
   sleep until the next earliest run (capped at 60s so newly-added
   entries get picked up without an explicit notify hook).
3. To "fire", it synthesizes an `Envelope` with `source_channel =
   target_channel` and routes it through the same `Router` everyone
   else uses. The reply streams back to the target channel naturally —
   the platform user sees a normal assistant message; nothing tells
   them it came from cron.

**Missed runs are silently skipped.** On startup we recompute
`next_run_at` from `now()`; we never backfill. If sanfuclaw was off
overnight, the 8am task simply runs at 8am the next day.

The Scheduler is started via `Wiring.start_runtime()` *after* all
channels are registered, since firing a schedule requires the target
channel to exist on the router. Stopped via `Wiring.shutdown()`.

CRUD lives in `cli_cron.py` (typer subcommands). The CLI commands
operate on SQLite directly without spinning up the agent stack — a
running `sanfuclaw start`/`serve` daemon picks up changes within one
poll interval (≤60s).

### Gateway (`src/sanfuclaw/gateway/`)

`GatewayServer` is a FastAPI app that exposes:

- HTTP: `/health`, `/api/status`, `/api/sessions`, `/api/sessions/{id}/messages`
- WebSocket: `/ws` with a small JSON wire protocol
  (`{"type": "message" | "ping" | "stream" | "done" | "typing" |
  "error", ...}`)
- Static: `/` serves the WebChat UI, `/static/*` serves its assets

The same `Router`/`LLMAgent`/`ToolRegistry` used by the standalone CLI
powers the gateway — there is no "server mode" vs "cli mode" branching
inside the agent. `WSChannel` is a pseudo-channel that delivers to
WebSocket clients, letting the router treat the browser exactly like
any other platform.

## Design decisions

1. **Protocols over ABCs.** No import or subclass required. A one-file
   `ShellTool` is a valid tool because it has the right attributes. Tests
   mock by structural match, not `Mock(spec=Tool)`.
2. **Single process, single event loop.** Gateway, channels, agents, and
   MCP clients all live in one `asyncio` process. Splitting over
   WebSocket is possible later but unnecessary for a local agent.
3. **Unified tool registry.** Shell, skills, and MCP share one registry.
   The agent has no special cases. Adding a new tool source means
   registering an adapter, not teaching the agent about it.
4. **Lazy skill loading.** System prompt lists only names and
   descriptions; full bodies load via `load_skill` on demand. Keeps
   context small regardless of library size.
5. **Buffer at the channel, not the agent.** Platform-specific streaming
   limitations stay behind the channel interface. The agent always
   streams; channels decide how to render.
6. **History trimming at the agent.** Long sessions don't inflate token
   cost because `_build_messages` caps history before every call. Rows
   are preserved in SQLite — we only trim the *sent* context.
7. **Usage is a first-class chunk.** `StreamChunkType.USAGE` carries
   input/output token counts. The OpenAI-compat transport deduplicates
   providers (like HPC-AI) that repeat usage in every streaming chunk
   and emits a single `USAGE` at the end.
8. **Retry at the transport.** The OpenAI-compat transport retries
   `APIError` / `APIConnectionError` with exponential backoff (1s, 2s,
   4s). Agents never see transient 502s.
9. **Tool schemas as JSON Schema.** Matches the Claude and OpenAI
   tool-use APIs one-to-one; no translation layer.
10. **TOML config + env vars.** Secrets come from env (`LLM_API_KEY`,
    `TELEGRAM_BOT_TOKEN`); structure comes from `sanfuclaw.toml`. The
    file is gitignored so live credentials never land in git.
11. **No ORM.** Three tables (`sessions`, `messages`, maybe one for
    hooks) is below the break-even point for any ORM. Raw `aiosqlite`
    is clearer and faster.
12. **Graceful MCP degradation.** A failing MCP server logs an error
    and the app keeps running. One broken `npx` package cannot brick
    the whole agent.

## Current state

All core components are implemented:

- ✅ **Channels**: CLI, Telegram, WeChat (reverse-engineered iLink Bot),
  WebChat (browser via WebSocket).
- ✅ **Transports**: Anthropic, OpenAI-compatible (with retry and
  usage-chunk deduplication).
- ✅ **Tools**: shell, web_fetch, load_skill, plus any MCP tool.
- ✅ **Skills**: markdown-based, lazy-loaded, ships with 5 example
  skills (`weather-report`, `code-review`, `commit-message`,
  `explain-error`, `release-notes`).
- ✅ **MCP**: stdio + SSE, auto-discovery, registered into the unified
  tool registry.
- ✅ **Storage**: SQLite with session manager and history.
- ✅ **Gateway**: FastAPI WebSocket + HTTP + WebChat UI.
- ✅ **Scheduler**: cron-driven prompts that synthesize envelopes into
  any registered channel; CLI CRUD via `sanfuclaw cron`.

Known gaps, in rough priority order:

- Event hook system referenced in the old design has no implementation
  yet — `SQLiteStore` is the only consumer of session-change events.
- No Discord channel. The `Channel` protocol is generic; adding one is
  a ~100-line file modeled on `TelegramChannel`.
- No built-in web search. Today users either wire up `exa` or
  `brave-search` via MCP, or rely on `web_fetch`.
- No auth/pairing on the WebSocket gateway — fine for localhost, not
  safe to expose.
- Scheduler has no agent-facing tool yet (the LLM can't add its own
  schedules); CRUD is human-driven only. Intentional for now.
