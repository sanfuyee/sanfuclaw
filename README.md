# Sanfuclaw

A local-first personal AI agent inspired by [OpenClaw](https://github.com/openclaw/openclaw), built in Python.

## Features

- **Multi-channel**: CLI, Telegram, WeChat (iLink Bot), WebChat (browser), WebSocket API
- **Multi-provider LLM**: Anthropic Claude, OpenAI-compatible APIs (HPC-AI, vLLM, Ollama, etc.)
- **Tool system**: Shell commands, web fetch, extensible tool registry
- **Skill plugins**: Markdown-based skills with frontmatter, lazy-loaded on demand
- **MCP support**: Connect to any Model Context Protocol server over stdio or SSE
- **Persistent sessions**: SQLite-backed conversation history with list/resume support
- **Scheduled tasks**: cron-driven prompts that fire into any channel
- **Gateway server**: FastAPI with WebSocket streaming, REST API, WebChat UI
- **Event hooks**: Pluggable event system for custom integrations
- **Streaming-first**: Real-time response streaming across all channels

## Quick Start

### Install

```bash
pip install -e .   # installs everything; channels & MCP activate based on config
```

All channel and MCP dependencies are bundled. The runtime only starts what you
declare in your config file (e.g. a Telegram bot under `[channels.telegram]`,
an MCP server under `[mcp.servers.*]`), so unused integrations cost nothing
beyond install size.

The first time you run any `sanfuclaw` command it auto-creates
`~/.sanfuclaw/config.json` (template) and `~/.sanfuclaw/skills/`. No
extra setup step is required — just run `sanfuclaw --help` once, then
edit the config.

### Configure

Edit `~/.sanfuclaw/config.json`:

```json
{
  "llm": {
    "provider": "openai_compat",
    "model": "moonshotai/kimi-k2.5",
    "base_url": "https://api.hpc-ai.com/inference/v1",
    "api_key": "your-api-key"
  },
  "channels": {
    "telegram": { "type": "telegram", "bot_token": "YOUR_BOT_TOKEN" }
  }
}
```

Or set secrets via environment variables:

```bash
export LLM_API_KEY="your-key"
export TELEGRAM_BOT_TOKEN="your-bot-token"
```

Config resolution order: `--config <path>` → `$SANFUCLAW_CONFIG` →
`~/.sanfuclaw/config.json` → `./sanfuclaw.toml` (legacy). Override the home
directory itself with `$SANFUCLAW_HOME`.

### Uninstall

```bash
pip uninstall sanfuclaw    # remove the package
rm -rf ~/.sanfuclaw/       # optional: drop user data (config, sessions, credentials)
```

`pip uninstall` removes only the Python package — Python packaging has no
post-uninstall hook, so user data in `~/.sanfuclaw/` stays put unless you
remove it yourself.

### Run

There are two equivalent entry points — pick whichever fits the situation:

| Form | When to use |
|------|-------------|
| `python -m sanfuclaw <cmd>` | Source/debug mode — run straight out of a checkout, no install required beyond `pip install -e .`. Good for iterating on code, reading tracebacks against the working tree, running with a non-default Python. |
| `sanfuclaw <cmd>` | Installed entry point — registered by `[project.scripts]`. Good for daily use, shell aliases, and background service units (systemd / launchd). |

Both resolve to the same CLI; all flags, subcommands, and exit codes are identical.

```bash
# CLI chat mode
sanfuclaw start
python -m sanfuclaw start                 # equivalent (debug mode)

# Single messaging channel
sanfuclaw start --channel telegram

# All channels declared in config (Telegram + WeChat + Discord + …)
sanfuclaw start --channel all

# Gateway server (WebChat + REST API + WebSocket)
sanfuclaw serve

# Session management
sanfuclaw sessions                        # List recent sessions
sanfuclaw sessions -n 20                  # Show more sessions
sanfuclaw sessions --channel cli          # Filter by channel
sanfuclaw start --resume <ID>             # Resume a session (prefix match)

# Scheduled tasks (cron-driven prompts)
sanfuclaw cron add "0 8 * * *" --channel telegram --prompt "今日天气和待办"
sanfuclaw cron list
sanfuclaw cron disable <ID>               # pause without deleting
sanfuclaw cron remove <ID>

# Or ask in chat directly (agent uses schedule tools):
#   "每天下午2点给我发明天的天气"

# Cron is interpreted in your configured timezone (default: Asia/Shanghai)
# Set top-level `timezone` in ~/.sanfuclaw/config.json or sanfuclaw.toml
```

### Run as a background service

For daily use, run the installed entry point under a process manager so the
agent survives logout and auto-restarts on failure:

```bash
# macOS / Linux — quick foreground check first
sanfuclaw start --channel all     # messaging channels (Telegram, WeChat, …)
sanfuclaw serve                   # WebChat + REST + WebSocket gateway

# Then wire both commands into systemd (Linux) or launchd (macOS).
```

See [docs/deployment.md](docs/deployment.md) for the full setup: venv layout,
user-level systemd units with `loginctl enable-linger`, launchd `.plist`
files, logging, auto-restart, and config-change restarts.

## Architecture

Sanfuclaw follows a **hub-and-spoke** design: channels produce/consume messages,
the Router dispatches them to an Agent, and the Agent streams responses back
through the same channel. Tools, skills, and MCP servers all plug into a single
`ToolRegistry` so the LLM sees one unified tool surface.

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

### Message flow

1. A **Channel** receives input from its platform and yields an `Envelope`
   (message + source channel) on its `receive()` async iterator.
2. The **Router** resolves a `Session` (persisted in SQLite) and a target
   `Agent`, then calls `agent.process(envelope, session)`.
3. The **LLM Agent** builds a provider-specific message list, calls the
   **Transport**, and streams `TEXT_DELTA` / `TOOL_USE` / `USAGE` chunks.
4. Tool calls are dispatched through the **ToolRegistry** (local tools,
   `load_skill`, or MCP adapters), and their results are fed back into the LLM
   for up to 5 tool rounds.
5. Text chunks stream to the channel via `channel.send(session_id, chunk,
   streaming=True)`; a final `done=True` call flushes any channel-level buffer
   (used by Telegram/WeChat which cannot render real-time partials).
6. The Router persists the session and new messages through the
   `SessionManager`.

### Key components

| Component | Description |
|-----------|-------------|
| **Gateway** | FastAPI app, WebSocket/HTTP endpoints, `Router` + `SessionManager` |
| **Router** | Resolves session+agent, runs the streaming loop, persists messages |
| **Agents** | `LLMAgent` — multi-round tool calling, history trimming, usage tracking |
| **Transports** | `AnthropicTransport`, `OpenAICompatTransport` with retry + usage chunks |
| **Channels** | `CLIChannel`, `TelegramChannel`, `WeixinChannel`, `WSChannel` (webchat) |
| **Tools** | `ShellTool`, `WebFetchTool`, `LoadSkillTool`, MCP adapters — all share one registry |
| **Skills** | `SkillRegistry` loads `*.md` files with frontmatter, injects summary into the system prompt |
| **MCP** | `MCPManager` + `MCPToolAdapter` — spawn stdio servers or connect over SSE, expose tools |
| **Storage** | `SQLiteStore` — sessions + messages + pluggable event hooks |

### Design notes

- **Protocol-driven extensibility.** `Channel`, `Tool`, `Agent`, and
  `LLMTransport` are all `typing.Protocol` interfaces — no base classes to
  inherit. Adding a new platform is ~80 lines; adding a new tool is ~20.
- **One Envelope, many channels.** The Router is channel-agnostic; a
  per-channel `session_id` convention (`tg-<chat>`, `wx-<user>`, `ws-<conn>`,
  `cli-session`) lets the same storage/agent pipeline serve all of them.
- **History trimming at the agent.** `LLMAgent` slices the last N messages
  (default 20) when building the prompt without mutating `session.history`,
  so long-running sessions don't inflate token cost and the persisted record
  stays complete.
- **Usage tracking is first-class.** `StreamChunk` has a `USAGE` variant, and
  the OpenAI-compat transport de-duplicates providers that repeat usage in
  every chunk. The agent stashes a per-turn trace (tool calls + total in/out
  tokens) on `agent.last_trace`; the router delivers it out-of-band only to
  channels that opt in via `wants_trace = True` (today, just CLI). User-facing
  channels stay clean.
- **Platform limitations are respected.** Telegram and WeChat can't render
  partial tokens, so their channels buffer the full response and send on
  `done=True`. CLI and WebChat stream in real time. All channels use the same
  `send(session_id, chunk, streaming=True/done=True)` API.
- **Skills are lazy.** The system prompt lists only `name: description` for
  each skill; the LLM calls `load_skill(name)` to pull the full instructions
  only when relevant. This keeps the system prompt small even with dozens of
  skills installed.
- **MCP tools are just tools.** `MCPManager` starts every configured server on
  launch, wraps each discovered tool in an `MCPToolAdapter` (name sanitized to
  `mcp_<server>_<tool>`), and registers them into the same `ToolRegistry` the
  local tools use. The agent's tool loop is unchanged.

### Skills

A skill is a markdown file with frontmatter:

```markdown
---
name: weather-report
description: Format a friendly weather report for a given city.
---

When the user asks about weather in a city:
1. Fetch https://wttr.in/<city>?format=j1
2. Reply in the format: ...
```

Drop it under the directory set by `[skills] dir` in `sanfuclaw.toml`. On
startup the `SkillRegistry` scans the directory, injects a summary into the
system prompt, and registers the `load_skill` tool. The LLM retrieves the
full body on demand.

### MCP

Declare servers under `[mcp.servers.<name>]`. Either spawn over stdio:

```toml
[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

…or connect to an SSE endpoint:

```toml
[mcp.servers.remote]
url = "https://example.com/mcp/sse"
```

The MCP client is bundled with the base install — no extra step. Tools appear
in the registry as `mcp_<server>_<tool>`.

#### Recommended servers

Zero-auth official reference servers — copy any of these into your
`sanfuclaw.toml` under `[mcp.servers.<name>]`:

```toml
# Sandboxed filesystem access (reads/writes under the given directory)
[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

# Structured git operations (log, diff, blame, show) — requires `uvx`
[mcp.servers.git]
command = "uvx"
args = ["mcp-server-git", "--repository", "."]

# Timezone-aware current time — fixes "what's today's date" confusion
[mcp.servers.time]
command = "uvx"
args = ["mcp-server-time"]

# Structured step-by-step reasoning tool — boosts hard-problem solving
[mcp.servers.sequential-thinking]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-sequential-thinking"]

# Fetch URLs with robots.txt handling + markdown conversion
[mcp.servers.fetch]
command = "uvx"
args = ["mcp-server-fetch"]
```

Servers needing API keys or tokens (add `env = { KEY = "..." }` or export
the variable before starting sanfuclaw):

- **github** — `npx @modelcontextprotocol/server-github` (needs `GITHUB_TOKEN`)
- **exa** / **brave-search** — web search (needs API key)
- **playwright** — `npx @playwright/mcp` — real browser automation

#### Enable only what you need

Every registered MCP tool becomes part of the `tools=` payload sent on
**every** LLM turn — not just the turn where it gets called. As a rough
guide: `filesystem` alone adds ~2.5k input tokens per call, `git` adds
~2k, and enabling all five recommended servers costs ~5k tokens on every
round of every conversation.

Mitigations:

1. **Enable only the servers you actually use for a given session.**
   Any server can be temporarily disabled without removing its config:
   ```toml
   [mcp.servers.filesystem]
   enabled = false
   command = "npx"
   args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
   ```
2. **Prompt caching is on by default for Anthropic.** The system prompt
   and the entire tools block are marked `cache_control: ephemeral`, so
   repeat turns pay ~10% of the nominal token cost for that prefix. A
   conversation that would have cost 5k tokens/turn for tools drops to
   ~500/turn after the first hit.
3. **OpenAI-compatible providers** (OpenAI, DeepSeek, Moonshot/Kimi, etc.)
   apply prompt caching automatically — there is no request-side flag.
   The transport reads cache-hit counts from the common usage-response
   fields (`prompt_tokens_details.cached_tokens`, `prompt_cache_hit_tokens`,
   `cached_tokens`) and surfaces them in the per-turn trace, so you can
   see caching kick in as `(N cached)` next to the input token count.

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/status` | Server status |
| `GET /api/sessions` | List sessions |
| `GET /api/sessions/{id}/messages` | Session history |
| `WS /ws` | WebSocket chat |
| `GET /` | WebChat UI |

### Extensibility

All key interfaces use `typing.Protocol` for structural subtyping:

- **Channel**: Implement `start()`, `stop()`, `send()`, `send_typing()`, `receive()` to add a new platform
- **Tool**: Implement `name`, `description`, `parameters_schema`, `execute()` to add a new tool
- **LLMTransport**: Implement `complete()` to add a new LLM provider
- **Skill**: Drop a `.md` file with frontmatter into the skills directory — no code required
- **MCP server**: Add an entry to `[mcp.servers.*]` — tools are auto-discovered

## Project Structure

```
src/sanfuclaw/
  core/         # Message, Session, Config, Types, Errors
  gateway/      # Router, Server, SessionManager, Wiring (factory), Hooks
  agents/       # Agent protocol, LLM agent
    transports/ # Anthropic, OpenAI-compatible
  channels/     # CLI, Telegram, WeChat, WebChat
  tools/        # Shell, WebFetch, LoadSkill, Registry
  skills/       # Skill dataclass, markdown SkillRegistry
  mcp_client/   # MCPManager, MCPToolAdapter
  storage/      # SQLite backend
  webchat/      # Browser chat UI
skills/         # User skill library (*.md)
```

## Contributing

```bash
pip install -e ".[dev]"   # adds pytest, pytest-asyncio, ruff, mypy
pytest
ruff check src/
```

## License

MIT
