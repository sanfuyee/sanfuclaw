"""Adapter that exposes an MCP tool via sanfuclaw's Tool protocol."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session

if TYPE_CHECKING:
    from sanfuclaw.mcp_client.manager import MCPManager

logger = logging.getLogger(__name__)


def _sanitize_name(raw: str) -> str:
    """Replace characters disallowed by Anthropic/OpenAI tool names."""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in raw)


class MCPToolAdapter:
    """Wraps a single MCP tool so the local ToolRegistry can dispatch it.

    The adapter holds a reference to the manager (not just a session) so a
    failed call can ask for a reconnect and retry once before surfacing an
    error to the LLM. Without this, a single MCP server crash would
    permanently break every tool from that server until the daemon restarts.
    """

    def __init__(self, server_name: str, mcp_tool: Any, manager: "MCPManager"):
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._manager = manager

        self.name = _sanitize_name(f"mcp_{server_name}_{mcp_tool.name}")
        self.description = (
            f"[MCP:{server_name}] {mcp_tool.description or mcp_tool.name}"
        )
        self.parameters_schema = mcp_tool.inputSchema or {
            "type": "object",
            "properties": {},
        }

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        try:
            return await self._call(params)
        except Exception as first_error:
            # First call failed — most often this means the MCP server died.
            # Try to reconnect once and retry; if that also fails, surface
            # the original error to the LLM as a ToolError.
            logger.warning(
                "MCP tool %r failed (%s); attempting reconnect to %r",
                self.name, first_error, self._server_name,
            )
            new_session = await self._manager.reconnect(self._server_name)
            if new_session is None:
                raise ToolError(f"MCP tool '{self.name}' failed: {first_error}")
            try:
                return await self._call(params)
            except Exception as second_error:
                raise ToolError(
                    f"MCP tool '{self.name}' failed after reconnect: {second_error}"
                )

    async def _call(self, params: dict[str, Any]) -> str:
        # Resolve the session every call — reconnect() swaps it in place,
        # and a stale reference would defeat the retry.
        session = self._manager.get_session(self._server_name)
        result = await session.call_tool(self._mcp_tool.name, params)

        parts: list[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
            else:
                parts.append(str(item))
        body = "\n".join(parts) if parts else "(no content)"

        if getattr(result, "isError", False):
            return f"[error] {body}"
        return body
