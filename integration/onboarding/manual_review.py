"""Metadata-only manual-review decisions for a future authenticated workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from .authorization import Role, authorize_manual_review
from .mappings import mapping_blocker
from .models import WorkflowItem, WorkflowState

_ACTOR_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


@dataclass(frozen=True, slots=True)
class ManualReviewDecision:
    """A reviewer decision bound to one exact non-sensitive workflow snapshot."""

    workflow_record_id: str
    source_revision: str
    intent_hash: str
    reviewer_id: str
    approved: bool
    decided_at: datetime

    def __post_init__(self) -> None:
        if not _RECORD_ID_PATTERN.fullmatch(self.workflow_record_id):
            raise ValueError("invalid_manual_review_record_id")
        if not self.source_revision or any(character.isspace() for character in self.source_revision):
            raise ValueError("invalid_manual_review_source_revision")
        if not self.intent_hash or any(character.isspace() for character in self.intent_hash):
            raise ValueError("invalid_manual_review_intent_hash")
        if not _ACTOR_PATTERN.fullmatch(self.reviewer_id):
            raise ValueError("invalid_manual_review_reviewer")
        if not isinstance(self.approved, bool):
            raise ValueError("invalid_manual_review_approved")
        if self.decided_at.tzinfo is None:
            raise ValueError("manual_review_timestamp_must_be_timezone_aware")


def record_manual_review(*, role: Role, decision: ManualReviewDecision) -> ManualReviewDecision:
    """Apply local role policy only; this does not persist or release a workflow."""
    authorize_manual_review(role)
    return decision


def can_release_manual_review(decision: ManualReviewDecision, item: WorkflowItem) -> bool:
    """Return true only for an exact approved snapshot with an enabled route."""
    return (
        decision.approved
        and item.state is WorkflowState.MANUAL_REVIEW_REQUIRED
        and decision.workflow_record_id == item.salesforce_record_id
        and decision.source_revision == item.source_revision
        and decision.intent_hash == item.intent_hash
        and mapping_blocker(item.engine_value) is None
    )
