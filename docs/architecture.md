# Sanfuclaw Architecture Design

## Overview

Sanfuclaw is a **local-first personal AI agent** built in Python, inspired by [OpenClaw](https://github.com/openclaw/openclaw). It runs on your own devices, connects to messaging platforms, and uses LLMs (Claude, OpenAI) to process messages and execute tools.

## Architecture

### Hub-and-Spoke Model

```
Channels (Telegram, Discord, CLI, WebChat)
              ↓
        ┌─────────────┐
        │   Gateway    │  (WebSocket control plane)
        │   Router     │
        └─────────────┘
         /     |      \
        /      |       \
   Agents   Tools   Storage
   (LLM)   (shell,  (SQLite)
            fetch)
```

### Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Runtime | Python 3.12+ | Stable async, TaskGroup, modern typing |
| HTTP/WS Server | FastAPI + uvicorn | Async-native, WebSocket built-in |
| LLM SDKs | `anthropic`, `openai` | Official SDKs with streaming |
| Database | SQLite via `aiosqlite` | Zero-config, local-first |
| Config | TOML + Pydantic Settings | Python-native, validated |
| CLI | Typer | Clean CLI commands |
| Scheduling | APScheduler | Cron/interval tasks |
| Testing | pytest + pytest-asyncio | Standard |

### Core Abstractions

All key interfaces use `typing.Protocol` (structural subtyping) for maximum decoupling.

#### Message & Envelope
- `Message` — immutable dataclass with id, role, content, channel_id, session_id, sender_id, timestamp, metadata
- `Envelope` — wraps a Message with routing info (source_channel, target_agent, reply_to)

#### Channel Protocol
Any class with `name`, `start()`, `stop()`, `send()`, `receive()`, `send_typing()` is a valid Channel.
Channels are platform adapters (Telegram, Discord, CLI, WebChat).

#### Agent Protocol
Any class with `name` and `process(envelope, session) -> AsyncIterator[str]` is a valid Agent.
Agents process messages and stream back responses.

#### LLM Transport Protocol
Any class with `complete(messages, tools, model) -> AsyncIterator[StreamChunk]` is a valid transport.
Transports abstract LLM provider differences (Claude vs OpenAI vs local models).

#### Tool Protocol
Any class with `name`, `description`, `parameters_schema`, `execute(params, session)` is a valid Tool.
Tools give agents real-world capabilities (shell, web fetch, search).

#### Store Protocol
Any class with `save_message()`, `get_history()`, `save_session()`, `get_session()` is a valid Store.

### Data Flow

```
1. User sends message on Platform (e.g., Telegram)
2. Channel adapter receives → creates Envelope
3. Gateway Router resolves session → selects Agent
4. Agent streams response via LLM Transport
5. If tool_use → Agent executes Tool → feeds result back to LLM
6. Response chunks streamed back through Channel → Platform
```

### Design Decisions

1. **Single async process** initially — gateway, agents, channels as coroutines in one event loop. Split later via WebSocket if needed.
2. **Protocols over ABC** — no import/subclass needed for plugins.
3. **SQLite** — local-first, WAL mode for concurrent access. Store protocol allows swapping to Postgres.
4. **TOML config + env vars** — secrets via env vars, structure via `sanfuclaw.toml`.
5. **Streaming-first** — all agent responses are `AsyncIterator[str]`.
6. **Tool schemas as JSON Schema** — matches Claude/OpenAI tool-use API directly.
7. **JSON wire protocol** for WebSocket — simple, debuggable.
8. **No ORM** — raw SQL for 3-4 tables.

## Implementation Phases

| Phase | Goal | Key Deliverables |
|-------|------|------------------|
| 1 | Walking skeleton | CLI channel + Claude agent + router = chat in terminal |
| 2 | Tool system | Tool protocol, registry, shell & web_fetch tools |
| 3 | Persistence | SQLite store, session manager, history survives restarts |
| 4 | WebSocket gateway | FastAPI WS server, auth, JSON wire protocol |
| 5 | Telegram channel | First real platform integration |
| 6 | Discord channel | Validates Channel abstraction is generic |
| 7 | Security & hooks | DM pairing, allowlists, event hooks, skills |
| 8 | Polish | OpenAI transport, web search, cron, multi-agent routing |
