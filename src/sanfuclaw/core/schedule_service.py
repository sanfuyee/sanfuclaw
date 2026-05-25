"""ScheduleService — narrow facade over Store for schedule CRUD.

Schedule tools used to take a `Store` directly; that coupled tool tests to
the full storage protocol. The service wraps `Store` and exposes only the
schedule operations the tools need, mirroring how memory tools depend on
`MemoryRegistry` rather than the storage layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sanfuclaw.core.schedule import Schedule
from sanfuclaw.gateway.scheduler import compute_next_run
from sanfuclaw.storage.base import Store


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleService:
    """CRUD operations for schedules, with cron parsing built in."""

    def __init__(self, store: Store, default_timezone: str = "UTC"):
        self._store = store
        self._default_timezone = default_timezone

    @property
    def default_timezone(self) -> str:
        return self._default_timezone

    async def create(
        self,
        cron: str,
        prompt: str,
        target_channel: str,
        target_session: str = "",
        enabled: bool = True,
        timezone_name: str | None = None,
    ) -> Schedule:
        tz = timezone_name or self._default_timezone
        schedule = Schedule(
            cron=cron,
            prompt=prompt,
            target_channel=target_channel,
            target_session=target_session,
            enabled=enabled,
        )
        schedule.next_run_at = compute_next_run(cron, _now(), tz)
        await self._store.add_schedule(schedule)
        return schedule

    async def get(self, schedule_id: str) -> Schedule | None:
        return await self._store.get_schedule(schedule_id)

    async def list(
        self,
        enabled_only: bool = False,
        target_channel: str | None = None,
        limit: int = 20,
    ) -> list[Schedule]:
        rows = await self._store.list_schedules(enabled_only=enabled_only)
        if target_channel:
            rows = [r for r in rows if r.target_channel == target_channel]
        return rows[:limit]

    async def set_enabled(
        self,
        schedule_id: str,
        enabled: bool,
        timezone_name: str | None = None,
    ) -> Schedule | None:
        schedule = await self._store.get_schedule(schedule_id)
        if not schedule:
            return None
        schedule.enabled = enabled
        if enabled:
            tz = timezone_name or self._default_timezone
            schedule.next_run_at = compute_next_run(schedule.cron, _now(), tz)
        await self._store.update_schedule(schedule)
        return schedule

    async def remove(self, schedule_id: str) -> bool:
        existing = await self._store.get_schedule(schedule_id)
        if not existing:
            return False
        await self._store.remove_schedule(schedule_id)
        return True
