"""load_skill tool — returns full instructions for a named skill."""

from __future__ import annotations

from typing import Any

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.skills.registry import SkillRegistry


class LoadSkillTool:
    """Fetch the full instructions of a skill by name."""

    name = "load_skill"
    description = (
        "Load the full instructions for one of the available skills listed in the "
        "system prompt. Call this whenever the user's request matches a skill, "
        "then follow the returned instructions."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name, exactly as listed in the system prompt.",
            },
        },
        "required": ["name"],
    }

    def __init__(self, registry: SkillRegistry):
        self._registry = registry

    async def execute(self, params: dict[str, Any], session: Session) -> str:
        name = params.get("name", "")
        if not name:
            raise ToolError("No skill name provided")

        skill = self._registry.get(name)
        if not skill:
            available = ", ".join(s.name for s in self._registry.list_all()) or "(none)"
            raise ToolError(f"Unknown skill: {name}. Available: {available}")

        body = skill.instructions.strip() or "(empty skill)"
        return f"# Skill: {skill.name}\n\n{body}"
