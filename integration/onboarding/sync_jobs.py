"""Non-sensitive contracts for local Salesforce synchronization jobs.

These models describe scheduling metadata only. They deliberately have no
field that can carry a Salesforce result, comment, contact, domain, or other
source payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from uuid import UUID

from .adapters import SyncScope

_ACTOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class SyncJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SyncFailureCategory(StrEnum):
    AUTHENTICATION = "authentication"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    VALIDATION = "validation"


@dataclass(frozen=True, slots=True)
class SyncJob:
    """Metadata-only job contract for a fixed, validated synchronization scope."""

    job_id: UUID
    scope: SyncScope
    requested_by: str
    requested_at: datetime
    status: SyncJobStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_category: SyncFailureCategory | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SyncScope):
            raise ValueError("sync_job_requires_validated_scope")
        if not _ACTOR_PATTERN.fullmatch(self.requested_by):
            raise ValueError("sync_job_invalid_requester")
        if not isinstance(self.status, SyncJobStatus):
            raise ValueError("sync_job_invalid_status")
        if self.requested_at.tzinfo is None:
            raise ValueError("sync_job_requested_at_must_be_timezone_aware")
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError("sync_job_started_at_must_be_timezone_aware")
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            raise ValueError("sync_job_completed_at_must_be_timezone_aware")
        if self.started_at is not None and self.started_at < self.requested_at:
            raise ValueError("sync_job_started_before_requested")
        if self.completed_at is not None and (self.started_at is None or self.completed_at < self.started_at):
            raise ValueError("sync_job_invalid_completion_time")
        if self.status is SyncJobStatus.QUEUED and (self.started_at is not None or self.completed_at is not None):
            raise ValueError("queued_sync_job_cannot_have_execution_times")
        if self.status is SyncJobStatus.RUNNING and (self.started_at is None or self.completed_at is not None):
            raise ValueError("running_sync_job_requires_start_only")
        if self.status is SyncJobStatus.SUCCEEDED and (self.started_at is None or self.completed_at is None):
            raise ValueError("succeeded_sync_job_requires_completion")
        if self.status is SyncJobStatus.FAILED:
            if self.started_at is None or self.completed_at is None:
                raise ValueError("failed_sync_job_requires_completion")
            if not isinstance(self.failure_category, SyncFailureCategory):
                raise ValueError("failed_sync_job_requires_failure_category")
        elif self.failure_category is not None:
            raise ValueError("nonfailed_sync_job_cannot_have_failure_category")
