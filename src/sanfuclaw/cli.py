"""CLI entry point — typer-based commands for sanfuclaw."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import typer
from rich.console import Console

from sanfuclaw.core.logging import configure as _configure_logging

app = typer.Typer(
    name="sanfuclaw",
    help="Sanfuclaw — your personal AI agent.",
    no_args_is_help=True,
)
console = Console()
logger = logging.getLogger(__name__)


@app.callback()
def _main(ctx: typer.Context) -> None:
    """Runs before every subcommand — install the stderr log handler so
    module-level `logger.info(...)` calls surface under systemd / launchd."""
    _configure_logging()


from sanfuclaw.cli_cron import cron_app
from sanfuclaw.cli_service import service_app
app.add_typer(cron_app, name="cron")
app.add_typer(service_app, name="service")


def _default_config_text() -> str:
    """On-disk default config — live defaults from Settings() with commented-out
    channel/MCP examples inline so users enable integrations by uncommenting.

    The loader tolerates `//` line comments (see `core.config._strip_line_comments`),
    so the file stays valid as users edit it."""
    from sanfuclaw.core.config import Settings

    s = Settings()
    # System prompt is long; JSON-escape it so embedded quotes survive.
    import json as _json
    system_prompt = _json.dumps(s.llm.system_prompt)

    return f'''{{
  "llm": {{
    "provider": "{s.llm.provider}",
    "model": "{s.llm.model}",
    "api_key": "",
    "base_url": "{s.llm.base_url}",
    "max_tokens": {s.llm.max_tokens},
    "context_window": {s.llm.context_window},
    "max_tool_rounds": {s.llm.max_tool_rounds},
    "temperature": {s.llm.temperature},
    "system_prompt": {system_prompt}
  }},
  "timezone": "{s.timezone}",
  "gateway": {{
    "host": "{s.gateway.host}",
    "port": {s.gateway.port}
  }},
  "channels": {{
    // Uncomment a block and fill in credentials to enable a channel.
    // "telegram": {{
    //   "type": "telegram",
    //   "bot_token": "YOUR_BOT_TOKEN",
    //   "allowed_users": []
    // }},
    // "discord": {{
    //   "type": "discord",
    //   "bot_token": "YOUR_BOT_TOKEN"
    // }}
  }},
  "skills": {{
    "dir": "{s.skills.dir}"
  }},
  "mcp": {{
    "servers": {{
      // See README for recommended servers (filesystem, git, time, …).
      // "filesystem": {{
      //   "command": "npx",
      //   "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      // }}
    }}
  }}
}}
'''


def _print_config_error(err: Exception) -> None:
    """Surface an LLM config error to both stderr (logger) and the user
    (rich console) so it's visible whether running in a TTY or under a
    service manager. The hint at the end points users at the wizard."""
    from sanfuclaw.core import paths

    msg = str(err)
    logger.error("LLM configuration invalid: %s", msg.replace("\n", " | "))
    console.print(f"[red]Configuration error:[/red] {msg}")
    console.print(
        f"[dim]Edit {paths.config_file()} or re-run [bold]sanfuclaw setup[/bold] "
        "to fix the LLM section.[/dim]"
    )


def _log_startup_banner(mode: str, channel_mode: str, settings) -> None:
    """One-line, structured startup log so operators see the active model
    config in journal/launchd output. Includes redacted api-key tail so
    you can confirm which key is loaded without leaking it."""
    from sanfuclaw.core.logging import current_format, current_level, redact_secret
    from sanfuclaw import __version__

    llm = settings.llm
    api_key = llm.resolved_api_key()
    extras: dict[str, object] = {
        "version": __version__,
        "mode": mode,
        "channel_mode": channel_mode,
        "provider": llm.provider,
        "model": llm.model,
        "max_tokens": llm.max_tokens,
        "context_window": llm.context_window,
        "max_tool_rounds": llm.max_tool_rounds,
        "temperature": llm.temperature,
        "timezone": settings.timezone,
        "log_level": current_level(),
        "log_format": current_format(),
        "api_key_fp": redact_secret(api_key),
    }
    if llm.provider == "openai_compat":
        extras["base_url"] = llm.base_url

    logger.info(
        "Sanfuclaw v%s starting: mode=%s channels=%s provider=%s model=%s "
        "tokens=%d/ctx=%d temp=%.2f tz=%s log=%s/%s key=%s%s",
        __version__, mode, channel_mode, llm.provider, llm.model,
        llm.max_tokens, llm.context_window, llm.temperature, settings.timezone,
        current_level(), current_format(), redact_secret(api_key),
        f" base_url={llm.base_url}" if llm.provider == "openai_compat" else "",
        extra=extras,
    )


def _ensure_home_initialized() -> None:
    """First-run auto-init: create ~/.sanfuclaw/config.json + skills/ if missing.

    If stdin is a TTY, offer the interactive `sanfuclaw setup` wizard on
    first run. In non-interactive contexts (systemd, Docker `-d`, CI)
    fall back to writing the commented template so the process can keep
    starting without human input.
    """
    from sanfuclaw.core import paths

    cfg = paths.config_file()
    if not cfg.exists():
        if sys.stdin.isatty():
            console.print(
                f"[dim]First run — no config at {cfg}.[/dim]"
            )
            if typer.confirm("Run the interactive setup wizard now?", default=True):
                from sanfuclaw.cli_setup import run_wizard
                run_wizard()
                return
            cfg.write_text(_default_config_text())
            console.print(f"[dim]Created template at {cfg} — edit it, then re-run.[/dim]")
        else:
            cfg.write_text(_default_config_text())
            logger.warning(
                "First run (no TTY): wrote default template to %s. "
                "Run `sanfuclaw setup` in a terminal to fill in LLM credentials.",
                cfg,
            )
    paths.skills_dir()


@app.command()
def start(
    config: str = typer.Option(None, "--config", "-c", help="Path to config file (default: ~/.sanfuclaw/config.json)"),
    model: str = typer.Option(None, "--model", "-m", help="Override LLM model"),
    provider: str = typer.Option(None, "--provider", "-p", help="Override LLM provider"),
    channel: str = typer.Option("cli", "--channel", help="Channel to run (cli, telegram, weixin, all)"),
    resume: str = typer.Option(None, "--resume", "-r", help="Resume a session by ID (prefix match supported)"),
):
    """Start the Sanfuclaw agent."""
    _ensure_home_initialized()
    asyncio.run(_run(config, model, provider, channel, resume=resume))


async def _run(
    config_path: str,
    model: str | None,
    provider: str | None,
    channel_mode: str,
    resume: str | None = None,
):
    """Main async entry point."""
    from sanfuclaw.core.config import LLMConfigError, Settings
    from sanfuclaw.gateway.session_manager import SessionManager
    from sanfuclaw.gateway.wiring import MissingAPIKey, build_router
    from sanfuclaw.storage.sqlite import SQLiteStore

    # Load config
    settings = Settings.load(config_path)
    if model:
        settings.llm.model = model
    if provider:
        settings.llm.provider = provider

    # Validate model/api/limits up front. Cheaper to fail here (a single
    # logged error + exit code) than to spawn MCP servers and channel
    # auth flows only to discover at the LLM call that a setting is wrong.
    try:
        settings.llm.validate_startup()
    except LLMConfigError as e:
        _print_config_error(e)
        raise typer.Exit(1)

    _log_startup_banner("start", channel_mode, settings)

    # Set up storage and session manager
    store = SQLiteStore()
    await store.init()
    session_manager = SessionManager(store)

    # Wire tools/MCP/agent/router via shared factory
    try:
        wiring = await build_router(settings, store, session_manager)
    except (MissingAPIKey, LLMConfigError) as e:
        _print_config_error(e)
        raise typer.Exit(1)

    if len(wiring.skill_registry) > 0:
        console.print(
            f"[dim]Loaded {len(wiring.skill_registry)} skill(s) from {settings.skills.dir}[/dim]"
        )
    if wiring.mcp_manager.tools():
        console.print(f"[dim]Loaded {len(wiring.mcp_manager.tools())} MCP tool(s)[/dim]")

    router = wiring.router
    is_all = channel_mode == "all"

    # Resolve session for CLI: default is a new session each time, or
    # a resumed one if --resume was passed.
    cli_session_id: str | None = None
    if channel_mode in ("cli", "all"):
        if resume:
            resolved = await _resolve_session(store, resume)
            if not resolved:
                console.print(f"[red]Error:[/red] No session matching '{resume}'")
                raise typer.Exit(1)
            cli_session_id = resolved.id
            console.print(
                f"[dim]Resuming session {resolved.id[:8]}… "
                f"({len(resolved.history)} messages)[/dim]"
            )

    # Build channels. In `all` mode any per-channel failure is logged and
    # skipped so the remaining channels keep working; in single-channel
    # mode it stays a hard error because the user explicitly asked for it.
    from sanfuclaw.channels.factory import build_channels

    requested = [channel_mode]
    build = build_channels(settings, requested, cli_session_id=cli_session_id)
    candidates = build.channels
    skipped = list(build.skipped)

    if not is_all and skipped and not candidates:
        # Single-channel mode + the requested channel couldn't be built:
        # surface the first failure as a hard error so the user fixes it.
        name, reason = skipped[0]
        console.print(f"[red]Error:[/red] {reason}")
        raise typer.Exit(1)

    if not candidates and not skipped:
        console.print(f"[red]Error:[/red] Unknown channel: {channel_mode}")
        raise typer.Exit(1)

    # Start each channel tolerantly. Only register with the router after a
    # successful start so a failed channel never ends up holding outbound
    # messages it can't deliver.
    channels = []
    for ch in candidates:
        try:
            await ch.start()
        except Exception as e:
            reason = f"start() failed: {e}"
            if is_all:
                console.print(f"[yellow]Warning:[/yellow] channel '{ch.name}' {reason}")
                skipped.append((ch.name, reason))
                continue
            console.print(f"[red]Error:[/red] channel '{ch.name}' {reason}")
            raise typer.Exit(1)
        router.register_channel(ch)
        channels.append(ch)

    if not channels:
        logger.error("No channels could be started (%d skipped)", len(skipped))
        for name, reason in skipped:
            logger.error("  skipped %s: %s", name, reason)
        console.print("[red]Error:[/red] No channels could be started.")
        for name, reason in skipped:
            console.print(f"  - [yellow]{name}[/yellow]: {reason}")
        raise typer.Exit(1)

    if skipped:
        console.print(
            f"[yellow]Running with {len(channels)} channel(s); "
            f"{len(skipped)} skipped:[/yellow]"
        )
        for name, reason in skipped:
            console.print(f"  - [yellow]{name}[/yellow]: {reason}")
            logger.warning("Channel skipped %s: %s", name, reason)

    logger.info(
        "Channels active: %s",
        ", ".join(ch.name for ch in channels) or "<none>",
    )

    # Start runtime services (scheduler) — order matters: scheduler routes
    # through channels, so they must be registered first.
    await wiring.start_runtime()
    logger.info("Runtime ready — listening for messages")

    # Run channel receive loops as a single task so we can cancel cleanly
    # on SIGTERM (systemd/launchd) — the previous code only handled
    # KeyboardInterrupt, leaving MCP servers and the scheduler dangling
    # when a service manager asked for a graceful stop.
    runner = asyncio.create_task(_serve_channels(channels, router))
    _install_shutdown_handlers(runner)

    try:
        await runner
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        pass
    finally:
        for ch in channels:
            try:
                await ch.stop()
            except Exception as e:
                logger.warning("Channel %s stop failed: %s", ch.name, e)
        await wiring.shutdown()
        await store.close()


async def _serve_channels(channels: list, router) -> None:
    """Run every channel's receive loop until cancelled or exhausted."""
    if len(channels) == 1:
        ch = channels[0]
        async for envelope in ch.receive():
            try:
                await router.route(envelope)
            except Exception as e:
                console.print(f"\n[red]Error:[/red] {e}\n")
        return

    async def _channel_loop(ch):
        async for envelope in ch.receive():
            try:
                await router.route(envelope)
            except Exception as e:
                console.print(f"\n[red]Error:[/red] [{ch.name}] {e}\n")

    async with asyncio.TaskGroup() as tg:
        for ch in channels:
            tg.create_task(_channel_loop(ch))


def _install_shutdown_handlers(runner: asyncio.Task) -> None:
    """Cancel the channel runner on SIGTERM/SIGINT.

    Without this, `systemctl stop` (SIGTERM) just kills the process before
    the `finally` block runs, leaving MCP child processes orphaned.
    """
    loop = asyncio.get_running_loop()

    def _request_stop(signame: str) -> None:
        if not runner.done():
            logger.info("Shutdown signal %s received, draining…", signame)
            runner.cancel()

    for sig, name in [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")]:
        try:
            loop.add_signal_handler(sig, _request_stop, name)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler on the proactor
            # event loop — Ctrl-C still raises KeyboardInterrupt and is
            # handled in the caller.
            pass


async def _resolve_session(store, session_id_prefix: str):
    """Resolve a session by exact or prefix match on its ID."""
    from sanfuclaw.core.session import Session

    # Try exact match first
    session = await store.get_session(session_id_prefix)
    if session:
        return session

    # Try prefix match via list
    all_sessions = await store.list_sessions(limit=100)
    matches = [s for s in all_sessions if s["id"].startswith(session_id_prefix)]
    if len(matches) == 1:
        return await store.get_session(matches[0]["id"])
    if len(matches) > 1:
        console.print(f"[yellow]Ambiguous prefix '{session_id_prefix}', matches {len(matches)} sessions:[/yellow]")
        for s in matches[:5]:
            console.print(f"  {s['id'][:8]}  {s['channel_id']}  {s['updated_at']}")
        return None
    return None


@app.command()
def sessions(
    config: str = typer.Option(None, "--config", "-c", help="Path to config file (default: ~/.sanfuclaw/config.json)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of sessions to show"),
    channel_filter: str = typer.Option(None, "--channel", help="Filter by channel"),
):
    """List recent sessions."""
    asyncio.run(_list_sessions(config, limit, channel_filter))


async def _list_sessions(config_path: str, limit: int, channel_filter: str | None):
    from rich.table import Table
    from sanfuclaw.storage.sqlite import SQLiteStore

    store = SQLiteStore()
    await store.init()
    try:
        rows = await store.list_sessions(channel_id=channel_filter, limit=limit)
        if not rows:
            console.print("[dim]No sessions found.[/dim]")
            return

        table = Table(title="Recent Sessions")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Channel", style="green")
        table.add_column("Updated", style="yellow")
        table.add_column("Msgs", justify="right")
        table.add_column("Last Message", max_width=50)

        for row in rows:
            last_msg = row["last_message"]
            if len(last_msg) > 50:
                last_msg = last_msg[:47] + "..."
            # Replace newlines for display
            last_msg = last_msg.replace("\n", " ")
            # Short IDs (e.g. "cli-session") display in full; UUIDs truncate to 8
            raw_id = row["id"]
            display_id = raw_id if len(raw_id) <= 16 else raw_id[:8]
            table.add_row(
                display_id,
                row["channel_id"],
                row["updated_at"][:19],
                str(row["message_count"]),
                last_msg,
            )

        console.print(table)
        console.print(f"\n[dim]Resume with:[/dim] sanfuclaw start --resume <ID>")
    finally:
        await store.close()


@app.command()
def serve(
    config: str = typer.Option(None, "--config", "-c", help="Path to config file (default: ~/.sanfuclaw/config.json)"),
    host: str = typer.Option(None, "--host", "-h", help="Override host"),
    port: int = typer.Option(None, "--port", help="Override port"),
):
    """Start the Sanfuclaw gateway server (WebSocket + HTTP + WebChat)."""
    import uvicorn
    from sanfuclaw.core.config import LLMConfigError, Settings
    from sanfuclaw.gateway.server import GatewayServer

    _ensure_home_initialized()
    settings = Settings.load(config)

    try:
        settings.llm.validate_startup()
    except LLMConfigError as e:
        _print_config_error(e)
        raise typer.Exit(1)

    server = GatewayServer(settings)

    final_host = host or settings.gateway.host
    final_port = port or settings.gateway.port

    _log_startup_banner("serve", "webchat", settings)
    logger.info("Gateway binding %s:%d", final_host, final_port)

    console.print(f"[bold green]Sanfuclaw Gateway[/bold green] starting on http://{final_host}:{final_port}")
    console.print(f"  WebChat:  http://{final_host}:{final_port}/")
    console.print(f"  API:      http://{final_host}:{final_port}/api/status")
    console.print(f"  WS:       ws://{final_host}:{final_port}/ws")
    console.print()

    # log_config=None — keep the root handler we installed in _main() so
    # sanfuclaw module logs aren't stomped by uvicorn's default dictConfig.
    uvicorn.run(server.app, host=final_host, port=final_port, log_config=None)


@app.command()
def weixin_login(
    base_url: str = typer.Option(
        "https://ilinkai.weixin.qq.com", "--base-url", help="iLink Bot API base URL"
    ),
):
    """Login to WeChat via QR code scan."""
    asyncio.run(_weixin_login(base_url))


async def _weixin_login(base_url: str):
    from sanfuclaw.channels.weixin import qr_login

    console.print("[bold]WeChat QR Login[/bold]")
    try:
        creds = await qr_login(base_url)
        console.print(f"[green]Login successful![/green]")
        console.print(f"  Bot ID:  {creds.bot_id}")
        console.print(f"  User ID: {creds.user_id}")
        console.print(f"  Credentials saved to: {creds.path.absolute()}")
    except Exception as e:
        console.print(f"[red]Login failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def setup():
    """Run the interactive first-run wizard — picks LLM, API key, channels, MCP."""
    from sanfuclaw.cli_setup import run_wizard
    run_wizard()


@app.command()
def init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
):
    """Create ~/.sanfuclaw/config.json with a default template."""
    from sanfuclaw.core import paths

    cfg = paths.config_file()
    if cfg.exists() and not force:
        console.print(f"[yellow]Config already exists:[/yellow] {cfg}")
        console.print("Use [bold]--force[/bold] to overwrite, or edit the file directly.")
        raise typer.Exit(0)

    cfg.write_text(_default_config_text())
    paths.skills_dir()  # ensure skills/ exists
    console.print(f"[green]Created:[/green] {cfg}")
    console.print(f"[dim]Skills dir: {paths.home() / 'skills'}[/dim]")
    console.print()
    console.print("Next: edit the file and set [bold]llm.api_key[/bold], then run [bold]sanfuclaw start[/bold].")


@app.command()
def version():
    """Show version."""
    from sanfuclaw import __version__
    console.print(f"sanfuclaw v{__version__}")


if __name__ == "__main__":
    app()
