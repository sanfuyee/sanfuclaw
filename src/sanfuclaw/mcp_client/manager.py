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

    Keeps each server's stdio/SSE transport and ClientSession alive via a
    single AsyncExitStack for the lifetime of the app.
    """

    def __init__(self, servers: dict[str, MCPServerConfig]):
        self._servers = servers
        self._stack = AsyncExitStack()
        self._sessions: dict[str, "ClientSession"] = {}
        self._tools: list[tuple[str, object]] = []  # (server_name, mcp.Tool)
        self._started = False

    async def start(self) -> None:
        """Connect to every enabled server and list its tools."""
        if self._started:
            return
        self._started = True
        await self._stack.__aenter__()

        for name, cfg in self._servers.items():
            if not cfg.enabled:
                continue
            try:
                session = await self._connect(cfg)
                self._sessions[name] = session
                result = await session.list_tools()
                for tool in result.tools:
                    self._tools.append((name, tool))
                logger.info(
                    f"MCP server '{name}' connected — {len(result.tools)} tool(s)"
                )
            except Exception as e:
                logger.error(f"MCP server '{name}' failed to start: {e}")

    async def _connect(self, cfg: MCPServerConfig) -> "ClientSession":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if cfg.url:
            from mcp.client.sse import sse_client
            transport = await self._stack.enter_async_context(sse_client(cfg.url))
        else:
            if not cfg.command:
                raise ValueError("MCP server needs either `url` or `command`")
            params = StdioServerParameters(
                command=cfg.command,
                args=list(cfg.args),
                env=dict(cfg.env) or None,
            )
            transport = await self._stack.enter_async_context(stdio_client(params))

        read, write = transport
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await self._stack.aclose()
        except Exception as e:
            logger.warning(f"MCP manager shutdown error: {e}")
        self._sessions.clear()
        self._tools.clear()
        self._started = False

    def tools(self) -> list[tuple[str, object]]:
        """Return (server_name, mcp_tool) pairs for all discovered tools."""
        return list(self._tools)

    def get_session(self, server_name: str) -> "ClientSession":
        return self._sessions[server_name]
