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
- **Streaming-first**: Real-time response streaming across all channels

## Install

### macOS — Homebrew (recommended)

```bash
brew tap sanfuyee/sanfuclaw
brew install sanfuclaw
sanfuclaw setup        # interactive wizard — pick provider, paste API key, enable channels
sanfuclaw start        # chat in the terminal
```

`brew upgrade sanfuclaw` later picks up new releases. No Gatekeeper
"unverified developer" warnings.

### Linux / Windows — direct download

1. Grab the binary for your platform from the
   [Releases page](https://github.com/sanfuyee/sanfuclaw/releases):
   - Linux (x86_64): `sanfuclaw-linux-x86_64`
   - Windows: `sanfuclaw-windows-x86_64.exe`
2. Make it runnable and put it on `PATH`:
   ```bash
   chmod +x ~/Downloads/sanfuclaw-linux-x86_64
   sudo mv ~/Downloads/sanfuclaw-linux-x86_64 /usr/local/bin/sanfuclaw
   ```
   On Windows, rename to `sanfuclaw.exe` and put it on `PATH` (or run it
   from the download folder directly).
3. `sanfuclaw setup` then `sanfuclaw start`.

### macOS — direct download (without Homebrew)

Same flow as Linux, with `sanfuclaw-macos-arm64` (Apple Silicon only).
The binary is unsigned, so the first launch needs **Right-click → Open
→ Open** to clear Gatekeeper. One-time. Homebrew avoids this entirely,
which is why it's the recommended macOS path.

Intel Macs aren't covered by the prebuilt binaries — install from
source: see [docs/developers.md](docs/developers.md).

### About the wizard

`sanfuclaw setup` handles LLM provider (HPC-AI, Moonshot, DeepSeek,
OpenAI, Anthropic, or local Ollama), API key, messaging channels,
optional MCP tools, and background autostart. Re-run it anytime to
change settings — your existing config is backed up automatically.

## Configure

The wizard writes everything you need. To tweak by hand later, edit
`~/.sanfuclaw/config.json`:

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

Or set secrets via env vars:

```bash
export SANFUCLAW_LLM__API_KEY="your-key"
export TELEGRAM_BOT_TOKEN="your-bot-token"
```

Config resolution order: `--config <path>` → `$SANFUCLAW_CONFIG` →
`~/.sanfuclaw/config.json` → `./sanfuclaw.toml` (legacy). Override the
home directory with `$SANFUCLAW_HOME`. Every knob with inline comments:
[`config.example.json`](config.example.json).

## Run

```bash
sanfuclaw start                       # chat in the terminal
sanfuclaw start --channel telegram    # one channel
sanfuclaw start --channel all         # all configured channels
sanfuclaw serve                       # WebChat UI + REST + WebSocket gateway

sanfuclaw sessions                    # list recent sessions
sanfuclaw start --resume <ID>         # resume a session (prefix match)

sanfuclaw cron add "0 8 * * *" --channel telegram --prompt "今日待办"
sanfuclaw cron list
```

For background services (systemd / launchd), live log streaming, scheduled
tasks, and deployment notes, see [docs/operations.md](docs/operations.md).

## Uninstall

1. Stop any background service first:
   ```bash
   sanfuclaw service uninstall
   ```
2. Delete the binary:
   ```bash
   brew uninstall sanfuclaw                  # if installed via Homebrew
   rm /usr/local/bin/sanfuclaw               # if installed directly
   ```
3. *(Optional)* Drop user data:
   ```bash
   rm -rf ~/.sanfuclaw/
   ```

User data is kept by default so a reinstall picks up where you left off.

> Source install (`pip install -e .`)? `pip uninstall sanfuclaw` instead
> of step 2 — see [docs/developers.md](docs/developers.md).

## Architecture

Hub-and-spoke: channels produce/consume messages, the Router dispatches
them to an Agent, and the Agent streams responses back through the same
channel. Tools, skills, and MCP servers all plug into one
`ToolRegistry` so the LLM sees a unified surface.

Full design (data flow, protocols, key components, design decisions) is
documented separately — this README stays focused on install and
day-to-day use.

## More docs

- [docs/architecture.md](docs/architecture.md) — internals, hub-and-spoke
  layout, design decisions
- [docs/concepts.md](docs/concepts.md) — primer on `system_prompt` /
  tools / skills / memory / history (what enters the LLM prompt each turn)
- [docs/operations.md](docs/operations.md) — background services, logs,
  scheduled tasks, deployment notes
- [docs/mcp.md](docs/mcp.md) — MCP server config, recommended bundle,
  token-cost trade-offs
- [docs/developers.md](docs/developers.md) — building from source,
  tests, lint, cutting a release
- [docs/installer-p0.md](docs/installer-p0.md) — design of the setup
  wizard + PyInstaller release pipeline

## License

MIT
