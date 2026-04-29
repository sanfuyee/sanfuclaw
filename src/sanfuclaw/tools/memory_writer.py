"""save_memory / update_memory / forget_memory tools.

These let the LLM curate its persistent notes during a conversation,
following the auto-memory pattern: each entry is a markdown file with
optional frontmatter, and ``MEMORY.md`` at the directory root is the
hand-curated (or LLM-curated) index injected into every turn's system
prompt.

Writes go through ``MemoryRegistry`` so the in-memory state stays in
sync with disk; ``load_memory`` on the next turn sees the new entry
without a process restart.
"""

from __future__ import annotations

from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.memory.registry import MemoryRegistry, MemoryWriteError


_VALID_TYPES = {"user", "feedback", "project", "reference", ""}


class SaveMemoryTool:
    """Create a new memory entry."""

    name = "save_memory"
    description = (
        "Save a new memory entry. Use this when the user asks you to "
        "remember something, or when you learn a fact / preference / "
        "context that will be useful in future conversations.\n\n"
        "Pick a short slug-like name (a-z, 0-9, _ or -). The body is the "
        "markdown content of the entry; description is the one-liner shown "
        "in the MEMORY index. Type is optional but encouraged for "
        "categorization: user / feedback / project / reference."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Slug-like entry name (a-z, 0-9, _ or -; max 64 chars).",
            },
            "description": {
                "type": "string",
                "description": "One-line hook shown in the MEMORY index.",
            },
            "body": {
                "type": "string",
                "description": "Full markdown body of the entry.",
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "Category — see save_memory description.",
            },
        },
        "required": ["name", "body"],
    }

    def __init__(self, registry: MemoryRegistry):
        self._registry = registry

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        name = str(params.get("name", "")).strip()
        body = str(params.get("body", "")).strip()
        description = str(params.get("description", "")).strip()
        type_ = str(params.get("type", "")).strip()
        if type_ and type_ not in _VALID_TYPES:
            raise ToolError(
                f"Invalid memory type {type_!r}; must be one of "
                f"{sorted(_VALID_TYPES - {''})}"
            )
        if not body:
            raise ToolError("Memory body is empty")
        try:
            entry = self._registry.save_entry(
                name=name, description=description, body=body, type_=type_,
            )
        except MemoryWriteError as e:
            raise ToolError(str(e)) from e
        return f"Saved memory {entry.name!r} → {entry.path}"


class UpdateMemoryTool:
    """Rewrite an existing memory entry."""

    name = "update_memory"
    description = (
        "Update an existing memory entry's body. Use this when a saved "
        "memory turns out to be wrong, incomplete, or outdated."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Existing entry name."},
            "body": {"type": "string", "description": "New full body."},
            "description": {
                "type": "string",
                "description": "Optional new description (omit to keep existing).",
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "Optional new type.",
            },
        },
        "required": ["name", "body"],
    }

    def __init__(self, registry: MemoryRegistry):
        self._registry = registry

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        name = str(params.get("name", "")).strip()
        body = str(params.get("body", "")).strip()
        description = params.get("description")
        type_ = params.get("type")
        if not body:
            raise ToolError("Memory body is empty")
        if type_ is not None and type_ not in _VALID_TYPES:
            raise ToolError(
                f"Invalid memory type {type_!r}; must be one of "
                f"{sorted(_VALID_TYPES - {''})}"
            )
        try:
            entry = self._registry.update_entry(
                name=name,
                body=body,
                description=description if description is not None else None,
                type_=type_ if type_ is not None else None,
            )
        except MemoryWriteError as e:
            raise ToolError(str(e)) from e
        return f"Updated memory {entry.name!r}"


class ForgetMemoryTool:
    """Delete a memory entry."""

    name = "forget_memory"
    description = (
        "Delete a memory entry. Use sparingly — only when the user asks "
        "to forget something, or when an entry has clearly become "
        "irrelevant. The entry file and its MEMORY.md index line are "
        "both removed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Entry name to delete."},
        },
        "required": ["name"],
    }

    def __init__(self, registry: MemoryRegistry):
        self._registry = registry

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        name = str(params.get("name", "")).strip()
        try:
            removed = self._registry.forget_entry(name)
        except MemoryWriteError as e:
            raise ToolError(str(e)) from e
        if not removed:
            raise ToolError(f"No such memory entry: {name}")
        return f"Forgot memory {name!r}"
