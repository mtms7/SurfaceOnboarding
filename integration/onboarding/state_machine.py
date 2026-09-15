"""Pure transition rules; persistence must enforce these atomically later."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .mappings import mapping_blocker
from .models import ReasonCode, WorkflowItem, WorkflowState

_ALLOWED: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.DISCOVERED: frozenset({WorkflowState.VALIDATING, WorkflowState.BLOCKED}),
    WorkflowState.VALIDATING: frozenset({WorkflowState.PENDING, WorkflowState.MANUAL_REVIEW_REQUIRED, WorkflowState.READY, WorkflowState.BLOCKED}),
    WorkflowState.PENDING: frozenset({WorkflowState.VALIDATING, WorkflowState.BLOCKED}),
    WorkflowState.MANUAL_REVIEW_REQUIRED: frozenset({WorkflowState.VALIDATING, WorkflowState.BLOCKED}),
    WorkflowState.READY: frozenset({WorkflowState.EXECUTING, WorkflowState.BLOCKED}),
    WorkflowState.EXECUTING: frozenset({WorkflowState.VERIFYING, WorkflowState.UNCERTAIN_EXTERNAL_RESULT}),
    WorkflowState.VERIFYING: frozenset({WorkflowState.SCANNING, WorkflowState.BLOCKED, WorkflowState.UNCERTAIN_EXTERNAL_RESULT}),
    WorkflowState.SCANNING: frozenset({WorkflowState.FINISHED, WorkflowState.BLOCKED}),
    WorkflowState.FINISHED: frozenset({WorkflowState.COMPLETE, WorkflowState.BLOCKED}),
    WorkflowState.COMPLETE: frozenset(),
    WorkflowState.BLOCKED: frozenset(),
    WorkflowState.UNCERTAIN_EXTERNAL_RESULT: frozenset(),
}


def transition(item: WorkflowItem, target: WorkflowState, *, source_revision: str, reason_code: ReasonCode | None = None) -> WorkflowItem:
    """Return the next immutable item or reject stale and forbidden changes."""
    if source_revision != item.source_revision:
        raise ValueError("stale_source_revision")
    if target not in _ALLOWED[item.state]:
        raise ValueError("forbidden_workflow_transition")
    if target is WorkflowState.READY:
        blocker = mapping_blocker(item.engine_value)
        if blocker is not None:
            raise ValueError(blocker)
    if target is WorkflowState.UNCERTAIN_EXTERNAL_RESULT:
        reason_code = ReasonCode.UNCERTAIN_EXTERNAL_RESULT
    if target in {WorkflowState.BLOCKED, WorkflowState.MANUAL_REVIEW_REQUIRED} and reason_code is None:
        raise ValueError("blocked_or_review_transition_requires_reason")
    if target not in {
        WorkflowState.BLOCKED,
        WorkflowState.MANUAL_REVIEW_REQUIRED,
        WorkflowState.UNCERTAIN_EXTERNAL_RESULT,
    } and reason_code is not None:
        raise ValueError("nonterminal_transition_cannot_have_reason")
    return replace(item, state=target, reason_code=reason_code, updated_at=datetime.now(timezone.utc))
