"""Configuration management — TOML + env vars via Pydantic Settings."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings


class LLMConfig(BaseSettings):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = "You are a helpful personal AI assistant called Sanfuclaw."


class GatewayConfig(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 18789


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
    def from_toml(cls, path: str | Path = "sanfuclaw.toml") -> "Settings":
        """Load settings from a TOML file, merged with env vars."""
        p = Path(path)
        data: dict[str, Any] = {}
        if p.exists():
            with open(p, "rb") as f:
                data = tomllib.load(f)
        return cls(**data)
