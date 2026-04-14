# Sanfuclaw

A local-first personal AI agent inspired by [OpenClaw](https://github.com/openclaw/openclaw), built in Python.

## Features

- **Multi-channel**: CLI, Telegram, WeChat (iLink Bot), WebChat (browser), WebSocket API
- **Multi-provider LLM**: Anthropic Claude, OpenAI-compatible APIs (HPC-AI, vLLM, Ollama, etc.)
- **Tool system**: Shell commands, web fetch, extensible tool registry
- **Skill plugins**: Markdown-based skills with frontmatter, lazy-loaded on demand
- **MCP support**: Connect to any Model Context Protocol server over stdio or SSE
- **Persistent sessions**: SQLite-backed conversation history
- **Gateway server**: FastAPI with WebSocket streaming, REST API, WebChat UI
- **Event hooks**: Pluggable event system for custom integrations
- **Streaming-first**: Real-time response streaming across all channels

## Quick Start

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# For Telegram support
pip install -e ".[telegram]"
```

### Configure

Edit `sanfuclaw.toml`:

```toml
[llm]
provider = "openai_compat"          # or "anthropic"
model = "minimax/minimax-m2.5"
base_url = "https://api.hpc-ai.com/inference/v1"
api_key = "your-api-key"

# Optional: Telegram bot
[channels.telegram]
type = "telegram"
bot_token = "YOUR_BOT_TOKEN"
```

Or set API keys via environment variables:

```bash
export LLM_API_KEY="your-key"
export TELEGRAM_BOT_TOKEN="your-bot-token"
```

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
