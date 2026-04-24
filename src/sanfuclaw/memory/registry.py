"""Memory registry — discovers markdown memory files from a directory.

Mirrors the Skill pattern but with one key difference: an optional
``MEMORY.md`` at the root is treated as a hand-curated index and
injected verbatim into the system prompt.  Individual ``*.md`` files
are loaded on demand via the ``load_memory`` tool, so the per-turn
prompt stays small regardless of how much memory accumulates.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sanfuclaw.skills.registry import _parse_frontmatter

from .base import MemoryEntry

logger = logging.getLogger(__name__)

_INDEX_FILENAME = "MEMORY.md"
_INDEX_MAX_LINES = 200


class MemoryRegistry:
    """Discovers markdown memory entries from a directory."""

    def __init__(self, memory_dir: str | Path | None = None):
        self._entries: dict[str, MemoryEntry] = {}
        self._index_body: str = ""
        self._dir: Path | None = None
        if memory_dir:
            self.load_dir(memory_dir)

    def load_dir(self, directory: str | Path) -> None:
        d = Path(directory).expanduser()
        self._dir = d
        if not d.exists():
            logger.info("Memory directory %s does not exist — skipping", d)
            return

        index_path = d / _INDEX_FILENAME
        if index_path.exists():
            self._index_body = self._read_index(index_path)

        for md in sorted(d.rglob("*.md")):
            if md.name == _INDEX_FILENAME:
                continue
            try:
                self._load_file(md)
            except Exception as e:
                logger.warning("Failed to load memory %s: %s", md, e)

    @staticmethod
    def _read_index(path: Path) -> str:
        text = path.read_text()
        # Truncate defensively: the index is loaded on every turn, so a
        # runaway MEMORY.md shouldn't silently blow up the system prompt.
        lines = text.splitlines()
        if len(lines) > _INDEX_MAX_LINES:
            lines = lines[:_INDEX_MAX_LINES] + [
                f"<!-- truncated: {_INDEX_FILENAME} exceeded {_INDEX_MAX_LINES} lines -->"
            ]
        return "\n".join(lines).strip()

    def _load_file(self, path: Path) -> None:
        text = path.read_text()
        meta, body = _parse_frontmatter(text)
        name = meta.get("name") or path.stem
        description = meta.get("description", "")
        entry = MemoryEntry(
            name=name,
            description=description,
            body=body if body else text,
            path=path,
        )
        self._entries[name] = entry
        logger.info("Loaded memory: %s from %s", name, path)

    def get(self, name: str) -> MemoryEntry | None:
        return self._entries.get(name)

    def list_all(self) -> list[MemoryEntry]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def dir(self) -> Path | None:
        return self._dir

    def system_prompt_block(self) -> str:
        """Render the index content for injection into the system prompt.

        Priority: the user-maintained ``MEMORY.md`` wins.  If it's absent
        but individual entries exist, fall back to an auto-generated list
        so the LLM at least knows what's available.  Either way, full
        bodies are only loaded on demand via ``load_memory``.
        """
        if self._index_body:
            return (
                "\n"
                "## Memory (persistent across sessions)\n"
                "\n"
                "The following notes were saved in previous conversations.  Call "
                "`load_memory(name)` to read the full body of any entry before "
                "acting on it.  Prefer this context over assumptions when the "
                "user's request overlaps.\n"
                "\n"
                f"{self._index_body}\n"
            )
        if not self._entries:
            return ""
        lines = [
            "",
            "## Memory (persistent across sessions)",
            "",
            "Saved notes from previous conversations.  Call `load_memory(name)` "
            "to read the full body of any entry before acting on it.",
            "",
        ]
        for entry in self._entries.values():
            desc = entry.description or "(no description)"
            lines.append(f"- **{entry.name}**: {desc}")
        return "\n".join(lines)
