"""Skill dataclass — a single skill plugin loaded from a markdown file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """A skill plugin with metadata and instructions.

    Skills are markdown files with YAML-like frontmatter:

        ---
        name: my-skill
        description: Short one-line summary shown to the LLM.
        ---

        Full instructions go here. Loaded on demand when the LLM
        invokes the `load_skill` tool.
    """

    name: str
    description: str
    instructions: str
    path: Path
