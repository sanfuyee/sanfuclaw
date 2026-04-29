"""Memory registry — discovers markdown memory files from a directory.

Mirrors the Skill pattern but with one key difference: an optional
``MEMORY.md`` at the root is treated as a hand-curated index and
injected verbatim into the system prompt.  Individual ``*.md`` files
are loaded on demand via the ``load_memory`` tool, so the per-turn
prompt stays small regardless of how much memory accumulates.

The registry also exposes write methods (``save_entry`` /
``update_entry`` / ``forget_entry``) so the LLM can curate its own
notes via tools.  Writes update both disk and in-memory state so the
next turn sees the change without a process restart, and the index
file is rewritten atomically (tmp + os.replace) to avoid leaving a
half-written MEMORY.md if the process is killed mid-write.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from sanfuclaw.skills.registry import _parse_frontmatter

from .base import MemoryEntry

logger = logging.getLogger(__name__)

_INDEX_FILENAME = "MEMORY.md"
_INDEX_MAX_LINES = 200

# Allowed memory entry name pattern. Names map directly to <name>.md on
# disk, so we forbid path separators and surprises like leading dots.
_VALID_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


class MemoryWriteError(RuntimeError):
    """Raised when a write operation fails (bad name, missing dir, IO)."""


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

    # --- write path -------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> str:
        name = (name or "").strip()
        if not _VALID_NAME.match(name):
            raise MemoryWriteError(
                f"Invalid memory name {name!r}: must match {_VALID_NAME.pattern!r}"
            )
        return name

    def _entry_path(self, name: str) -> Path:
        if self._dir is None:
            raise MemoryWriteError("Memory directory is not configured")
        return self._dir / f"{name}.md"

    def _index_path(self) -> Path:
        if self._dir is None:
            raise MemoryWriteError("Memory directory is not configured")
        return self._dir / _INDEX_FILENAME

    def save_entry(
        self,
        name: str,
        description: str,
        body: str,
        type_: str = "",
        index_line: str | None = None,
    ) -> MemoryEntry:
        """Create a new entry file and add a line to MEMORY.md.

        Refuses to overwrite an existing entry — the LLM should call
        ``update_entry`` for that. Index line defaults to
        ``- [{description or name}]({name}.md) — {description}``.
        """
        name = self._validate_name(name)
        if self._dir is None:
            raise MemoryWriteError("Memory directory is not configured")
        self._dir.mkdir(parents=True, exist_ok=True)

        path = self._entry_path(name)
        if path.exists():
            raise MemoryWriteError(
                f"Memory {name!r} already exists; use update_entry to modify it"
            )

        frontmatter_lines = ["---", f"name: {name}"]
        if description:
            frontmatter_lines.append(f"description: {description}")
        if type_:
            frontmatter_lines.append(f"type: {type_}")
        frontmatter_lines.append("---")
        text = "\n".join(frontmatter_lines) + "\n\n" + body.strip() + "\n"
        path.write_text(text)

        line = index_line or self._default_index_line(name, description)
        self._append_index_line(line)

        # Refresh in-memory state so next turn's load_memory sees the new entry.
        self.load_dir(self._dir)
        return MemoryEntry(name=name, description=description, body=body, path=path)

    def update_entry(
        self,
        name: str,
        body: str,
        description: str | None = None,
        type_: str | None = None,
    ) -> MemoryEntry:
        """Rewrite an existing entry's body. Description/type optional;
        when omitted, the existing values are preserved."""
        name = self._validate_name(name)
        path = self._entry_path(name)
        if not path.exists():
            raise MemoryWriteError(f"No such memory entry: {name}")

        existing_meta, _ = _parse_frontmatter(path.read_text())
        new_desc = description if description is not None else existing_meta.get("description", "")
        new_type = type_ if type_ is not None else existing_meta.get("type", "")

        frontmatter_lines = ["---", f"name: {name}"]
        if new_desc:
            frontmatter_lines.append(f"description: {new_desc}")
        if new_type:
            frontmatter_lines.append(f"type: {new_type}")
        frontmatter_lines.append("---")
        text = "\n".join(frontmatter_lines) + "\n\n" + body.strip() + "\n"
        path.write_text(text)

        self.load_dir(self._dir)  # type: ignore[arg-type]
        return MemoryEntry(name=name, description=new_desc, body=body, path=path)

    def forget_entry(self, name: str) -> bool:
        """Delete an entry file and strip the matching line from MEMORY.md.

        Returns True if the entry existed and was removed.
        """
        name = self._validate_name(name)
        path = self._entry_path(name)
        if not path.exists():
            return False
        path.unlink()
        self._remove_index_line_for(name)
        self.load_dir(self._dir)  # type: ignore[arg-type]
        return True

    @staticmethod
    def _default_index_line(name: str, description: str) -> str:
        title = description or name
        # Hook duplicates the description if no separate one was provided —
        # keeps the format consistent with hand-curated entries.
        hook = description or "(no description)"
        return f"- [{title}]({name}.md) — {hook}"

    def _append_index_line(self, line: str) -> None:
        index_path = self._index_path()
        existing = index_path.read_text() if index_path.exists() else ""
        # If the line already points at the same target, don't duplicate.
        target_marker = self._target_marker(line)
        if target_marker and target_marker in existing:
            # Replace the existing line for that target so descriptions stay
            # current rather than accumulating duplicates.
            new_lines = []
            for existing_line in existing.splitlines():
                if target_marker in existing_line:
                    new_lines.append(line.rstrip())
                else:
                    new_lines.append(existing_line)
            new_text = "\n".join(new_lines)
            if not new_text.endswith("\n"):
                new_text += "\n"
        else:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            new_text = existing + sep + line.rstrip() + "\n"
        self._atomic_write(index_path, new_text)
        self._index_body = self._read_index(index_path)

    def _remove_index_line_for(self, name: str) -> None:
        index_path = self._index_path()
        if not index_path.exists():
            return
        target_marker = f"({name}.md)"
        kept = [
            line for line in index_path.read_text().splitlines()
            if target_marker not in line
        ]
        new_text = "\n".join(kept)
        if kept and not new_text.endswith("\n"):
            new_text += "\n"
        self._atomic_write(index_path, new_text)
        self._index_body = self._read_index(index_path) if index_path.exists() else ""

    @staticmethod
    def _target_marker(line: str) -> str:
        # Returns "(name.md)" if the line carries a markdown link; "" otherwise.
        match = re.search(r"\(([A-Za-z0-9_\-]+\.md)\)", line)
        return f"({match.group(1)})" if match else ""

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)

    # ---------------------------------------------------------------------

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
