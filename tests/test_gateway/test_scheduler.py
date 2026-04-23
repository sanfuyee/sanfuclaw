"""Scheduler loop tests — cron math, firing, and missed-run policy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from sanfuclaw.core.message import Envelope
from sanfuclaw.core.schedule import Schedule
from sanfuclaw.gateway.scheduler import Scheduler, compute_next_run
from sanfuclaw.storage.sqlite import SQLiteStore


class FakeRouter:
    """Records envelopes in lieu of actually routing them."""

    def __init__(self) -> None:
        self.routed: list[Envelope] = []

    async def route(self, envelope: Envelope) -> None:
        self.routed.append(envelope)


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStore(db_path=str(tmp_path / "scheduler.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def test_compute_next_run_is_strictly_after_base():
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert compute_next_run("*/5 * * * *", base) == datetime(
        2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc
    )
    # When base is exactly on a boundary, next_run must still advance.
    on_boundary = datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
    assert compute_next_run("*/5 * * * *", on_boundary) == datetime(
        2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc
    )


async def test_scheduler_fires_due_entry(store):
    """Scheduler routes an envelope when next_run_at elapses."""
    router = FakeRouter()
    scheduler = Scheduler(store=store, router=router)
    scheduler.POLL_INTERVAL = 1.0

    s = Schedule(
        cron="* * * * *",
        prompt="ping",
        target_channel="cli",
        target_session="sess-fire",
    )
    # Soon-but-not-past: Scheduler.start() rebases any next_run_at<=now
    # (the missed-run skip policy), so we give ~0.8s of headroom so the
    # loop's first tick catches it instead.
    s.next_run_at = datetime.now(timezone.utc) + timedelta(milliseconds=800)
    await store.add_schedule(s)

    await scheduler.start()
    try:
        for _ in range(60):
            if router.routed:
                break
            await asyncio.sleep(0.1)
    finally:
        await scheduler.stop()

    assert len(router.routed) >= 1
    env = router.routed[0]
    assert env.message.content == "ping"
    assert env.message.channel_id == "cli"
    assert env.message.session_id == "sess-fire"
    assert env.message.sender_id == f"cron:{s.id}"
    assert env.message.metadata.get("schedule_id") == s.id

    after = await store.get_schedule(s.id)
    assert after is not None
    assert after.last_run_at is not None
    assert after.next_run_at is not None and after.next_run_at > datetime.now(timezone.utc)


async def test_scheduler_skips_missed_runs(store):
    """A stale next_run_at must rebase forward on start(), not backfill."""
    router = FakeRouter()
    scheduler = Scheduler(store=store, router=router)
    scheduler.POLL_INTERVAL = 5.0

    s = Schedule(cron="0 8 * * *", prompt="morning", target_channel="cli")
    s.next_run_at = datetime.now(timezone.utc) - timedelta(days=3)
    await store.add_schedule(s)

    await scheduler.start()
    try:
        await asyncio.sleep(0.2)
    finally:
        await scheduler.stop()

    assert router.routed == []
    after = await store.get_schedule(s.id)
    assert after is not None
    assert after.next_run_at is not None
    assert after.next_run_at > datetime.now(timezone.utc)


async def test_scheduler_disables_schedule_with_broken_cron(store):
    """If cron becomes invalid mid-fire, the schedule is disabled, not crashed."""
    router = FakeRouter()
    scheduler = Scheduler(store=store, router=router)

    s = Schedule(cron="* * * * *", prompt="p", target_channel="cli")
    s.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await store.add_schedule(s)

    # Corrupt the cron in storage after insert so start()'s recompute still
    # works (it re-reads and uses current cron), then tamper before _tick.
    # Simpler: drive _tick() directly, bypassing the loop's sleep.
    s.cron = "not-a-cron"
    await store.update_schedule(s)

    await scheduler._tick()

    after = await store.get_schedule(s.id)
    assert after is not None
    assert after.enabled is False
