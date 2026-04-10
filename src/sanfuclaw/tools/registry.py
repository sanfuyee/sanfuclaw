"""Tool registry — manages available tools and generates LLM-compatible schemas."""

from __future__ import annotations

from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session

from .base import Tool


class ToolRegistry:
    """Registers tools and resolves them by name."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def to_llm_schemas(self) -> list[dict]:
        """Generate tool schemas in the format expected by Claude's API."""
        schemas = []
        for tool in self._tools.values():
            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters_schema,
            })
        return schemas

    async def execute(self, name: str, params: dict[str, Any], session: Session) -> Any:
        tool = self.get(name)
        return await tool.execute(params, session)
