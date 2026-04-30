"""Central logging setup for CLI entrypoints.

All sanfuclaw modules do `logger = logging.getLogger(__name__)`, but without a
root handler those `logger.info(...)` calls would go nowhere. `configure()`
wires up a stderr handler on first call; the CLI typer callback runs it before
any subcommand so both `sanfuclaw start` and `sanfuclaw serve` get logs.

Level precedence: explicit arg > `$SANFUCLAW_LOG_LEVEL` > `INFO`.
Set `SANFUCLAW_LOG_LEVEL=DEBUG` to surface per-envelope router traffic.

Format precedence: `$SANFUCLAW_LOG_FORMAT=json` switches to JSON-line output;
otherwise the default human-readable format.

If `$SANFUCLAW_LOG_FILE` is set, log records are also appended there (rotating
at 5 MB × 3 backups). The CLI/serve startup banner is the same line that goes
to stderr, so the file becomes a complete record without any extra wiring.

JSON mode emits one object per line with `ts`, `level`, `logger`, `msg`, plus
any context fields the call site attached via `logger.info(..., extra={...})`.
The agent and tools tag records with `session_id`, `turn_id`, `tool` so log
streams can be filtered by conversation or tool invocation downstream.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_CONFIGURED = False
_RESOLVED_LEVEL: str | int = "INFO"
_RESOLVED_FORMAT: str = "text"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Standard LogRecord attributes — anything else on a record was added by the
# call site via `extra=` and should appear in the JSON output as a context
# field. Sourced from the cpython logging docs.
_STANDARD_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class JSONFormatter(logging.Formatter):
    """One JSON object per record. Extra fields flow through verbatim."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure(level: str | int | None = None, fmt: str | None = None) -> None:
    """Attach handlers to the root logger. Idempotent."""
    global _CONFIGURED, _RESOLVED_LEVEL, _RESOLVED_FORMAT
    if _CONFIGURED:
        return

    resolved = level or os.environ.get("SANFUCLAW_LOG_LEVEL", "INFO")
    if isinstance(resolved, str):
        resolved = resolved.upper()

    fmt_choice = (fmt or os.environ.get("SANFUCLAW_LOG_FORMAT", "text")).lower()

    def _make_formatter() -> logging.Formatter:
        return JSONFormatter() if fmt_choice == "json" else logging.Formatter(_FORMAT, _DATE_FORMAT)

    root = logging.getLogger()
    if not root.handlers:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(_make_formatter())
        root.addHandler(stream_handler)

        log_file = os.environ.get("SANFUCLAW_LOG_FILE", "").strip()
        if log_file:
            from logging.handlers import RotatingFileHandler
            from pathlib import Path

            path = Path(log_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
            )
            file_handler.setFormatter(_make_formatter())
            root.addHandler(file_handler)

    root.setLevel(resolved)

    # Dampen library chatter unless DEBUG was explicitly requested.
    if resolved != "DEBUG" and resolved != logging.DEBUG:
        for noisy in (
            "httpx", "httpcore", "telegram", "telegram.ext",
            "openai", "anthropic", "asyncio", "websockets",
            "uvicorn.error", "uvicorn.access",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _RESOLVED_LEVEL = resolved
    _RESOLVED_FORMAT = fmt_choice
    _CONFIGURED = True


def current_level() -> str:
    """Return the resolved log level as a string (post-configure)."""
    lvl = _RESOLVED_LEVEL
    if isinstance(lvl, int):
        return logging.getLevelName(lvl)
    return str(lvl)


def current_format() -> str:
    """Return the resolved format choice ('text' or 'json')."""
    return _RESOLVED_FORMAT


def redact_secret(value: str, keep: int = 4) -> str:
    """Render a credential safely for logs: shows length + last `keep` chars."""
    if not value:
        return "(empty)"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * (len(value) - keep)}{value[-keep:]} (len={len(value)})"
