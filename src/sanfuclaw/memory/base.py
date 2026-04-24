"""Memory dataclass — a single memory entry loaded from a markdown file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MemoryEntry:
    """A memory entry: persistent, cross-session note kept on disk.

    Entries are markdown files with optional YAML-like frontmatter:

        ---
        name: user_role
        description: One-line hook shown in the MEMORY index.
        ---

        Full body here.  Loaded on demand via the `load_memory` tool.

    Frontmatter is optional.  If absent, the file stem is used as the
    name and the description defaults to empty.  A sibling ``MEMORY.md``
    at the memory root serves as the user-maintained index and is
    injected verbatim into the system prompt.
    """

    name: str
    description: str
    body: str
    path: Path
