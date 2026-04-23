"""`sanfuclaw cron ...` subcommands — manage scheduled tasks in SQLite."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from croniter import croniter
from rich.console import Console
from rich.table import Table

from sanfuclaw.core.schedule import Schedule
from sanfuclaw.gateway.scheduler import compute_next_run
from sanfuclaw.storage.sqlite import SQLiteStore

cron_app = typer.Typer(
    name="cron",
    help="Manage scheduled prompts (cron-driven).",
    no_args_is_help=True,
)
console = Console()


async def _with_store(fn):
    store = SQLiteStore()
    await store.init()
    try:
        return await fn(store)
    finally:
        await store.close()


def _fmt_dt(dt) -> str:
    return dt.isoformat(sep=" ", timespec="seconds") if dt else "-"


@cron_app.command("add")
def add(
    expr: str = typer.Argument(..., help="Cron expression, e.g. '0 8 * * *'"),
    channel: str = typer.Option(..., "--channel", "-c", help="Target channel (cli/telegram/weixin/webchat)"),
    prompt: str = typer.Option(..., "--prompt", "-p", help="Prompt text to send when triggered"),
    session: str = typer.Option("", "--session", "-s", help="Target session id (empty = SessionManager resolves by channel+sender)"),
):
    """Add a new scheduled task."""
    if not croniter.is_valid(expr):
        console.print(f"[red]Invalid cron expression:[/red] {expr!r}")
        raise typer.Exit(1)

    schedule = Schedule(
        cron=expr,
        prompt=prompt,
        target_channel=channel,
        target_session=session,
    )
    from datetime import datetime, timezone
    schedule.next_run_at = compute_next_run(expr, datetime.now(timezone.utc))

    async def _go(store):
        await store.add_schedule(schedule)
        return schedule

    s = asyncio.run(_with_store(_go))
    console.print(f"[green]Added[/green] schedule [cyan]{s.id}[/cyan] → {s.target_channel}")
    console.print(f"  cron:     {s.cron}")
    console.print(f"  prompt:   {s.prompt}")
    console.print(f"  next run: {_fmt_dt(s.next_run_at)}")


@cron_app.command("list")
def list_cmd():
    """List all scheduled tasks."""
    async def _go(store):
        return await store.list_schedules()

    schedules = asyncio.run(_with_store(_go))
    if not schedules:
        console.print("[dim]No schedules configured.[/dim]")
        console.print("Add one with: [bold]sanfuclaw cron add '<expr>' --channel <ch> --prompt '<text>'[/bold]")
        return

    table = Table(title="Schedules")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Cron", style="yellow")
    table.add_column("Channel", style="green")
    table.add_column("Next run")
    table.add_column("Prompt", max_width=40)

    for s in schedules:
        status = "[green]on[/green]" if s.enabled else "[dim]off[/dim]"
        prompt = s.prompt if len(s.prompt) <= 40 else s.prompt[:37] + "..."
        table.add_row(s.id, status, s.cron, s.target_channel, _fmt_dt(s.next_run_at), prompt.replace("\n", " "))

    console.print(table)


@cron_app.command("remove")
def remove(
    schedule_id: str = typer.Argument(..., help="Schedule ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a scheduled task."""
    async def _load(store):
        return await store.get_schedule(schedule_id)

    s = asyncio.run(_with_store(_load))
    if not s:
        console.print(f"[red]No such schedule:[/red] {schedule_id}")
        raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm(f"Remove schedule {schedule_id} ({s.cron} → {s.target_channel})?", default=False)
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    async def _go(store):
        await store.remove_schedule(schedule_id)

    asyncio.run(_with_store(_go))
    console.print(f"[green]Removed[/green] {schedule_id}")


@cron_app.command("enable")
def enable(schedule_id: str = typer.Argument(..., help="Schedule ID")):
    """Enable a schedule."""
    _set_enabled(schedule_id, True)


@cron_app.command("disable")
def disable(schedule_id: str = typer.Argument(..., help="Schedule ID")):
    """Disable a schedule (keeps the entry)."""
    _set_enabled(schedule_id, False)


def _set_enabled(schedule_id: str, enabled: bool) -> None:
    async def _go(store):
        s = await store.get_schedule(schedule_id)
        if not s:
            return None
        if s.enabled == enabled:
            return s
        s.enabled = enabled
        if enabled:
            from datetime import datetime, timezone
            s.next_run_at = compute_next_run(s.cron, datetime.now(timezone.utc))
        await store.update_schedule(s)
        return s

    s = asyncio.run(_with_store(_go))
    if s is None:
        console.print(f"[red]No such schedule:[/red] {schedule_id}")
        raise typer.Exit(1)
    word = "enabled" if enabled else "disabled"
    color = "green" if enabled else "yellow"
    console.print(f"[{color}]{word.capitalize()}[/{color}] {schedule_id}")
