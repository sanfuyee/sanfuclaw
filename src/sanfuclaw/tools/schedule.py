"""Schedule tools — allow the agent to manage cron schedules in-session."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from croniter import croniter

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.schedule import Schedule
from sanfuclaw.core.session import Session
from sanfuclaw.gateway.scheduler import compute_next_run
from sanfuclaw.storage.base import Store


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleCreateTool:
    """Create a schedule row from structured parameters."""

    name = "schedule_create"
    description = (
        "Create a scheduled task. Use when the user asks reminders like "
        "'every day at 2pm' or 'weekly'. Convert natural language time to cron."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "cron": {
                "type": "string",
                "description": "Cron expression, e.g. '0 14 * * *'.",
            },
            "prompt": {
                "type": "string",
                "description": "Prompt text to send when the schedule fires.",
            },
            "target_channel": {
                "type": "string",
                "description": "Optional channel override (defaults to current channel).",
            },
            "target_session": {
                "type": "string",
                "description": "Optional session override (defaults to current session when channel is unchanged).",
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether the schedule starts enabled (default: true).",
            },
        },
        "required": ["cron", "prompt"],
    }

    def __init__(self, store: Store):
        self._store = store

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        cron_expr = str(params.get("cron", "")).strip()
        prompt = str(params.get("prompt", "")).strip()
        if not cron_expr:
            raise ToolError("Missing required field: cron")
        if not prompt:
            raise ToolError("Missing required field: prompt")
        if not croniter.is_valid(cron_expr):
            raise ToolError(f"Invalid cron expression: {cron_expr!r}")

        target_channel = str(params.get("target_channel") or session.channel_id).strip()
        if not target_channel:
            raise ToolError("Unable to resolve target_channel")

        target_session_raw = params.get("target_session")
        if target_session_raw is None:
            target_session = session.id if target_channel == session.channel_id else ""
        else:
            target_session = str(target_session_raw).strip()

        enabled = bool(params.get("enabled", True))
        schedule = Schedule(
            cron=cron_expr,
            prompt=prompt,
            target_channel=target_channel,
            target_session=target_session,
            enabled=enabled,
        )
        schedule.next_run_at = compute_next_run(cron_expr, _now())
        await self._store.add_schedule(schedule)

        return {
            "ok": True,
            "id": schedule.id,
            "cron": schedule.cron,
            "prompt": schedule.prompt,
            "target_channel": schedule.target_channel,
            "target_session": schedule.target_session,
            "enabled": schedule.enabled,
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else "",
        }


class ScheduleListTool:
    """List schedule rows for management in chat."""

    name = "schedule_list"
    description = "List scheduled tasks. Optionally filter by channel or enabled status."
    parameters_schema = {
        "type": "object",
        "properties": {
            "target_channel": {
                "type": "string",
                "description": "Optional channel filter.",
            },
            "enabled_only": {
                "type": "boolean",
                "description": "Only list enabled schedules.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of rows to return (default: 20, max: 50).",
            },
        },
        "required": [],
    }

    def __init__(self, store: Store):
        self._store = store

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        enabled_only = bool(params.get("enabled_only", False))
        target_channel = str(params.get("target_channel", "")).strip()
        limit = int(params.get("limit", 20))
        limit = max(1, min(limit, 50))

        rows = await self._store.list_schedules(enabled_only=enabled_only)
        if target_channel:
            rows = [r for r in rows if r.target_channel == target_channel]
        rows = rows[:limit]

        return {
            "ok": True,
            "count": len(rows),
            "items": [
                {
                    "id": s.id,
                    "cron": s.cron,
                    "prompt": s.prompt,
                    "target_channel": s.target_channel,
                    "target_session": s.target_session,
                    "enabled": s.enabled,
                    "last_run_at": s.last_run_at.isoformat() if s.last_run_at else "",
                    "next_run_at": s.next_run_at.isoformat() if s.next_run_at else "",
                }
                for s in rows
            ],
        }


class ScheduleSetEnabledTool:
    """Enable or disable an existing schedule."""

    name = "schedule_set_enabled"
    description = "Enable or disable a schedule by id."
    parameters_schema = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Schedule id.",
            },
            "enabled": {
                "type": "boolean",
                "description": "True to enable, false to disable.",
            },
        },
        "required": ["id", "enabled"],
    }

    def __init__(self, store: Store):
        self._store = store

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        schedule_id = str(params.get("id", "")).strip()
        if not schedule_id:
            raise ToolError("Missing required field: id")
        enabled = bool(params["enabled"])

        schedule = await self._store.get_schedule(schedule_id)
        if not schedule:
            raise ToolError(f"No such schedule: {schedule_id}")

        schedule.enabled = enabled
        if enabled:
            schedule.next_run_at = compute_next_run(schedule.cron, _now())
        await self._store.update_schedule(schedule)

        return {
            "ok": True,
            "id": schedule.id,
            "enabled": schedule.enabled,
            "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else "",
        }


class ScheduleRemoveTool:
    """Delete an existing schedule."""

    name = "schedule_remove"
    description = "Delete a schedule by id."
    parameters_schema = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Schedule id.",
            },
        },
        "required": ["id"],
    }

    def __init__(self, store: Store):
        self._store = store

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        schedule_id = str(params.get("id", "")).strip()
        if not schedule_id:
            raise ToolError("Missing required field: id")
        schedule = await self._store.get_schedule(schedule_id)
        if not schedule:
            raise ToolError(f"No such schedule: {schedule_id}")
        await self._store.remove_schedule(schedule_id)
        return {"ok": True, "id": schedule_id, "removed": True}
