"""`sanfuclaw service ...` — render and install systemd / launchd units.

Writes unit files to `~/.sanfuclaw/systemd/` (Linux) or `~/.sanfuclaw/launchd/`
(macOS) as the source of truth, then with `--enable` symlinks them into the
user-level systemd / launchd directory and starts them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from sanfuclaw.core import paths

console = Console()
service_app = typer.Typer(
    name="service",
    help="Manage system service integration (systemd on Linux, launchd on macOS).",
    no_args_is_help=True,
)


@dataclass
class Unit:
    name: str
    source: Path       # ~/.sanfuclaw/systemd/…  or  ~/.sanfuclaw/launchd/…
    target: Path       # ~/.config/systemd/user/…  or  ~/Library/LaunchAgents/…
    content: str


def _sanfuclaw_invocation() -> list[str]:
    """Resolve the command prefix that launches the agent.

    Prefers the console script installed next to the running Python (works
    for both venvs and system installs). Falls back to `<python> -m sanfuclaw`
    if the script can't be found (e.g. `pip install` placed scripts elsewhere).
    """
    bin_dir = Path(sys.executable).resolve().parent
    candidate = bin_dir / "sanfuclaw"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return [str(candidate)]
    return [sys.executable, "-m", "sanfuclaw"]


def _render_systemd_units() -> list[Unit]:
    invocation = _sanfuclaw_invocation()
    home_dir = str(Path.home())
    src_dir = paths.home() / "systemd"
    tgt_dir = Path.home() / ".config" / "systemd" / "user"
    exec_prefix = " ".join(invocation)

    agent = f"""[Unit]
Description=Sanfuclaw agent (channels)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_prefix} start --channel all
WorkingDirectory={home_dir}
StandardInput=null
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    serve = f"""[Unit]
Description=Sanfuclaw gateway (WebChat/REST/WS)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_prefix} serve
WorkingDirectory={home_dir}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    return [
        Unit("sanfuclaw-agent.service",
             src_dir / "sanfuclaw-agent.service",
             tgt_dir / "sanfuclaw-agent.service",
             agent),
        Unit("sanfuclaw-serve.service",
             src_dir / "sanfuclaw-serve.service",
             tgt_dir / "sanfuclaw-serve.service",
             serve),
    ]


def _render_launchd_units() -> list[Unit]:
    invocation = _sanfuclaw_invocation()
    home_dir = str(Path.home())
    src_dir = paths.home() / "launchd"
    tgt_dir = Path.home() / "Library" / "LaunchAgents"
    log_dir = paths.home()

    def _plist(label: str, extra_args: list[str], out: Path, err: Path) -> str:
        args = invocation + extra_args
        args_xml = "\n".join(f"    <string>{a}</string>" for a in args)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardInPath</key><string>/dev/null</string>
  <key>StandardOutPath</key><string>{out}</string>
  <key>StandardErrorPath</key><string>{err}</string>
  <key>WorkingDirectory</key><string>{home_dir}</string>
</dict>
</plist>
"""
    return [
        Unit("com.sanfuclaw.agent.plist",
             src_dir / "com.sanfuclaw.agent.plist",
             tgt_dir / "com.sanfuclaw.agent.plist",
             _plist("com.sanfuclaw.agent", ["start", "--channel", "all"],
                    log_dir / "agent.log", log_dir / "agent.err")),
        Unit("com.sanfuclaw.serve.plist",
             src_dir / "com.sanfuclaw.serve.plist",
             tgt_dir / "com.sanfuclaw.serve.plist",
             _plist("com.sanfuclaw.serve", ["serve"],
                    log_dir / "serve.log", log_dir / "serve.err")),
    ]


def _platform_units() -> tuple[str, list[Unit]]:
    """Return (manager_name, units) for the current OS, or exit with a
    message for unsupported platforms."""
    if sys.platform.startswith("linux"):
        return "systemd", _render_systemd_units()
    if sys.platform == "darwin":
        return "launchd", _render_launchd_units()
    console.print(f"[red]Error:[/red] `sanfuclaw service` is only supported on Linux and macOS (got {sys.platform}).")
    raise typer.Exit(1)


def _write_sources(units: list[Unit], force: bool) -> None:
    for u in units:
        u.source.parent.mkdir(parents=True, exist_ok=True)
        if u.source.exists() and not force:
            console.print(f"[yellow]Skipping existing[/yellow] {u.source} (use --force to overwrite)")
        else:
            u.source.write_text(u.content)
            console.print(f"[green]Wrote[/green] {u.source}")


def _print_manual_steps(manager: str, units: list[Unit]) -> None:
    console.print()
    console.print("[bold]To enable manually:[/bold]")
    tgt_parent = units[0].target.parent
    if manager == "systemd":
        console.print("  sudo loginctl enable-linger $USER   # one-time, survives logout")
        console.print(f"  mkdir -p {tgt_parent}")
        for u in units:
            console.print(f"  ln -sf {u.source} {u.target}")
        console.print("  systemctl --user daemon-reload")
        names = " ".join(u.name.removesuffix(".service") for u in units)
        console.print(f"  systemctl --user enable --now {names}")
    else:
        console.print(f"  mkdir -p {tgt_parent}")
        for u in units:
            console.print(f"  ln -sf {u.source} {u.target}")
            console.print(f"  launchctl load {u.target}")
    console.print()
    console.print("Or re-run with [bold]--enable[/bold] to do all of that automatically.")


def _link_targets(units: list[Unit]) -> None:
    units[0].target.parent.mkdir(parents=True, exist_ok=True)
    for u in units:
        if u.target.is_symlink() or u.target.exists():
            u.target.unlink()
        u.target.symlink_to(u.source)
        console.print(f"[green]Linked[/green] {u.target} -> {u.source}")


def _systemd_enable(units: list[Unit]) -> None:
    names = [u.name.removesuffix(".service") for u in units]
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", *names], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        console.print(f"[red]systemctl failed:[/red] {e}")
        console.print("If this is a container or SSH session without a user systemd instance,")
        console.print("enable linger first:  [bold]sudo loginctl enable-linger $USER[/bold]")
        raise typer.Exit(1)

    # Check linger status — running services won't survive logout without it.
    try:
        r = subprocess.run(
            ["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"],
            capture_output=True, text=True, check=False,
        )
        if "Linger=yes" not in r.stdout:
            console.print(
                "[yellow]Tip:[/yellow] run "
                "[bold]sudo loginctl enable-linger $USER[/bold] so the service "
                "keeps running after you log out."
            )
    except FileNotFoundError:
        pass

    console.print("[green]Enabled and started.[/green]")
    console.print("Status: [bold]systemctl --user status sanfuclaw-agent sanfuclaw-serve[/bold]")
    console.print("Logs:   [bold]journalctl --user -u sanfuclaw-agent -f[/bold]")


def _launchd_load(units: list[Unit]) -> None:
    for u in units:
        try:
            subprocess.run(["launchctl", "load", str(u.target)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            console.print(f"[red]launchctl load failed for {u.target}:[/red] {e}")
            raise typer.Exit(1)
    console.print("[green]Loaded via launchctl.[/green]")
    console.print("Status: [bold]launchctl list | grep sanfuclaw[/bold]")


@service_app.command("install")
def install(
    enable: bool = typer.Option(False, "--enable", help="Symlink into the user-level service dir and start immediately."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing unit files in ~/.sanfuclaw/."),
):
    """Render systemd (Linux) or launchd (macOS) unit files into ~/.sanfuclaw/."""
    manager, units = _platform_units()
    _write_sources(units, force)

    if not enable:
        _print_manual_steps(manager, units)
        return

    _link_targets(units)
    if manager == "systemd":
        _systemd_enable(units)
    else:
        _launchd_load(units)


@service_app.command("uninstall")
def uninstall():
    """Stop services and remove the user-level symlinks. Source files in ~/.sanfuclaw/ are kept."""
    manager, units = _platform_units()

    if manager == "systemd":
        names = [u.name.removesuffix(".service") for u in units]
        subprocess.run(["systemctl", "--user", "disable", "--now", *names], check=False)
        for u in units:
            if u.target.is_symlink() or u.target.exists():
                u.target.unlink()
                console.print(f"[green]Removed[/green] {u.target}")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    else:
        for u in units:
            if u.target.is_symlink() or u.target.exists():
                subprocess.run(["launchctl", "unload", str(u.target)], check=False)
                u.target.unlink()
                console.print(f"[green]Removed[/green] {u.target}")

    console.print(f"Source files kept under [dim]{paths.home()}[/dim] — delete them manually if unwanted.")


@service_app.command("status")
def status():
    """Show status of the installed services."""
    manager, units = _platform_units()
    if manager == "systemd":
        names = [u.name.removesuffix(".service") for u in units]
        subprocess.run(["systemctl", "--user", "status", "--no-pager", *names], check=False)
    else:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=False)
        matches = [ln for ln in r.stdout.splitlines() if "sanfuclaw" in ln]
        if matches:
            console.print("\n".join(matches))
        else:
            console.print("[yellow]No sanfuclaw services loaded in launchd.[/yellow]")
