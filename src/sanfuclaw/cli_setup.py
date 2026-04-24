"""`sanfuclaw setup` — interactive first-run wizard.

Collects LLM provider + key, messaging channels, MCP bundle, and service
autostart preferences, then writes a complete `~/.sanfuclaw/config.json`.

Design: docs/installer-p0.md. Keep this file dependency-free beyond the
already-bundled `typer` + `rich` stack.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console
from rich.prompt import Confirm, Prompt

from sanfuclaw.core import paths
from sanfuclaw.core.config import Settings

console = Console()


# ---------------------------------------------------------------------------
# Provider presets — single source of truth for the menu
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    provider: str       # goes to llm.provider
    base_url: str       # goes to llm.base_url
    default_model: str  # goes to llm.model (user may override)
    signup_url: str
    needs_key: bool = True
    note: str = ""      # shown after selection


_PRESETS: list[Preset] = [
    Preset(
        key="hpc-ai",
        label="HPC-AI (CN, subsidized — recommended)",
        provider="openai_compat",
        base_url="https://api.hpc-ai.com/inference/v1",
        default_model="zai-org/glm-5.1",
        signup_url="https://hpc-ai.com/",
    ),
    Preset(
        key="moonshot",
        label="Moonshot / Kimi (CN)",
        provider="openai_compat",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        signup_url="https://platform.moonshot.cn/",
    ),
    Preset(
        key="deepseek",
        label="DeepSeek (CN)",
        provider="openai_compat",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        signup_url="https://platform.deepseek.com/",
    ),
    Preset(
        key="openai",
        label="OpenAI (intl)",
        provider="openai_compat",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        signup_url="https://platform.openai.com/",
    ),
    Preset(
        key="anthropic",
        label="Anthropic (intl, native tool_use)",
        provider="anthropic",
        base_url="",
        default_model="claude-sonnet-4-6",
        signup_url="https://console.anthropic.com/",
    ),
    Preset(
        key="ollama",
        label="Ollama (local, no API key needed)",
        provider="openai_compat",
        base_url="http://127.0.0.1:11434/v1",
        default_model="llama3.2",
        signup_url="https://ollama.com/",
        needs_key=False,
        note="Install Ollama first, then `ollama pull llama3.2`.",
    ),
    Preset(
        key="custom",
        label="Custom (any OpenAI-compatible endpoint)",
        provider="openai_compat",
        base_url="",
        default_model="",
        signup_url="",
    ),
]


# ---------------------------------------------------------------------------
# Recommended MCP bundle — enabled as a single yes/no
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class McpEntry:
    name: str
    command: str
    args: list[str]
    runtime: str  # "uvx" or "npx" — what needs to be on PATH


_MCP_BUNDLE: list[McpEntry] = [
    McpEntry("time", "uvx", ["mcp-server-time"], "uvx"),
    McpEntry("fetch", "uvx", ["mcp-server-fetch"], "uvx"),
    McpEntry(
        "sequential-thinking",
        "npx",
        ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "npx",
    ),
]


# ---------------------------------------------------------------------------
# Collected answers
# ---------------------------------------------------------------------------

@dataclass
class WizardAnswers:
    preset: Preset
    api_key: str
    model: str
    base_url: str
    telegram_token: str = ""
    enable_weixin: bool = False
    enable_mcp_bundle: bool = False
    install_service: bool = False


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    """Run the full interactive wizard. Raises SystemExit(0) on clean exit."""
    try:
        _print_welcome()
        _env_check()
        answers = _collect_answers()
        _write_config(answers)
        _maybe_install_service(answers)
        _print_summary(answers)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Setup cancelled. Nothing written.[/yellow]")
        raise SystemExit(130)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _print_welcome() -> None:
    console.print()
    console.print("[bold cyan]Sanfuclaw setup[/bold cyan]")
    console.print(
        "[dim]This wizard asks a few questions and writes a working "
        f"config to {paths.config_file()}.[/dim]"
    )
    console.print("[dim]Press Ctrl-C at any time to abort without saving.[/dim]")
    console.print()


def _env_check() -> None:
    """Non-blocking environment summary. Never exits on failure."""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks = [
        ("python", py_ver, "ok", ""),
        _check_tool("npx", "needed for some MCP servers (e.g. sequential-thinking)"),
        _check_tool("uvx", "needed for mcp-server-time and mcp-server-fetch"),
    ]
    console.print("[bold]Environment[/bold]")
    for name, version, status, hint in checks:
        mark = "[green]✓[/green]" if status == "ok" else "[yellow]✗[/yellow]"
        pad = f"{name:<8}"
        val = f"{version:<16}" if version else f"{'not found':<16}"
        tail = f"[dim]({hint})[/dim]" if hint and status != "ok" else ""
        console.print(f"  {mark} {pad}{val}{tail}")
    console.print()


def _check_tool(name: str, hint: str) -> tuple[str, str, str, str]:
    found = shutil.which(name)
    if not found:
        return (name, "", "missing", hint)
    try:
        r = subprocess.run(
            [found, "--version"], capture_output=True, text=True, timeout=5
        )
        version = (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 else "(unknown)"
    except Exception:
        version = "(unknown)"
    return (name, version, "ok", "")


def _collect_answers() -> WizardAnswers:
    preset = _pick_preset()
    api_key, model, base_url = _collect_key_model(preset)
    telegram_token = _ask_telegram()
    enable_weixin = _ask_weixin()
    enable_mcp = _ask_mcp_bundle()
    install_service = _ask_service()
    return WizardAnswers(
        preset=preset,
        api_key=api_key,
        model=model,
        base_url=base_url,
        telegram_token=telegram_token,
        enable_weixin=enable_weixin,
        enable_mcp_bundle=enable_mcp,
        install_service=install_service,
    )


def _pick_preset() -> Preset:
    console.print("[bold]Step 1 ▸ LLM provider[/bold]")
    for i, p in enumerate(_PRESETS, 1):
        console.print(f"  [cyan]{i}.[/cyan] {p.label}")
    choices = [str(i) for i in range(1, len(_PRESETS) + 1)]
    pick = Prompt.ask("Pick one", choices=choices, default="1", show_choices=False)
    preset = _PRESETS[int(pick) - 1]
    console.print(f"[dim]Selected: {preset.label}[/dim]")
    if preset.signup_url:
        console.print(f"[dim]Signup / docs: {preset.signup_url}[/dim]")
    if preset.note:
        console.print(f"[dim]Note: {preset.note}[/dim]")
    console.print()
    return preset


def _collect_key_model(preset: Preset) -> tuple[str, str, str]:
    """Returns (api_key, model, base_url) — filled from preset defaults
    when the user doesn't override."""
    console.print("[bold]Step 2 ▸ Credentials & model[/bold]")

    # base_url: empty for presets means "ask". Non-empty means lock it.
    if preset.base_url:
        base_url = preset.base_url
    else:
        base_url = Prompt.ask(
            "  API base URL",
            default="https://api.openai.com/v1" if preset.key != "anthropic" else "",
        )

    # Model: always offer the preset default, let user override.
    model = Prompt.ask("  Model", default=preset.default_model or "")

    # API key: hidden input, optional only for Ollama.
    if preset.needs_key:
        api_key = Prompt.ask("  API key", password=True, default="").strip()
        if not api_key:
            console.print(
                "  [yellow]No key entered — you'll need to fill "
                "llm.api_key in config.json before `sanfuclaw start` works.[/yellow]"
            )
    else:
        api_key = ""
    console.print()
    return api_key, model, base_url


def _ask_telegram() -> str:
    console.print("[bold]Step 3 ▸ Messaging channels[/bold]")
    console.print("[dim]The CLI channel is always on. WebChat comes up via `sanfuclaw serve`.[/dim]")

    if not Confirm.ask("  Enable Telegram?", default=False):
        console.print()
        return ""
    console.print(
        "[dim]  Create a bot at https://t.me/BotFather — it gives you a token "
        "like `12345:ABC…`.[/dim]"
    )
    token = Prompt.ask("  Telegram bot token", password=True, default="").strip()
    return token


def _ask_weixin() -> bool:
    if not Confirm.ask("  Enable WeChat (iLink Bot)?", default=False):
        console.print()
        return False
    console.print(
        "[dim]  WeChat credentials come from a QR scan. After setup finishes, "
        "run:  [bold]sanfuclaw weixin-login[/bold][/dim]"
    )
    console.print()
    return True


def _ask_mcp_bundle() -> bool:
    console.print("[bold]Step 4 ▸ MCP tools (optional)[/bold]")
    console.print(
        "[dim]The recommended bundle adds: time, fetch, sequential-thinking. "
        "Requires `uvx` and `npx` on PATH (see Environment section above).[/dim]"
    )
    answer = Confirm.ask("  Enable recommended MCP bundle?", default=False)
    if answer:
        missing: list[str] = []
        for needed in {e.runtime for e in _MCP_BUNDLE}:
            if not shutil.which(needed):
                missing.append(needed)
        if missing:
            console.print(
                f"  [yellow]Warning:[/yellow] {', '.join(missing)} not on PATH. "
                "The MCP entries will be written but those servers won't start "
                "until you install the runtime."
            )
    console.print()
    return answer


def _ask_service() -> bool:
    console.print("[bold]Step 5 ▸ Background autostart[/bold]")
    if sys.platform == "win32":
        console.print(
            "[dim]  Windows autostart is not yet supported. Start manually with "
            "`sanfuclaw start` or `sanfuclaw serve`.[/dim]"
        )
        console.print()
        return False
    if sys.platform not in ("linux", "darwin"):
        console.print(f"[dim]  Autostart not supported on {sys.platform}.[/dim]")
        console.print()
        return False

    manager = "launchd" if sys.platform == "darwin" else "systemd"
    console.print(
        f"[dim]  Sanfuclaw can install itself as a {manager} user service so it "
        "starts at login and restarts on crash.[/dim]"
    )
    answer = Confirm.ask("  Install background service now?", default=False)
    console.print()
    return answer


# ---------------------------------------------------------------------------
# Write the config
# ---------------------------------------------------------------------------

def _write_config(answers: WizardAnswers) -> None:
    cfg = paths.config_file()
    if cfg.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = cfg.with_name(f"{cfg.name}.bak.{ts}")
        shutil.copy2(cfg, backup)
        console.print(f"[dim]Backed up existing config → {backup}[/dim]")

    channels: dict[str, dict] = {}
    if answers.telegram_token:
        channels["telegram"] = {
            "type": "telegram",
            "bot_token": answers.telegram_token,
            "allowed_users": [],
        }
    if answers.enable_weixin:
        channels["weixin"] = {"type": "weixin"}

    mcp_servers: dict[str, dict] = {}
    if answers.enable_mcp_bundle:
        for e in _MCP_BUNDLE:
            mcp_servers[e.name] = {"command": e.command, "args": list(e.args)}

    text = build_config_text(
        llm_provider=answers.preset.provider,
        llm_model=answers.model,
        llm_api_key=answers.api_key,
        llm_base_url=answers.base_url,
        channels=channels,
        mcp_servers=mcp_servers,
    )
    cfg.write_text(text)
    paths.skills_dir()  # ensure skills/ exists
    console.print(f"[green]Wrote[/green] {cfg}")


def _maybe_install_service(answers: WizardAnswers) -> None:
    if not answers.install_service:
        return
    console.print()
    console.print("[bold]Installing background service…[/bold]")
    from sanfuclaw.cli_service import _sanfuclaw_invocation

    cmd = [*_sanfuclaw_invocation(), "service", "install", "--enable"]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        console.print(
            f"[yellow]Service install failed (exit {e.returncode}). "
            "You can retry later with `sanfuclaw service install --enable`.[/yellow]"
        )


def _print_summary(answers: WizardAnswers) -> None:
    console.print()
    console.print("[bold green]Setup complete.[/bold green]")
    console.print()
    console.print("Next steps:")
    if not answers.api_key and answers.preset.needs_key:
        console.print(
            f"  [yellow]1.[/yellow] Fill in [bold]llm.api_key[/bold] at "
            f"{paths.config_file()} (you left it blank)."
        )
        console.print("  [yellow]2.[/yellow] Run [bold]sanfuclaw start[/bold] to chat in the terminal.")
    else:
        console.print("  [cyan]1.[/cyan] Run [bold]sanfuclaw start[/bold] to chat in the terminal.")
    if answers.enable_weixin:
        console.print(
            "  [cyan]•[/cyan] Finish WeChat login: [bold]sanfuclaw weixin-login[/bold]"
        )
    if answers.telegram_token:
        console.print(
            "  [cyan]•[/cyan] Start Telegram: [bold]sanfuclaw start --channel telegram[/bold]"
        )
    console.print("  [cyan]•[/cyan] Open the WebChat UI: [bold]sanfuclaw serve[/bold]")
    console.print()


# ---------------------------------------------------------------------------
# Config renderer — emits plain valid JSON from the wizard's collected
# answers. The legacy commented-template renderer (used by `sanfuclaw init`
# and the TTY-less first-run fallback) lives in cli.py and is unchanged.
# ---------------------------------------------------------------------------

def build_config_text(
    *,
    llm_provider: str,
    llm_model: str,
    llm_api_key: str,
    llm_base_url: str,
    channels: dict[str, dict],
    mcp_servers: dict[str, dict],
) -> str:
    """Render a complete `config.json` as plain JSON, 2-space indent.

    Users who want commented-out channel/MCP examples can see them in
    `config.example.json` at the project root — keeping the wizard output
    as pure JSON avoids indentation edge cases and is trivially diff-able.
    """
    s = Settings()
    payload: dict = {
        "llm": {
            "provider": llm_provider,
            "model": llm_model,
            "api_key": llm_api_key,
            "base_url": llm_base_url,
            "max_tokens": s.llm.max_tokens,
            "context_window": s.llm.context_window,
            "max_tool_rounds": s.llm.max_tool_rounds,
            "temperature": s.llm.temperature,
            "system_prompt": s.llm.system_prompt,
        },
        "timezone": s.timezone,
        "gateway": {"host": s.gateway.host, "port": s.gateway.port},
        "channels": channels,
        "skills": {"dir": s.skills.dir},
        "memory": {"dir": "~/.sanfuclaw/memory"},
        "mcp": {"servers": mcp_servers},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
