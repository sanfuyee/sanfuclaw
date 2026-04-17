# Sanfuclaw

A local-first personal AI agent inspired by [OpenClaw](https://github.com/openclaw/openclaw), built in Python.

## Features

- **Multi-channel**: CLI, Telegram, WeChat (iLink Bot), WebChat (browser), WebSocket API
- **Multi-provider LLM**: Anthropic Claude, OpenAI-compatible APIs (HPC-AI, vLLM, Ollama, etc.)
- **Tool system**: Shell commands, web fetch, extensible tool registry
- **Skill plugins**: Markdown-based skills with frontmatter, lazy-loaded on demand
- **MCP support**: Connect to any Model Context Protocol server over stdio or SSE
- **Persistent sessions**: SQLite-backed conversation history with list/resume support
- **Gateway server**: FastAPI with WebSocket streaming, REST API, WebChat UI
- **Event hooks**: Pluggable event system for custom integrations
- **Streaming-first**: Real-time response streaming across all channels

## Quick Start

### Install

```bash
# Install from the repo (editable, with dev extras)
pip install -e ".[dev]"

# Optional extras
pip install -e ".[telegram]"   # Telegram channel
pip install -e ".[weixin]"     # WeChat channel
pip install -e ".[mcp]"        # MCP servers
```

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
sanfuclaw uninstall --purge      # remove ~/.sanfuclaw/ AND the Python package
sanfuclaw uninstall              # remove ~/.sanfuclaw/ only
sanfuclaw uninstall --keep-config  # drop data, keep config.json
pip uninstall sanfuclaw          # remove only the package (leaves ~/.sanfuclaw/)
```

`pip install` / `pip uninstall` alone can't touch files outside the
package directory (Python packaging has no post-install or pre-uninstall
hooks), so the user-data dance lives in `sanfuclaw uninstall`.
`--purge` wraps both steps into one command.

### Run

```bash
# CLI chat mode
python -m sanfuclaw start

# Telegram bot
python -m sanfuclaw start --channel telegram

# Both CLI + Telegram
python -m sanfuclaw start --channel all

# Gateway server (WebChat + REST API + WebSocket)
python -m sanfuclaw serve

# Session management
python -m sanfuclaw sessions              # List recent sessions
python -m sanfuclaw sessions -n 20        # Show more sessions
python -m sanfuclaw sessions --channel cli # Filter by channel
python -m sanfuclaw start --resume <ID>   # Resume a session (prefix match)
```

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
- **History trimming at the agent.** `LLMAgent` caps `session.history` to the
  last N messages (default 20) before building the prompt, so long-running
  sessions don't inflate token cost.
- **Usage tracking is first-class.** `StreamChunk` has a `USAGE` variant, and
  the OpenAI-compat transport de-duplicates providers that repeat usage in
  every chunk. The agent appends a per-turn trace (tool calls + total in/out
  tokens) to the response so you can see what happened.
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

Install the extra: `pip install -e ".[mcp]"`. Tools appear in the registry
as `mcp_<server>_<tool>`.

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
  gateway/      # Router, Server, SessionManager, Hooks
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

## License

MIT
