"""CLI entry point — typer-based commands for sanfuclaw."""

from __future__ import annotations

import asyncio
import os

import typer
from rich.console import Console

app = typer.Typer(
    name="sanfuclaw",
    help="Sanfuclaw — your personal AI agent.",
    no_args_is_help=True,
)
console = Console()


def _ensure_home_initialized() -> None:
    """First-run auto-init: create ~/.sanfuclaw/config.json + skills/ if missing.

    Runs before every CLI invocation so `pip install` + any `sanfuclaw` command
    produces a ready-to-edit config with no extra setup step.
    """
    import json as _json
    from sanfuclaw.core import paths

    cfg = paths.config_file()
    if not cfg.exists():
        cfg.write_text(_json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
        console.print(f"[dim]First run — created {cfg}[/dim]")
    paths.skills_dir()


@app.callback()
def _main():
    """Sanfuclaw — your personal AI agent."""
    _ensure_home_initialized()


@app.command()
def start(
    config: str = typer.Option(None, "--config", "-c", help="Path to config file (default: ~/.sanfuclaw/config.json)"),
    model: str = typer.Option(None, "--model", "-m", help="Override LLM model"),
    provider: str = typer.Option(None, "--provider", "-p", help="Override LLM provider"),
    channel: str = typer.Option("cli", "--channel", help="Channel to run (cli, telegram, weixin, all)"),
    resume: str = typer.Option(None, "--resume", "-r", help="Resume a session by ID (prefix match supported)"),
):
    """Start the Sanfuclaw agent."""
    asyncio.run(_run(config, model, provider, channel, resume=resume))


def _build_transport(settings):
    """Create the LLM transport based on config."""
    api_key = settings.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
    if not api_key:
        console.print("[red]Error:[/red] No API key found. Set llm.api_key in ~/.sanfuclaw/config.json or LLM_API_KEY env var")
        raise typer.Exit(1)

    if settings.llm.provider == "anthropic":
        from sanfuclaw.agents.transports.anthropic import AnthropicTransport
        return AnthropicTransport(api_key=api_key, default_model=settings.llm.model)
    else:
        from sanfuclaw.agents.transports.openai_compat import OpenAICompatTransport
        return OpenAICompatTransport(
            api_key=api_key,
            base_url=settings.llm.base_url,
            default_model=settings.llm.model,
        )


async def _run(
    config_path: str,
    model: str | None,
    provider: str | None,
    channel_mode: str,
    resume: str | None = None,
):
    """Main async entry point."""
    from sanfuclaw.core.config import Settings
    from sanfuclaw.agents.llm_agent import LLMAgent
    from sanfuclaw.gateway.router import Router
    from sanfuclaw.gateway.session_manager import SessionManager
    from sanfuclaw.storage.sqlite import SQLiteStore
    from sanfuclaw.mcp_client.manager import MCPManager
    from sanfuclaw.mcp_client.tool_adapter import MCPToolAdapter
    from sanfuclaw.skills.registry import SkillRegistry
    from sanfuclaw.tools.registry import ToolRegistry
    from sanfuclaw.tools.shell import ShellTool
    from sanfuclaw.tools.skill_loader import LoadSkillTool
    from sanfuclaw.tools.web_fetch import WebFetchTool

    # Load config
    settings = Settings.load(config_path)
    if model:
        settings.llm.model = model
    if provider:
        settings.llm.provider = provider

    # Set up storage and session manager
    store = SQLiteStore()
    await store.init()

    session_manager = SessionManager(store)

    # Set up skills
    skill_registry = SkillRegistry(settings.skills.dir)
    if len(skill_registry) > 0:
        console.print(f"[dim]Loaded {len(skill_registry)} skill(s) from {settings.skills.dir}[/dim]")

    # Set up tools
    tool_registry = ToolRegistry()
    tool_registry.register(ShellTool())
    tool_registry.register(WebFetchTool())
    if len(skill_registry) > 0:
        tool_registry.register(LoadSkillTool(skill_registry))

    # Set up MCP servers — start and register their tools
    mcp_manager = MCPManager(settings.mcp.servers)
    await mcp_manager.start()
    for server_name, mcp_tool in mcp_manager.tools():
        session = mcp_manager.get_session(server_name)
        tool_registry.register(MCPToolAdapter(server_name, mcp_tool, session))
    if mcp_manager.tools():
        console.print(f"[dim]Loaded {len(mcp_manager.tools())} MCP tool(s)[/dim]")

    # Set up transport and agent
    transport = _build_transport(settings)
    agent = LLMAgent(
        name="default",
        transport=transport,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        system_prompt=settings.llm.system_prompt,
        model=settings.llm.model,
        max_tokens=settings.llm.max_tokens,
        temperature=settings.llm.temperature,
    )

    # Set up router
    router = Router(session_manager=session_manager)
    router.register_agent(agent, default=True)

    # Build channels
    channels = []

    if channel_mode in ("cli", "all"):
        from sanfuclaw.channels.cli_channel import CLIChannel

        # Resolve session for CLI: default is a new session each time
        if resume:
            resolved = await _resolve_session(store, resume)
            if not resolved:
                console.print(f"[red]Error:[/red] No session matching '{resume}'")
                raise typer.Exit(1)
            cli_session_id = resolved.id
            console.print(f"[dim]Resuming session {resolved.id[:8]}… ({len(resolved.history)} messages)[/dim]")
        else:
            import uuid
            cli_session_id = f"cli-{uuid.uuid4().hex[:8]}"

        cli = CLIChannel(session_id=cli_session_id)
        router.register_channel(cli)
        channels.append(cli)

    if channel_mode in ("telegram", "all"):
        tg_config = settings.channels.get("telegram")
        bot_token = None
        if tg_config:
            bot_token = getattr(tg_config, "bot_token", "") or ""
        bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            console.print("[red]Error:[/red] Telegram bot token not found. Set channels.telegram.bot_token in config or TELEGRAM_BOT_TOKEN env var")
            raise typer.Exit(1)

        from sanfuclaw.channels.telegram import TelegramChannel
        allowed = getattr(tg_config, "allowed_users", None) if tg_config else None
        tg = TelegramChannel(bot_token=bot_token, allowed_users=allowed)
        router.register_channel(tg)
        channels.append(tg)

    if channel_mode in ("weixin", "all"):
        from sanfuclaw.channels.weixin import WeixinChannel
        wx = WeixinChannel()
        if not wx._creds.is_valid:
            console.print("[red]Error:[/red] WeChat not logged in. Run 'sanfuclaw weixin-login' first.")
            raise typer.Exit(1)
        router.register_channel(wx)
        channels.append(wx)

    if not channels:
        console.print(f"[red]Error:[/red] Unknown channel: {channel_mode}")
        raise typer.Exit(1)

    # Start all channels
    for ch in channels:
        await ch.start()

    try:
        if len(channels) == 1:
            # Single channel — simple loop
            async for envelope in channels[0].receive():
                try:
                    await router.route(envelope)
                except Exception as e:
                    console.print(f"\n[red]Error:[/red] {e}\n")
        else:
            # Multiple channels — run receive loops concurrently
            async def _channel_loop(ch):
                async for envelope in ch.receive():
                    try:
                        await router.route(envelope)
                    except Exception as e:
                        console.print(f"\n[red]Error:[/red] [{ch.name}] {e}\n")

            async with asyncio.TaskGroup() as tg:
                for ch in channels:
                    tg.create_task(_channel_loop(ch))
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        for ch in channels:
            await ch.stop()
        await mcp_manager.stop()
        await store.close()


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
    from sanfuclaw.core.config import Settings
    from sanfuclaw.gateway.server import GatewayServer

    settings = Settings.load(config)
    server = GatewayServer(settings)

    final_host = host or settings.gateway.host
    final_port = port or settings.gateway.port

    console.print(f"[bold green]Sanfuclaw Gateway[/bold green] starting on http://{final_host}:{final_port}")
    console.print(f"  WebChat:  http://{final_host}:{final_port}/")
    console.print(f"  API:      http://{final_host}:{final_port}/api/status")
    console.print(f"  WS:       ws://{final_host}:{final_port}/ws")
    console.print()

    uvicorn.run(server.app, host=final_host, port=final_port, log_level="info")


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


DEFAULT_CONFIG: dict = {
    "llm": {
        "provider": "openai_compat",
        "model": "moonshotai/kimi-k2.5",
        "base_url": "https://api.hpc-ai.com/inference/v1",
        "api_key": "",
        "max_tokens": 4096,
        "temperature": 0.7,
        "system_prompt": "You are a helpful personal AI assistant called Sanfuclaw. You are running locally on the user's machine. Be concise and helpful.",
    },
    "gateway": {"host": "127.0.0.1", "port": 18789},
    "skills": {"dir": "~/.sanfuclaw/skills"},
    "channels": {},
    "mcp": {"servers": {}},
}


@app.command()
def init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
):
    """Create ~/.sanfuclaw/config.json with a default template."""
    import json as _json
    from sanfuclaw.core import paths

    cfg = paths.config_file()
    if cfg.exists() and not force:
        console.print(f"[yellow]Config already exists:[/yellow] {cfg}")
        console.print("Use [bold]--force[/bold] to overwrite, or edit the file directly.")
        raise typer.Exit(0)

    cfg.write_text(_json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    paths.skills_dir()  # ensure skills/ exists
    console.print(f"[green]Created:[/green] {cfg}")
    console.print(f"[dim]Skills dir: {paths.home() / 'skills'}[/dim]")
    console.print()
    console.print("Next: edit the file and set [bold]llm.api_key[/bold], then run [bold]sanfuclaw start[/bold].")


@app.command()
def uninstall(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    keep_config: bool = typer.Option(False, "--keep-config", help="Keep config.json"),
    purge: bool = typer.Option(False, "--purge", help="Also run `pip uninstall sanfuclaw`"),
):
    """Remove ~/.sanfuclaw/ (data, sessions, credentials).

    Pass --purge to additionally uninstall the Python package in one go.
    """
    import shutil
    import subprocess
    import sys
    from sanfuclaw.core import paths

    home = paths.home()
    home_exists = home.exists() and any(home.iterdir())

    targets = []
    if home_exists:
        targets.append(str(home))
    if purge:
        targets.append("Python package 'sanfuclaw'")

    if not targets:
        console.print(f"[dim]Nothing to remove.[/dim]")
        return

    console.print("[bold]About to remove:[/bold]")
    for t in targets:
        console.print(f"  - {t}")
    if home_exists:
        contents = sorted(p.name for p in home.iterdir())
        for name in contents:
            console.print(f"      · {name}")

    if not yes:
        confirm = typer.confirm("Proceed?", default=False)
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    if home_exists:
        if keep_config:
            cfg = paths.config_file()
            backup = cfg.read_bytes() if cfg.exists() else None
            shutil.rmtree(home)
            if backup is not None:
                paths.home()
                cfg.write_bytes(backup)
                console.print(f"[green]Removed data, kept:[/green] {cfg}")
        else:
            shutil.rmtree(home)
            console.print(f"[green]Removed:[/green] {home}")

    if purge:
        console.print("[dim]Running `pip uninstall sanfuclaw -y`…[/dim]")
        rc = subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "sanfuclaw"])
        if rc != 0:
            console.print(f"[red]pip uninstall exited with code {rc}[/red]")
            raise typer.Exit(rc)
        console.print("[green]Package uninstalled.[/green]")


@app.command()
def version():
    """Show version."""
    from sanfuclaw import __version__
    console.print(f"sanfuclaw v{__version__}")


if __name__ == "__main__":
    app()
