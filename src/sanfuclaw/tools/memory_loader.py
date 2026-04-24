"""load_memory tool — returns the full body of a named memory entry."""

from __future__ import annotations

from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.memory.registry import MemoryRegistry


class LoadMemoryTool:
    """Fetch the full body of a memory entry by name."""

    name = "load_memory"
    description = (
        "Load the full body of a memory entry listed in the Memory section of "
        "the system prompt.  Call this whenever the user's request may be "
        "informed by a saved note; don't act on a memory entry from the index "
        "summary alone — read the full body first."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The memory entry name, as listed in the Memory index.",
            },
        },
        "required": ["name"],
    }

    def __init__(self, registry: MemoryRegistry):
        self._registry = registry

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        name = params.get("name", "")
        if not name:
            raise ToolError("No memory name provided")

        entry = self._registry.get(name)
        if not entry:
            available = ", ".join(e.name for e in self._registry.list_all()) or "(none)"
            raise ToolError(f"Unknown memory entry: {name}. Available: {available}")

        body = entry.body.strip() or "(empty memory entry)"
        return f"# Memory: {entry.name}\n\n{body}"
