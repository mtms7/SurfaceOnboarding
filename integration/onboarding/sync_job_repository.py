"""Pure local, single-flight coordination for metadata-only sync jobs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID, uuid4

from .adapters import SyncScope
from .authorization import Role, authorize_sync_request
from .sync_jobs import SyncFailureCategory, SyncJob, SyncJobStatus

_ACTIVE_STATUSES = frozenset({SyncJobStatus.QUEUED, SyncJobStatus.RUNNING})


@dataclass(frozen=True, slots=True)
class SyncRequestResult:
    job: SyncJob
    coalesced: bool


class InMemorySyncJobRepository:
    """Allow one queued or running job per fixed scope without executing it."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, SyncJob] = {}
        self._active_by_scope: dict[SyncScope, UUID] = {}
        self._lock = RLock()

    def request(self, scope: SyncScope, *, requested_by: str, role: Role,
                now: datetime | None = None) -> SyncRequestResult:
        """Create a queued job or return the existing active job for the scope."""
        authorize_sync_request(role)
        at = now or datetime.now(timezone.utc)
        with self._lock:
            active_id = self._active_by_scope.get(scope)
            if active_id is not None:
                active = self._jobs[active_id]
                if active.status in _ACTIVE_STATUSES:
                    return SyncRequestResult(active, coalesced=True)
                raise ValueError("sync_job_active_index_inconsistent")
            job = SyncJob(uuid4(), scope, requested_by, at, SyncJobStatus.QUEUED)
            self._jobs[job.job_id] = job
            self._active_by_scope[scope] = job.job_id
            return SyncRequestResult(job, coalesced=False)

    def start(self, job_id: UUID, *, now: datetime | None = None) -> SyncJob:
        with self._lock:
            job = self._require(job_id)
            if job.status is not SyncJobStatus.QUEUED:
                raise ValueError("sync_job_must_be_queued_to_start")
            started = replace(job, status=SyncJobStatus.RUNNING, started_at=now or datetime.now(timezone.utc))
            self._jobs[job_id] = started
            return started

    def finish(self, job_id: UUID, *, succeeded: bool, failure_category: SyncFailureCategory | None = None,
               now: datetime | None = None) -> SyncJob:
        with self._lock:
            job = self._require(job_id)
            if job.status is not SyncJobStatus.RUNNING:
                raise ValueError("sync_job_must_be_running_to_finish")
            if succeeded and failure_category is not None:
                raise ValueError("successful_sync_job_cannot_have_failure_category")
            if not succeeded and failure_category is None:
                raise ValueError("failed_sync_job_requires_failure_category")
            status = SyncJobStatus.SUCCEEDED if succeeded else SyncJobStatus.FAILED
            finished = replace(job, status=status, completed_at=now or datetime.now(timezone.utc),
                               failure_category=failure_category)
            self._jobs[job_id] = finished
            if self._active_by_scope.get(job.scope) == job_id:
                del self._active_by_scope[job.scope]
            return finished

    def get(self, job_id: UUID) -> SyncJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _require(self, job_id: UUID) -> SyncJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("sync_job_not_found")
        return job
