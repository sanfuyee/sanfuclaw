"""Adapter that exposes an MCP tool via sanfuclaw's Tool protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session

if TYPE_CHECKING:
    from mcp import ClientSession


def _sanitize_name(raw: str) -> str:
    """Replace characters disallowed by Anthropic/OpenAI tool names."""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in raw)


class MCPToolAdapter:
    """Wraps a single MCP tool so the local ToolRegistry can dispatch it."""

    def __init__(self, server_name: str, mcp_tool: Any, session: "ClientSession"):
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._session = session

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
            result = await self._session.call_tool(self._mcp_tool.name, params)
        except Exception as e:
            raise ToolError(f"MCP tool '{self.name}' failed: {e}")

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
