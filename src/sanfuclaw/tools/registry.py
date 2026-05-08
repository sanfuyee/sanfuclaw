"""Tool registry — manages available tools and generates LLM-compatible schemas."""

from __future__ import annotations

import logging
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session

from .base import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registers tools and resolves them by name."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._validators: dict[str, Draft202012Validator] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        """Register a tool. Raises ValueError if a tool with the same name
        is already registered, unless `replace=True` is passed.

        The duplicate guard catches silent shadowing — most often when an MCP
        server exposes a tool whose sanitized name collides with another
        MCP tool or a local tool.
        """
        if tool.name in self._tools and not replace:
            raise ValueError(
                f"Tool {tool.name!r} is already registered "
                f"(existing: {type(self._tools[tool.name]).__name__}, "
                f"new: {type(tool).__name__}). "
                "Pass replace=True to override."
            )
        self._tools[tool.name] = tool
        # Pre-compile a validator if the tool ships a schema. Compilation can
        # fail if the schema itself is malformed — log and skip rather than
        # blocking registration; execute() will then run without validation.
        schema = getattr(tool, "parameters_schema", None)
        if schema:
            try:
                self._validators[tool.name] = Draft202012Validator(schema)
            except jsonschema.SchemaError as e:
                logger.warning(
                    "Tool %r has invalid parameters_schema, skipping validation: %s",
                    tool.name, e,
                )
                self._validators.pop(tool.name, None)
        else:
            self._validators.pop(tool.name, None)

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
        validator = self._validators.get(name)
        if validator is not None:
            errors = sorted(validator.iter_errors(params), key=lambda e: e.path)
            if errors:
                # Surface the first error in human-readable form. The LLM reads
                # this and is expected to retry with corrected arguments.
                first = errors[0]
                path = ".".join(str(p) for p in first.path) or "<root>"
                raise ToolError(
                    f"Invalid arguments for {name}: {first.message} (at {path})"
                )
        return await tool.execute(params, session)
