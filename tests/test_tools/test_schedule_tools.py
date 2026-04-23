"""Schedule tool tests."""

from __future__ import annotations

import pytest

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.session import Session
from sanfuclaw.storage.sqlite import SQLiteStore
from sanfuclaw.tools.schedule import (
    ScheduleCreateTool,
    ScheduleListTool,
    ScheduleRemoveTool,
    ScheduleSetEnabledTool,
)


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStore(db_path=str(tmp_path / "schedule-tools.db"))
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def session() -> Session:
    return Session(id="tg-12345", channel_id="telegram", sender_id="u1")


async def test_schedule_create_defaults_to_current_conversation(store, session):
    tool = ScheduleCreateTool(store)

    result = await tool.execute(
        {"cron": "0 14 * * *", "prompt": "Send tomorrow weather"},
        session,
    )

    assert result["ok"] is True
    assert result["target_channel"] == "telegram"
    assert result["target_session"] == "tg-12345"
    assert result["next_run_at"]

    row = await store.get_schedule(result["id"])
    assert row is not None
    assert row.target_channel == "telegram"
    assert row.target_session == "tg-12345"


async def test_schedule_create_rejects_invalid_cron(store, session):
    tool = ScheduleCreateTool(store)
    with pytest.raises(ToolError):
        await tool.execute({"cron": "not-a-cron", "prompt": "x"}, session)


async def test_schedule_list_filter_enable_disable_and_remove(store, session):
    create = ScheduleCreateTool(store)
    list_tool = ScheduleListTool(store)
    set_enabled = ScheduleSetEnabledTool(store)
    remove = ScheduleRemoveTool(store)

    a = await create.execute({"cron": "0 9 * * *", "prompt": "a"}, session)
    b = await create.execute(
        {
            "cron": "0 10 * * *",
            "prompt": "b",
            "target_channel": "cli",
            "target_session": "cli-abc",
        },
        session,
    )

    all_rows = await list_tool.execute({}, session)
    assert all_rows["count"] == 2

    tg_rows = await list_tool.execute({"target_channel": "telegram"}, session)
    assert tg_rows["count"] == 1
    assert tg_rows["items"][0]["id"] == a["id"]

    await set_enabled.execute({"id": a["id"], "enabled": False}, session)
    enabled_rows = await list_tool.execute({"enabled_only": True}, session)
    assert enabled_rows["count"] == 1
    assert enabled_rows["items"][0]["id"] == b["id"]

    removed = await remove.execute({"id": b["id"]}, session)
    assert removed["removed"] is True
    after = await list_tool.execute({}, session)
    assert after["count"] == 1
    assert after["items"][0]["id"] == a["id"]
