# Operations

Running sanfuclaw in production: background services, logs, scheduled
tasks, deployment notes. For install / first-run setup see the top-level
[README](../README.md).

## Background services

The bundled `sanfuclaw service` command wraps **systemd** (Linux) and
**launchd** (macOS). It renders unit files into `~/.sanfuclaw/systemd/`
or `~/.sanfuclaw/launchd/` with the `sanfuclaw` binary's absolute path
baked in (works for both the prebuilt binary and a `pip install` source
checkout), then optionally symlinks them into the user-level service
directory and starts them.

```bash
# Quick foreground smoke test first
sanfuclaw start --channel all     # messaging channels
sanfuclaw serve                   # WebChat + REST + WS gateway

# Render + audit the unit files (prints the commands needed to activate)
sanfuclaw service install

# Or do everything — symlink, daemon-reload, enable --now
sanfuclaw service install --enable

# Day-to-day
sanfuclaw service status
sanfuclaw service uninstall       # stop + unlink; sources kept under ~/.sanfuclaw/

# Linux only: keep services alive after logout (one-time)
sudo loginctl enable-linger $USER
```

Two units are installed side-by-side:

| Unit | Command | Purpose |
|---|---|---|
| `sanfuclaw-agent` / `com.sanfuclaw.agent` | `sanfuclaw start --channel all` | Message channels (Telegram, WeChat) |
| `sanfuclaw-serve` / `com.sanfuclaw.serve` | `sanfuclaw serve` | WebChat UI + REST + WebSocket |

The rendered files in `~/.sanfuclaw/systemd/*.service` (or
`launchd/*.plist`) are the source of truth — the system directory just
holds symlinks. Edit them in place for custom flags, then reload:

- **systemd** — `systemctl --user daemon-reload && systemctl --user restart sanfuclaw-agent sanfuclaw-serve`
- **launchd** — `launchctl unload <plist> && launchctl load <plist>`

### Without a service manager (nohup)

For a quick, non-persistent background run:

```bash
nohup sanfuclaw start --channel all < /dev/null > ~/.sanfuclaw/agent.log 2>&1 &
nohup sanfuclaw serve               < /dev/null > ~/.sanfuclaw/serve.log 2>&1 &
pkill -f sanfuclaw                   # stop everything
```

Stops when the terminal / session goes away — fine for quick checks,
not for daily use.

## Logs

Where runtime output goes depends on the service manager.

### Linux (systemd → journald)

```bash
# Stream agent + serve together (Ctrl-C to exit)
journalctl --user -u sanfuclaw-agent -u sanfuclaw-serve -f

# Just agent, last 100 lines
journalctl --user -u sanfuclaw-agent -n 100

# Errors only, today
journalctl --user -u sanfuclaw-agent -p err --since today

# Snapshot status (running / failed / inactive)
systemctl --user status sanfuclaw-agent sanfuclaw-serve
```

journald rotates automatically — no manual cleanup needed.

### macOS (launchd → files under `~/.sanfuclaw/`)

```bash
# Stream both units together
tail -f ~/.sanfuclaw/agent.log ~/.sanfuclaw/serve.log

# Just the last chunk
tail -100 ~/.sanfuclaw/agent.log

# Errors (stderr)
tail -f ~/.sanfuclaw/agent.err ~/.sanfuclaw/serve.err

# Snapshot status — first column is the PID (number = running, `-` = loaded but dead)
launchctl list | grep sanfuclaw

# Truncate if the file grows too big (the running service keeps writing)
: > ~/.sanfuclaw/agent.log
```

The file paths are baked into the rendered plists (see
`src/sanfuclaw/cli_service.py`), so they're stable across restarts.

### Empty log = service never started

If `agent.log` is 0 bytes or `journalctl` has no entries, the service
tried to start and failed before the first log line. Typical causes:

- `llm.api_key` empty → `MissingAPIKey` at startup
- Telegram `bot_token` invalid → Telegram handshake fails
- Gateway port 30423 already in use → `sanfuclaw-serve` can't bind

Check the error stream (`.err` file on macOS, same `journalctl` command
on Linux) for the traceback.

### Chat history is separate

The commands above show **runtime logs** (process startup, tool calls,
errors). Actual conversation content (user/assistant messages) lives in
SQLite, not in the logs. Browse it with:

```bash
sanfuclaw sessions               # recent sessions across all channels
sanfuclaw sessions --channel telegram
sanfuclaw start --resume <id>    # replay / continue a session
```

## Scheduled tasks

Cron-driven prompts that fire into any channel. Either add via CLI:

```bash
sanfuclaw cron add "0 8 * * *" --channel telegram --prompt "今日天气和待办"
sanfuclaw cron list
sanfuclaw cron disable <ID>      # pause without deleting
sanfuclaw cron remove <ID>
```

Or just ask in chat (the agent has `schedule_*` tools registered):

> 每天下午2点给我发明天的天气

Cron expressions are interpreted in `Settings.timezone` (default
`Asia/Shanghai`); change it via the top-level `timezone` field in
`~/.sanfuclaw/config.json`.

Missed runs are silently skipped — if sanfuclaw was off overnight, the
8am task simply runs at 8am the next day. There's no backfill.

## Operational notes

- **Config changes require a restart** — no hot reload. Use the
  `service` command or `systemctl --user restart …` /
  `launchctl unload && load …`.
- **Exposing the gateway** — `gateway.host` is `127.0.0.1` by default.
  Set it to `0.0.0.0` to listen on all interfaces, open the firewall
  for `gateway.port`, and front it with nginx + HTTPS rather than
  exposing the raw port.
- **MCP servers** are spawned and supervised by sanfuclaw itself — no
  separate service unit needed. See [docs/mcp.md](mcp.md) for the
  recommended bundle and token-cost trade-offs.
- **Backups** — everything persistent lives under `~/.sanfuclaw/`
  (config, sessions DB, skills, WeChat credentials, rendered units).
  Archive that one directory.
- **WeChat on an overseas VPS** — `ilinkai.weixin.qq.com` is
  IP-restricted and usually unreachable from outside mainland China.
  Run `sanfuclaw weixin-login` on a local / domestic machine, then
  `scp ~/.sanfuclaw/weixin_credentials.json vps:~/.sanfuclaw/`. If
  runtime messaging also gets blocked, keep the WeChat channel on a
  reachable host and run the other channels on the VPS.
