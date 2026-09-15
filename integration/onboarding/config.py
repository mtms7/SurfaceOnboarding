"""Non-secret configuration contracts for the local scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TargetEnvironment(StrEnum):
    LEONARDO_DEVELOPMENT = "leonardo_development"


@dataclass(frozen=True, slots=True)
class PollSchedule:
    """Two daily local poll times; deployment ownership remains a later gate."""

    timezone: str
    times: tuple[time, time]

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid_poll_timezone") from exc
        if len(self.times) != 2 or not all(isinstance(value, time) for value in self.times):
            raise ValueError("poll_schedule_requires_two_times")
        if self.times[0] == self.times[1]:
            raise ValueError("poll_schedule_times_must_differ")


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Configuration with no endpoint, credential, token, or secret fields."""

    target_environment: TargetEnvironment
    poll_schedule: PollSchedule
    web_enabled: bool = False

    def __post_init__(self) -> None:
        if self.target_environment is not TargetEnvironment.LEONARDO_DEVELOPMENT:
            raise ValueError("production_and_unknown_targets_are_blocked")
        if not isinstance(self.poll_schedule, PollSchedule):
            raise ValueError("invalid_poll_schedule")
        if self.web_enabled:
            raise ValueError("web_listener_requires_identity_approval")
