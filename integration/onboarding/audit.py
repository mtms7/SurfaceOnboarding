"""Strict metadata-only audit-event contract for the future persistence layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from uuid import UUID

from .models import ReasonCode

_ACTOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


class AuditEventType(StrEnum):
    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_TRANSITIONED = "workflow_transitioned"
    MANUAL_REVIEW_RECORDED = "manual_review_recorded"
    SYNC_REQUESTED = "sync_requested"
    SYNC_COALESCED = "sync_coalesced"
    SYNC_STARTED = "sync_started"
    SYNC_FINISHED = "sync_finished"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Correlation metadata only; payloads, secrets, and responses are excluded."""

    event_id: UUID
    event_type: AuditEventType
    actor_id: str
    occurred_at: datetime
    workflow_record_id: str | None = None
    sync_job_id: UUID | None = None
    reason_code: ReasonCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, AuditEventType):
            raise ValueError("invalid_audit_event_type")
        if not _ACTOR_PATTERN.fullmatch(self.actor_id):
            raise ValueError("invalid_audit_actor")
        if self.occurred_at.tzinfo is None:
            raise ValueError("audit_timestamp_must_be_timezone_aware")
        if self.workflow_record_id is None and self.sync_job_id is None:
            raise ValueError("audit_event_requires_correlation_id")
        if self.workflow_record_id is not None and not _RECORD_ID_PATTERN.fullmatch(self.workflow_record_id):
            raise ValueError("invalid_audit_workflow_record_id")
        if self.sync_job_id is not None and not isinstance(self.sync_job_id, UUID):
            raise ValueError("invalid_audit_sync_job_id")
        if self.reason_code is not None and not isinstance(self.reason_code, ReasonCode):
            raise ValueError("invalid_audit_reason_code")
