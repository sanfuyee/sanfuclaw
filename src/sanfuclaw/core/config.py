"""Configuration management — JSON (default) or TOML, plus env vars.

Precedence (highest wins):
  1. CLI flags (`--model`, `--provider`, `--channel`) — applied in cli.py
  2. SANFUCLAW_* env vars (Pydantic-style, e.g. SANFUCLAW_LLM__MODEL)
  3. Explicit `--config` path passed to `sanfuclaw start`
  4. $SANFUCLAW_CONFIG env var
  5. ~/.sanfuclaw/config.json
  6. ./sanfuclaw.toml (legacy, deprecated — see _legacy_toml_warning)
"""

from __future__ import annotations

import json
import logging
import tomllib
import warnings
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from sanfuclaw.core import paths

logger = logging.getLogger(__name__)


SUPPORTED_PROVIDERS = ("openai_compat", "anthropic")


class LLMConfigError(ValueError):
    """Raised when llm.* settings are missing or out of range.

    Subclasses `ValueError` so callers that already catch generic config
    failures keep working; the CLI catches this explicitly to print a
    user-friendly hint instead of a traceback."""


class LLMConfig(BaseSettings):
    provider: str = "openai_compat"
    model: str = "zai-org/glm-5.1"
    api_key: str = ""
    base_url: str = "https://api.hpc-ai.com/inference/v1"
    max_tokens: int = 8192
    context_window: int = 200000
    max_tool_rounds: int = 20
    temperature: float = 0.7
    system_prompt: str = (
        "You are a helpful personal AI assistant called Sanfuclaw. "
        "You are running locally on the user's machine. Be concise and helpful."
    )

    def resolved_api_key(self) -> str:
        """Return api_key from config, falling back to env vars in order:
        ANTHROPIC_API_KEY (only when provider=anthropic), then LLM_API_KEY."""
        import os

        if self.api_key:
            return self.api_key
        if self.provider == "anthropic":
            env_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if env_key:
                return env_key
        return os.environ.get("LLM_API_KEY", "")

    def validate_startup(self) -> None:
        """Validate the model-related settings at process boot.

        Raises `LLMConfigError` with a single human-readable message — the
        CLI surfaces it as a user-facing error before any heavy startup
        work (DB init, MCP spawn, channel auth) happens."""
        problems: list[str] = []

        if self.provider not in SUPPORTED_PROVIDERS:
            problems.append(
                f"llm.provider={self.provider!r} is not supported "
                f"(expected one of: {', '.join(SUPPORTED_PROVIDERS)})"
            )
        if not self.model.strip():
            problems.append("llm.model is empty — set it in config.json or with --model")
        if self.provider == "openai_compat" and not self.base_url.strip():
            problems.append(
                "llm.base_url is empty — required for provider=openai_compat "
                "(e.g. https://api.openai.com/v1)"
            )
        if not self.resolved_api_key():
            hint = "ANTHROPIC_API_KEY or LLM_API_KEY" if self.provider == "anthropic" else "LLM_API_KEY"
            problems.append(
                f"llm.api_key is empty and no {hint} env var is set"
            )
        if self.max_tokens <= 0:
            problems.append(f"llm.max_tokens must be > 0 (got {self.max_tokens})")
        if self.context_window <= 0:
            problems.append(f"llm.context_window must be > 0 (got {self.context_window})")
        if self.max_tool_rounds <= 0:
            problems.append(f"llm.max_tool_rounds must be > 0 (got {self.max_tool_rounds})")
        if not (0.0 <= self.temperature <= 2.0):
            problems.append(
                f"llm.temperature must be in [0.0, 2.0] (got {self.temperature})"
            )
        if self.max_tokens > self.context_window:
            problems.append(
                f"llm.max_tokens ({self.max_tokens}) exceeds llm.context_window "
                f"({self.context_window}) — the model has no room for input"
            )

        if problems:
            raise LLMConfigError("\n  - ".join(["Invalid LLM configuration:"] + problems))


class GatewayConfig(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 30423


class ChannelConfig(BaseSettings):
    model_config = {"extra": "allow"}
    type: str = "cli"


class SkillsConfig(BaseSettings):
    dir: str = "~/.sanfuclaw/skills"


class MemoryConfig(BaseSettings):
    dir: str = "~/.sanfuclaw/memory"


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
    timezone: str = "Asia/Shanghai"
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
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
        _warn_legacy_toml(legacy)
        return legacy

    return None


def _warn_legacy_toml(path: Path) -> None:
    """sanfuclaw.toml is the pre-0.4 location. Surface it loudly so users
    migrate to ~/.sanfuclaw/config.json before the fallback is removed."""
    msg = (
        f"Loading legacy {path} — this fallback will be removed in a future release. "
        f"Migrate to {paths.config_file()} (run `sanfuclaw init` to scaffold)."
    )
    warnings.warn(msg, DeprecationWarning, stacklevel=3)
    logger.warning(msg)


def _read_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(_strip_line_comments(path.read_text()))
    if suffix == ".toml":
        with open(path, "rb") as f:
            return tomllib.load(f)
    raise ValueError(f"Unsupported config format: {path} (expected .json or .toml)")


def _strip_line_comments(text: str) -> str:
    """Relax strict JSON into a minimal JSON5-ish dialect so the default
    template can ship commented-out channel/MCP examples.

    Accepts:
    - whole-line `//` comments (line must start with `//` after any indent)
    - trailing commas before `}` or `]`

    Both are walked with string-state tracking so URLs like
    `"https://..."` and literal commas inside strings are preserved.
    Good enough for a human-edited config file, not a general JSON5 parser.
    """
    # Pass 1: strip whole-line `//` comments.
    without_comments = "\n".join(
        "" if line.lstrip().startswith("//") else line
        for line in text.splitlines()
    )

    # Pass 2: drop trailing commas before `}` / `]`, tracking strings so
    # commas inside string values stay put.
    out: list[str] = []
    i = 0
    in_string = False
    s = without_comments
    while i < len(s):
        ch = s[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < len(s) and s[j].isspace():
                j += 1
            if j < len(s) and s[j] in "}]":
                i += 1  # skip the comma
                continue
        out.append(ch)
        i += 1
    return "".join(out)
