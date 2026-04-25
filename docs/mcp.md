# MCP (Model Context Protocol)

Sanfuclaw bundles the official `mcp` SDK — declare a server in your
config and its tools appear in the unified tool registry as
`mcp_<server>_<tool>`. The agent's tool-calling loop is unchanged; the
LLM sees them as ordinary tools.

## Configure a server

Under `mcp.servers.<name>` in `~/.sanfuclaw/config.json`. Either spawn
over stdio:

```json
"mcp": {
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

…or connect to an SSE endpoint:

```json
"mcp": {
  "servers": {
    "remote": { "url": "https://example.com/mcp/sse" }
  }
}
```

Disable any server without removing its config:

```json
"filesystem": {
  "enabled": false,
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
}
```

## Recommended servers (zero auth)

Official reference servers — drop any of these into your config under
`mcp.servers.<name>`:

| Name | Command | Purpose |
|---|---|---|
| `filesystem` | `npx -y @modelcontextprotocol/server-filesystem /tmp` | Sandboxed read/write under the given dir |
| `git` | `uvx mcp-server-git --repository .` | Structured git ops (log, diff, blame, show) |
| `time` | `uvx mcp-server-time` | Timezone-aware current time |
| `sequential-thinking` | `npx -y @modelcontextprotocol/server-sequential-thinking` | Step-by-step reasoning helper |
| `fetch` | `uvx mcp-server-fetch` | URL fetcher with robots.txt + markdown conversion |

`npx`-based servers need [Node.js](https://nodejs.org/) on PATH;
`uvx`-based servers need [uv](https://github.com/astral-sh/uv).
`sanfuclaw setup` checks for both at startup and warns if missing.

## Servers needing auth

Pass secrets via `env = { KEY = "..." }` or export the variable before
starting sanfuclaw:

- **github** — `npx @modelcontextprotocol/server-github` (needs `GITHUB_TOKEN`)
- **exa** / **brave-search** — web search (needs API key)
- **playwright** — `npx @playwright/mcp` — real browser automation

## Token cost — enable only what you need

Every registered MCP tool becomes part of the `tools=` payload sent on
**every** LLM turn — not just the turn where it gets called. As a rough
guide: `filesystem` alone adds ~2.5k input tokens per call, `git` adds
~2k, and enabling all five recommended servers costs ~5k tokens on every
round of every conversation.

Mitigations:

1. **Enable only the servers you actually use for a given session.**
   Toggle one off via `enabled: false` rather than deleting its block.
2. **Prompt caching is on by default for Anthropic.** The system
   prompt and the entire tools block are marked
   `cache_control: ephemeral`, so repeat turns pay ~10% of the nominal
   token cost for that prefix. A conversation that would have cost 5k
   tokens/turn for tools drops to ~500/turn after the first hit.
3. **OpenAI-compatible providers** (OpenAI, DeepSeek, Moonshot/Kimi,
   etc.) apply prompt caching automatically — there is no request-side
   flag. The transport reads cache-hit counts from the common
   usage-response fields (`prompt_tokens_details.cached_tokens`,
   `prompt_cache_hit_tokens`, `cached_tokens`) and surfaces them in
   the per-turn trace, so you see caching kick in as `(N cached)`
   next to the input token count.

## How tools end up in the registry

`MCPManager.start()` enters every enabled server's stdio/SSE transport
under a shared `AsyncExitStack`, calls `initialize()` then `list_tools()`,
and wraps each tool in an `MCPToolAdapter` (name sanitized to
`mcp_<server>_<tool>` to satisfy Anthropic/OpenAI's `[A-Za-z0-9_-]`
constraint). All adapters get registered into the same `ToolRegistry`
the local tools use.

A per-server failure is logged but doesn't crash startup — the other
servers still come up. Shutdown is a single `await self._stack.aclose()`
which closes every transport in reverse registration order.

For deeper internals (lifecycle, schema forwarding, error handling) see
[docs/architecture.md § MCP](architecture.md#mcp-srcsanfuclawmcp_client).
