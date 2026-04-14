"""Skill plugin system — markdown-based skills with YAML frontmatter."""

from .base import Skill
from .registry import SkillRegistry

__all__ = ["Skill", "SkillRegistry"]
