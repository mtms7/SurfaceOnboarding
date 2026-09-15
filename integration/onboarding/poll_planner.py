"""Pure calculation of configured poll times; it does not schedule or execute work."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import PollSchedule


def next_poll_times(schedule: PollSchedule, *, after: datetime, count: int = 2) -> tuple[datetime, ...]:
    """Return upcoming configured local times after an aware instant.

    The caller is responsible for any future timer or worker; this function has
    no side effects and no external-system capability.
    """
    if after.tzinfo is None:
        raise ValueError("poll_planner_requires_timezone_aware_after")
    if count < 1:
        raise ValueError("poll_planner_count_must_be_positive")
    timezone = ZoneInfo(schedule.timezone)
    local_after = after.astimezone(timezone)
    candidates: list[datetime] = []
    day = local_after.date()
    while len(candidates) < count:
        for configured_time in sorted(schedule.times):
            candidate = datetime.combine(day, configured_time, tzinfo=timezone)
            if candidate > local_after:
                candidates.append(candidate)
                if len(candidates) == count:
                    break
        day += timedelta(days=1)
    return tuple(candidates)
