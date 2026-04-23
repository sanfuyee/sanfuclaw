"""SQLiteStore schedule CRUD round-trip tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sanfuclaw.core.schedule import Schedule
from sanfuclaw.gateway.scheduler import compute_next_run
from sanfuclaw.storage.sqlite import SQLiteStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStore(db_path=str(tmp_path / "schedules.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _make_schedule(**overrides) -> Schedule:
    defaults = dict(
        cron="0 8 * * *",
        prompt="morning brief",
        target_channel="cli",
        target_session="sess-1",
    )
    defaults.update(overrides)
    s = Schedule(**defaults)
    s.next_run_at = compute_next_run(s.cron, datetime.now(timezone.utc))
    return s


async def test_add_get_roundtrip(store):
    s = _make_schedule()
    await store.add_schedule(s)

    loaded = await store.get_schedule(s.id)
    assert loaded is not None
    assert loaded.id == s.id
    assert loaded.cron == s.cron
    assert loaded.prompt == s.prompt
    assert loaded.target_channel == s.target_channel
    assert loaded.target_session == s.target_session
    assert loaded.enabled is True
    assert loaded.next_run_at is not None
    # last_run_at is empty string in DB -> surfaced as None on read.
    assert loaded.last_run_at is None


async def test_get_missing_returns_none(store):
    assert await store.get_schedule("nonexistent") is None


async def test_list_and_enabled_only_filter(store):
    a = _make_schedule(prompt="a")
    b = _make_schedule(prompt="b")
    b.enabled = False
    await store.add_schedule(a)
    await store.add_schedule(b)

    all_rows = await store.list_schedules()
    assert {r.id for r in all_rows} == {a.id, b.id}

    enabled = await store.list_schedules(enabled_only=True)
    assert [r.id for r in enabled] == [a.id]


async def test_update_persists_all_fields(store):
    s = _make_schedule()
    await store.add_schedule(s)

    s.enabled = False
    s.prompt = "updated"
    s.target_channel = "telegram"
    s.target_session = "sess-2"
    s.last_run_at = datetime.now(timezone.utc)
    await store.update_schedule(s)

    loaded = await store.get_schedule(s.id)
    assert loaded is not None
    assert loaded.enabled is False
    assert loaded.prompt == "updated"
    assert loaded.target_channel == "telegram"
    assert loaded.target_session == "sess-2"
    assert loaded.last_run_at is not None


async def test_remove_schedule(store):
    s = _make_schedule()
    await store.add_schedule(s)
    assert await store.get_schedule(s.id) is not None

    await store.remove_schedule(s.id)
    assert await store.get_schedule(s.id) is None
    assert await store.list_schedules() == []
