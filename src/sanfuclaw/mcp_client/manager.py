"""MCP Manager — connects to configured MCP servers and exposes their tools."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from sanfuclaw.core.config import MCPServerConfig

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages MCP client sessions across multiple configured servers.

    Each server's stdio/SSE transport + ClientSession lives in its own
    AsyncExitStack so we can tear down + rebuild a single server (for
    reconnect) without disturbing the others.
    """

    def __init__(self, servers: dict[str, MCPServerConfig]):
        self._servers = servers
        self._sessions: dict[str, "ClientSession"] = {}
        # Per-server stacks let us reconnect one server at a time.
        self._stacks: dict[str, AsyncExitStack] = {}
        self._tools: list[tuple[str, object]] = []  # (server_name, mcp.Tool)
        self._started = False

    async def start(self) -> None:
        """Connect to every enabled server and list its tools."""
        if self._started:
            return
        self._started = True

        for name, cfg in self._servers.items():
            if not cfg.enabled:
                continue
            try:
                await self._spin_up(name, cfg)
            except Exception as e:
                logger.error(f"MCP server '{name}' failed to start: {e}")

    async def _spin_up(self, name: str, cfg: MCPServerConfig) -> None:
        """Connect one server and record its tools. Caller handles errors."""
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            session = await self._connect(stack, cfg)
        except Exception:
            await stack.aclose()
            raise
        self._stacks[name] = stack
        self._sessions[name] = session

        result = await session.list_tools()
        for tool in result.tools:
            self._tools.append((name, tool))
        logger.info(
            f"MCP server '{name}' connected — {len(result.tools)} tool(s)"
        )

    async def _connect(
        self, stack: AsyncExitStack, cfg: MCPServerConfig
    ) -> "ClientSession":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if cfg.url:
            from mcp.client.sse import sse_client
            transport = await stack.enter_async_context(sse_client(cfg.url))
        else:
            if not cfg.command:
                raise ValueError("MCP server needs either `url` or `command`")
            params = StdioServerParameters(
                command=cfg.command,
                args=list(cfg.args),
                env=dict(cfg.env) or None,
            )
            transport = await stack.enter_async_context(stdio_client(params))

        read, write = transport
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def reconnect(self, server_name: str) -> "ClientSession | None":
        """Tear down and re-establish a single server's session.

        Used by MCPToolAdapter when a tool call fails — gives long-running
        daemons a chance to recover from a server crash without restarting.
        Returns the new session, or None if reconnect failed (caller should
        surface the original error).
        """
        cfg = self._servers.get(server_name)
        if cfg is None or not cfg.enabled:
            return None

        # Drop the old session+stack first; ignore close errors since the
        # session is presumed dead anyway.
        old_stack = self._stacks.pop(server_name, None)
        self._sessions.pop(server_name, None)
        if old_stack is not None:
            try:
                await old_stack.aclose()
            except Exception as e:
                logger.debug("Discarding old MCP stack for %s: %s", server_name, e)

        try:
            await self._spin_up(server_name, cfg)
            return self._sessions.get(server_name)
        except Exception as e:
            logger.error("MCP reconnect failed for %s: %s", server_name, e)
            return None

    async def stop(self) -> None:
        if not self._started:
            return
        for name, stack in list(self._stacks.items()):
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning(f"MCP manager shutdown error for {name}: {e}")
        self._stacks.clear()
        self._sessions.clear()
        self._tools.clear()
        self._started = False

    def tools(self) -> list[tuple[str, object]]:
        """Return (server_name, mcp_tool) pairs for all discovered tools."""
        return list(self._tools)

    def get_session(self, server_name: str) -> "ClientSession":
        return self._sessions[server_name]
