"""Scheduler — fires synthetic envelopes on cron-driven ticks.

A schedule entry is "at this cron time, send this prompt as a user message
into <target_channel>". The Scheduler computes next-run times via croniter,
sleeps until the earliest one, then routes a synthesized Envelope through
the same Router that platform channels use. Replies stream back to the
target channel naturally — the user-facing channel doesn't know the message
came from cron.

Missed runs (sanfuclaw was down) are silently skipped: on startup we
recompute next_run_at from now, never backfill.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter

from sanfuclaw.core.message import Envelope, Message
from sanfuclaw.core.schedule import Schedule
from sanfuclaw.core.types import MessageRole
from sanfuclaw.gateway.router import Router
from sanfuclaw.storage.base import Store

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_next_run(cron_expr: str, base: datetime) -> datetime:
    """Next firing time for a cron expression, strictly after `base`."""
    it = croniter(cron_expr, base)
    return it.get_next(datetime)


class Scheduler:
    """Cron loop that synthesizes envelopes and routes them."""

    # Re-check the schedule table this often even if nothing seems imminent.
    # Lets newly-added entries get picked up without an explicit notify hook.
    POLL_INTERVAL = 60.0

    def __init__(self, store: Store, router: Router):
        self._store = store
        self._router = router
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        # Recompute next_run_at for any schedule whose stored value is missing
        # or already past — this is how we silently skip missed runs.
        schedules = await self._store.list_schedules(enabled_only=True)
        now = _now()
        for s in schedules:
            if s.next_run_at is None or s.next_run_at <= now:
                try:
                    s.next_run_at = compute_next_run(s.cron, now)
                except Exception as e:
                    logger.error(f"Schedule {s.id} has invalid cron {s.cron!r}: {e}")
                    continue
                await self._store.update_schedule(s)
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Scheduler started ({len(schedules)} active schedule(s))")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.exception(f"Scheduler tick failed: {e}")

            sleep_for = await self._compute_sleep()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        """Fire any schedules whose next_run_at has passed."""
        schedules = await self._store.list_schedules(enabled_only=True)
        now = _now()
        due = [s for s in schedules if s.next_run_at and s.next_run_at <= now]
        for s in due:
            try:
                await self._fire(s)
            except Exception as e:
                logger.error(f"Schedule {s.id} fire failed: {e}")
            s.last_run_at = now
            try:
                s.next_run_at = compute_next_run(s.cron, now)
            except Exception as e:
                logger.error(f"Schedule {s.id} cron {s.cron!r} broken; disabling: {e}")
                s.enabled = False
            await self._store.update_schedule(s)

    async def _compute_sleep(self) -> float:
        """How long to sleep before the next tick, capped at POLL_INTERVAL."""
        schedules = await self._store.list_schedules(enabled_only=True)
        now = _now()
        future = [
            (s.next_run_at - now).total_seconds()
            for s in schedules
            if s.next_run_at and s.next_run_at > now
        ]
        if not future:
            return self.POLL_INTERVAL
        return max(1.0, min(min(future), self.POLL_INTERVAL))

    async def _fire(self, schedule: Schedule) -> None:
        message = Message(
            role=MessageRole.USER,
            content=schedule.prompt,
            channel_id=schedule.target_channel,
            session_id=schedule.target_session,
            sender_id=f"cron:{schedule.id}",
            metadata={"schedule_id": schedule.id},
        )
        envelope = Envelope(message=message, source_channel=schedule.target_channel)
        logger.info(f"Scheduler firing {schedule.id} → {schedule.target_channel}")
        await self._router.route(envelope)
