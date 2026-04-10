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


@app.command()
def start(
    config: str = typer.Option("sanfuclaw.toml", "--config", "-c", help="Path to config file"),
    model: str = typer.Option(None, "--model", "-m", help="Override LLM model"),
    provider: str = typer.Option(None, "--provider", "-p", help="Override LLM provider"),
    channel: str = typer.Option("cli", "--channel", help="Channel to run (cli, telegram, all)"),
):
    """Start the Sanfuclaw agent."""
    asyncio.run(_run(config, model, provider, channel))


def _build_transport(settings):
    """Create the LLM transport based on config."""
    api_key = settings.llm.api_key or os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
    if not api_key:
        console.print("[red]Error:[/red] No API key found. Set api_key in sanfuclaw.toml or LLM_API_KEY env var")
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


async def _run(config_path: str, model: str | None, provider: str | None, channel_mode: str):
    """Main async entry point."""
    from sanfuclaw.core.config import Settings
    from sanfuclaw.agents.llm_agent import LLMAgent
    from sanfuclaw.gateway.router import Router
    from sanfuclaw.gateway.session_manager import SessionManager
    from sanfuclaw.storage.sqlite import SQLiteStore
    from sanfuclaw.tools.registry import ToolRegistry
    from sanfuclaw.tools.shell import ShellTool
    from sanfuclaw.tools.web_fetch import WebFetchTool

    # Load config
    settings = Settings.from_toml(config_path)
    if model:
        settings.llm.model = model
    if provider:
        settings.llm.provider = provider

    # Set up storage and session manager
    store = SQLiteStore()
    await store.init()

    session_manager = SessionManager(store)

    # Set up tools
    tool_registry = ToolRegistry()
    tool_registry.register(ShellTool())
    tool_registry.register(WebFetchTool())

    # Set up transport and agent
    transport = _build_transport(settings)
    agent = LLMAgent(
        name="default",
        transport=transport,
        tool_registry=tool_registry,
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
        cli = CLIChannel()
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
        await store.close()


@app.command()
def serve(
    config: str = typer.Option("sanfuclaw.toml", "--config", "-c", help="Path to config file"),
    host: str = typer.Option(None, "--host", "-h", help="Override host"),
    port: int = typer.Option(None, "--port", help="Override port"),
):
    """Start the Sanfuclaw gateway server (WebSocket + HTTP + WebChat)."""
    import uvicorn
    from sanfuclaw.core.config import Settings
    from sanfuclaw.gateway.server import GatewayServer

    settings = Settings.from_toml(config)
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
def version():
    """Show version."""
    from sanfuclaw import __version__
    console.print(f"sanfuclaw v{__version__}")


if __name__ == "__main__":
    app()
