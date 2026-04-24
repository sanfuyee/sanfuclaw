"""User-level paths for sanfuclaw config and data.

Everything lives under `~/.sanfuclaw/` by default. Override the base
directory with `SANFUCLAW_HOME`.
"""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    """Return the sanfuclaw home directory, creating it if missing."""
    override = os.environ.get("SANFUCLAW_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".sanfuclaw"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_file() -> Path:
    return home() / "config.json"


def db_file() -> Path:
    return home() / "sanfuclaw.db"


def weixin_credentials_file() -> Path:
    return home() / "weixin_credentials.json"


def skills_dir() -> Path:
    d = home() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def memory_dir() -> Path:
    d = home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d
