"""Skill registry — discovers and loads skills from a directory."""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Skill

logger = logging.getLogger(__name__)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a YAML-ish frontmatter block.

    Only supports simple `key: value` lines — sufficient for skill metadata.
    Returns (metadata, body).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    meta: dict[str, str] = {}
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
        line = lines[i]
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")

    if end == -1:
        return {}, text

    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


class SkillRegistry:
    """Discovers markdown skills from one or more directories."""

    def __init__(self, skill_dir: str | Path | None = None):
        self._skills: dict[str, Skill] = {}
        if skill_dir:
            self.load_dir(skill_dir)

    def load_dir(self, directory: str | Path) -> None:
        """Load every `*.md` file under `directory` as a skill."""
        d = Path(directory).expanduser()
        if not d.exists():
            logger.info(f"Skill directory {d} does not exist — skipping")
            return

        for md in sorted(d.rglob("*.md")):
            try:
                self._load_file(md)
            except Exception as e:
                logger.warning(f"Failed to load skill {md}: {e}")

    def _load_file(self, path: Path) -> None:
        text = path.read_text()
        meta, body = _parse_frontmatter(text)
        name = meta.get("name") or path.stem
        description = meta.get("description", "")
        skill = Skill(
            name=name,
            description=description,
            instructions=body,
            path=path,
        )
        self._skills[name] = skill
        logger.info(f"Loaded skill: {name} from {path}")

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    def system_prompt_block(self) -> str:
        """Render a summary of available skills for injection into the system prompt."""
        if not self._skills:
            return ""
        lines = [
            "",
            "## Available skills",
            "",
            "You have access to the following skills. Each skill is a set of detailed "
            "instructions for a specific task. When the user's request matches a skill, "
            "call the `load_skill` tool with the skill name to retrieve its full "
            "instructions, then follow them.",
            "",
        ]
        for s in self._skills.values():
            desc = s.description or "(no description)"
            lines.append(f"- **{s.name}**: {desc}")
        return "\n".join(lines)
