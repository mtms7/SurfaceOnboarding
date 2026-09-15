"""Configurable in-memory rate limiting for future on-demand sync entry points."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from threading import RLock

_ACTOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class SyncRateLimitPolicy:
    max_requests: int
    window: timedelta

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError("rate_limit_max_requests_must_be_positive")
        if self.window <= timedelta(0):
            raise ValueError("rate_limit_window_must_be_positive")


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.allowed != (self.retry_after_seconds is None):
            raise ValueError("rate_limit_result_must_be_consistent")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 1:
            raise ValueError("rate_limit_retry_after_must_be_positive")


class InMemorySyncRateLimiter:
    """Track request timestamps by safe actor identifier; it has no execution behavior."""

    def __init__(self, policy: SyncRateLimitPolicy) -> None:
        self._policy = policy
        self._requests: dict[str, deque[datetime]] = {}
        self._lock = RLock()

    def check(self, actor_id: str, *, now: datetime) -> RateLimitResult:
        """Record an allowed local request or report its deterministic retry delay."""
        if not _ACTOR_PATTERN.fullmatch(actor_id):
            raise ValueError("invalid_rate_limit_actor")
        if now.tzinfo is None:
            raise ValueError("rate_limit_timestamp_must_be_timezone_aware")
        cutoff = now - self._policy.window
        with self._lock:
            requests = self._requests.setdefault(actor_id, deque())
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self._policy.max_requests:
                retry_at = requests[0] + self._policy.window
                seconds = max(1, int((retry_at - now).total_seconds()))
                return RateLimitResult(False, seconds)
            requests.append(now)
            return RateLimitResult(True)
