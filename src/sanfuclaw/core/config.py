"""Configuration management — JSON (default) or TOML, plus env vars."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from sanfuclaw.core import paths


class LLMConfig(BaseSettings):
    provider: str = "openai_compat"
    model: str = "moonshotai/kimi-k2.5"
    api_key: str = ""
    base_url: str = "https://api.hpc-ai.com/inference/v1"
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = (
        "You are a helpful personal AI assistant called Sanfuclaw. "
        "You are running locally on the user's machine. Be concise and helpful."
    )


class GatewayConfig(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 30423


class ChannelConfig(BaseSettings):
    model_config = {"extra": "allow"}
    type: str = "cli"


class SkillsConfig(BaseSettings):
    dir: str = "~/.sanfuclaw/skills"


class MCPServerConfig(BaseSettings):
    """One MCP server — either stdio (command/args/env) or SSE (url)."""

    model_config = {"extra": "allow"}
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    enabled: bool = True


class MCPConfig(BaseSettings):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class Settings(BaseSettings):
    model_config = {"env_prefix": "SANFUCLAW_", "env_nested_delimiter": "__"}

    llm: LLMConfig = Field(default_factory=LLMConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """Load settings from an explicit path, or auto-discover.

        Search order when `path` is None:
          1. $SANFUCLAW_CONFIG
          2. ~/.sanfuclaw/config.json
          3. ./sanfuclaw.toml (legacy, kept for backward compat)
        """
        resolved = _resolve_config_path(path)
        data: dict[str, Any] = _read_config(resolved) if resolved else {}
        return cls(**data)

    @classmethod
    def from_toml(cls, path: str | Path = "sanfuclaw.toml") -> "Settings":
        """Legacy loader — kept so old callers keep working."""
        return cls.load(path)


def _resolve_config_path(path: str | Path | None) -> Path | None:
    if path:
        p = Path(path).expanduser()
        return p if p.exists() else None

    import os
    env_path = os.environ.get("SANFUCLAW_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p

    user_cfg = paths.config_file()
    if user_cfg.exists():
        return user_cfg

    legacy = Path("sanfuclaw.toml")
    if legacy.exists():
        return legacy

    return None


def _read_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text())
    if suffix == ".toml":
        with open(path, "rb") as f:
            return tomllib.load(f)
    raise ValueError(f"Unsupported config format: {path} (expected .json or .toml)")
