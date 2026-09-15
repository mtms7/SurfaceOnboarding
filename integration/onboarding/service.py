"""Local composition root for queue reads and non-executing sync requests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .adapters import SyncScope
from .audit import AuditEvent, AuditEventType
from .audit_repository import InMemoryAuditRepository
from .authorization import Role
from .manual_review import ManualReviewDecision, record_manual_review
from .manual_review_repository import InMemoryManualReviewRepository
from .queue_view import QueueRow, queue_rows
from .rate_limit import InMemorySyncRateLimiter
from .repository import InMemoryWorkflowRepository
from .sync_job_repository import InMemorySyncJobRepository, SyncRequestResult
from .synthetic_intake import SyntheticIntakeCandidate, SyntheticIntakeResult, ingest_synthetic
from .synthetic_intake import validate_synthetic_batch
from .source_read import SourceReadResult


class LocalOnboardingService:
    """Coordinates pure in-memory components; it cannot perform external work.

    In particular, this class intentionally has no Salesforce client, worker
    start method, HTTP transport, browser adapter, or writeback capability.
    """

    def __init__(self, workflow_repository: InMemoryWorkflowRepository | None = None,
                 sync_job_repository: InMemorySyncJobRepository | None = None,
                 audit_repository: InMemoryAuditRepository | None = None,
                 manual_review_repository: InMemoryManualReviewRepository | None = None,
                 sync_rate_limiter: InMemorySyncRateLimiter | None = None) -> None:
        self._workflow_repository = workflow_repository or InMemoryWorkflowRepository()
        self._sync_job_repository = sync_job_repository or InMemorySyncJobRepository()
        self._audit_repository = audit_repository or InMemoryAuditRepository()
        self._manual_review_repository = manual_review_repository or InMemoryManualReviewRepository()
        self._sync_rate_limiter = sync_rate_limiter

    def request_sync(self, scope: SyncScope, *, requested_by: str, role: Role) -> SyncRequestResult:
        """Queue or coalesce a local metadata job; it does not execute it."""
        if self._sync_rate_limiter is not None:
            rate_limit = self._sync_rate_limiter.check(requested_by, now=datetime.now(timezone.utc))
            if not rate_limit.allowed:
                raise PermissionError("sync_request_rate_limited")
        result = self._sync_job_repository.request(scope, requested_by=requested_by, role=role)
        event_type = AuditEventType.SYNC_COALESCED if result.coalesced else AuditEventType.SYNC_REQUESTED
        self._audit_repository.record(AuditEvent(
            uuid4(), event_type, requested_by, datetime.now(timezone.utc), sync_job_id=result.job.job_id,
        ))
        return result

    def queue(self) -> tuple[QueueRow, ...]:
        """Return only the safe queue projection from local workflow metadata."""
        return queue_rows(self._workflow_repository)

    def ingest_synthetic(self, candidate: SyntheticIntakeCandidate) -> SyntheticIntakeResult:
        """Exercise local intake gates against synthetic references only."""
        result = ingest_synthetic(candidate, self._workflow_repository)
        if result.created or result.changed:
            event_type = (AuditEventType.WORKFLOW_CREATED if result.created
                          else AuditEventType.WORKFLOW_TRANSITIONED)
            self._audit_repository.record(AuditEvent(
                uuid4(), event_type, "local.synthetic", datetime.now(timezone.utc),
                workflow_record_id=result.item.salesforce_record_id,
            ))
        return result

    def ingest_synthetic_batch(self, result: SourceReadResult,
                               candidates: tuple[SyntheticIntakeCandidate, ...]) -> tuple[SyntheticIntakeResult, ...]:
        """Ingest a matching synthetic read snapshot; it cannot call a source system."""
        validate_synthetic_batch(result, candidates)
        return tuple(self.ingest_synthetic(candidate) for candidate in candidates)

    def audit_events(self) -> tuple[AuditEvent, ...]:
        """Return process-local, metadata-only audit events for local inspection."""
        return self._audit_repository.list_events()

    def record_manual_review(self, decision: ManualReviewDecision, *, role: Role) -> ManualReviewDecision:
        """Record a local reviewer decision; it does not release or execute work."""
        accepted = record_manual_review(role=role, decision=decision)
        saved = self._manual_review_repository.save_with_status(accepted)
        if saved.created:
            self._audit_repository.record(AuditEvent(
                uuid4(), AuditEventType.MANUAL_REVIEW_RECORDED, saved.decision.reviewer_id,
                datetime.now(timezone.utc), workflow_record_id=saved.decision.workflow_record_id,
            ))
        return saved.decision
