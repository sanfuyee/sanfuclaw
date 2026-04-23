"""Central logging setup for CLI entrypoints.

All sanfuclaw modules do `logger = logging.getLogger(__name__)`, but without a
root handler those `logger.info(...)` calls would go nowhere. `configure()`
wires up a stderr handler on first call; the CLI typer callback runs it before
any subcommand so both `sanfuclaw start` and `sanfuclaw serve` get logs.

Level precedence: explicit arg > `$SANFUCLAW_LOG_LEVEL` > `INFO`.
Set `SANFUCLAW_LOG_LEVEL=DEBUG` to surface per-envelope router traffic.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure(level: str | int | None = None) -> None:
    """Attach a stderr handler to the root logger. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved = level or os.environ.get("SANFUCLAW_LOG_LEVEL", "INFO")
    if isinstance(resolved, str):
        resolved = resolved.upper()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(resolved)

    # Dampen library chatter unless DEBUG was explicitly requested.
    if resolved != "DEBUG" and resolved != logging.DEBUG:
        for noisy in ("httpx", "httpcore", "telegram", "telegram.ext"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
