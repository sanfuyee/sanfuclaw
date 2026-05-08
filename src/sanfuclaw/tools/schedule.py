"""Schedule tools — allow the agent to manage cron schedules in-session."""

from __future__ import annotations

from typing import Any

from croniter import croniter

from sanfuclaw.core.errors import ToolError
from sanfuclaw.core.schedule_service import ScheduleService
from sanfuclaw.core.session import Session


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
            "timezone": {
                "type": "string",
                "description": "IANA timezone for cron interpretation, e.g. 'Asia/Shanghai'.",
            },
        },
        "required": ["cron", "prompt"],
    }

    def __init__(self, service: ScheduleService):
        self._service = service

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
        timezone_name = (
            str(params.get("timezone") or self._service.default_timezone).strip()
        )
        schedule = await self._service.create(
            cron=cron_expr,
            prompt=prompt,
            target_channel=target_channel,
            target_session=target_session,
            enabled=enabled,
            timezone_name=timezone_name,
        )

        return {
            "ok": True,
            "id": schedule.id,
            "cron": schedule.cron,
            "prompt": schedule.prompt,
            "target_channel": schedule.target_channel,
            "target_session": schedule.target_session,
            "enabled": schedule.enabled,
            "timezone": timezone_name,
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

    def __init__(self, service: ScheduleService):
        self._service = service

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        enabled_only = bool(params.get("enabled_only", False))
        target_channel = str(params.get("target_channel", "")).strip() or None
        limit = int(params.get("limit", 20))
        limit = max(1, min(limit, 50))

        rows = await self._service.list(
            enabled_only=enabled_only,
            target_channel=target_channel,
            limit=limit,
        )

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
            "timezone": {
                "type": "string",
                "description": "IANA timezone for cron interpretation when enabling.",
            },
        },
        "required": ["id", "enabled"],
    }

    def __init__(self, service: ScheduleService):
        self._service = service

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        schedule_id = str(params.get("id", "")).strip()
        if not schedule_id:
            raise ToolError("Missing required field: id")
        enabled = bool(params["enabled"])
        timezone_name = (
            str(params.get("timezone") or self._service.default_timezone).strip()
        )

        schedule = await self._service.set_enabled(
            schedule_id, enabled, timezone_name=timezone_name,
        )
        if not schedule:
            raise ToolError(f"No such schedule: {schedule_id}")

        return {
            "ok": True,
            "id": schedule.id,
            "enabled": schedule.enabled,
            "timezone": timezone_name,
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

    def __init__(self, service: ScheduleService):
        self._service = service

    async def execute(self, params: dict[str, Any], session: Session) -> dict[str, Any]:
        schedule_id = str(params.get("id", "")).strip()
        if not schedule_id:
            raise ToolError("Missing required field: id")
        removed = await self._service.remove(schedule_id)
        if not removed:
            raise ToolError(f"No such schedule: {schedule_id}")
        return {"ok": True, "id": schedule_id, "removed": True}
