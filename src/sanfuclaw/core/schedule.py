"""Schedule model — a cron-driven prompt that fires into a target channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _short_id() -> str:
    return uuid4().hex[:8]


@dataclass
class Schedule:
    """A cron entry the Scheduler fires on tick."""

    cron: str
    prompt: str
    target_channel: str
    id: str = field(default_factory=_short_id)
    target_session: str = ""
    enabled: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime = field(default_factory=_now)
