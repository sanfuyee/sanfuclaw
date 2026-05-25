"""SystemPromptBuilder — assemble the system prompt from named sections.

The agent's system prompt is built from several independent blocks (base
persona, schedule guidance, tool-use efficiency, planning, web research,
memory index, …). Building it via f-string concatenation makes it hard to
log how big each piece is and easy to reorder by accident. This builder
keeps the sections ordered, named, and inspectable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SystemPromptBuilder:
    """Ordered, named system-prompt sections.

    Sections are joined with a blank line. Empty bodies are skipped so a
    disabled section (e.g. memory when no entries exist) doesn't leave
    a trailing blank.
    """

    _sections: list[tuple[str, str]] = field(default_factory=list)

    def add(self, name: str, body: str) -> "SystemPromptBuilder":
        """Append a section. Empty/whitespace-only bodies are dropped."""
        if body and body.strip():
            self._sections.append((name, body.strip()))
        return self

    def render(self) -> str:
        return "\n\n".join(body for _, body in self._sections)

    def section_sizes(self) -> list[tuple[str, int]]:
        """(name, char_count) per section — handy for startup logging."""
        return [(name, len(body)) for name, body in self._sections]

    def log_summary(self, label: str = "system_prompt") -> None:
        """Emit one INFO log line summarizing the assembled prompt size."""
        sizes = self.section_sizes()
        total = sum(n for _, n in sizes)
        breakdown = ", ".join(f"{name}={n}" for name, n in sizes)
        logger.info(
            "%s assembled: %d chars across %d section(s) [%s]",
            label, total, len(sizes), breakdown,
        )
