# Sanfuclaw

A local-first personal AI agent inspired by [OpenClaw](https://github.com/openclaw/openclaw), built in Python.

## Features

- **Multi-channel**: CLI, Telegram, WebChat (browser), WebSocket API
- **Multi-provider LLM**: Anthropic Claude, OpenAI-compatible APIs (HPC-AI, vLLM, Ollama, etc.)
- **Tool system**: Shell commands, web fetch, extensible tool registry
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

```
Channels (CLI, Telegram, WebChat)
              |
        +-----+-----+
        |  Gateway   |   (WebSocket + HTTP)
        |  Router    |
        +-----+------+
         /    |     \
      Agents Tools  Storage
      (LLM) (shell, (SQLite)
             fetch)
```

### Key Components

| Component | Description |
|-----------|-------------|
| **Gateway** | WebSocket/HTTP server, message routing, session management |
| **Agents** | LLM-backed message processing with streaming |
| **Channels** | Platform adapters (CLI, Telegram, WebChat) |
| **Tools** | Executable capabilities (shell, web fetch) |
| **Storage** | SQLite persistence for sessions and messages |
| **Transports** | LLM provider adapters (Anthropic, OpenAI-compatible) |

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

- **Channel**: Implement `start()`, `stop()`, `send()`, `receive()` to add a new platform
- **Tool**: Implement `name`, `description`, `parameters_schema`, `execute()` to add a new tool
- **LLMTransport**: Implement `complete()` to add a new LLM provider

## Project Structure

```
src/sanfuclaw/
  core/         # Message, Session, Config, Types
  gateway/      # Router, Server, SessionManager, Hooks
  agents/       # Agent protocol, LLM agent
    transports/ # Anthropic, OpenAI-compatible
  channels/     # CLI, Telegram, WebChat
  tools/        # Shell, WebFetch, Registry
  storage/      # SQLite backend
  webchat/      # Browser chat UI
```

## License

MIT
