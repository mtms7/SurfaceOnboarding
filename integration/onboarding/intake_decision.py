"""Non-sensitive intake decision combining mapping and large-scope gates."""

from __future__ import annotations

from dataclasses import dataclass

from .mappings import mapping_blocker
from .models import ReasonCode, WorkflowState
from .scope_policy import ScopeCounts, manual_review_reason


@dataclass(frozen=True, slots=True)
class IntakeDecision:
    state: WorkflowState
    reason_code: ReasonCode | None


def decide_intake(engine_value: object, counts: ScopeCounts, *, policy_flag: bool = False) -> IntakeDecision:
    """Return the next pre-execution state using only route and count metadata."""
    if not isinstance(counts, ScopeCounts):
        raise ValueError("intake_decision_requires_scope_counts")
    mapping_reason = mapping_blocker(engine_value)
    if mapping_reason is not None:
        return IntakeDecision(WorkflowState.BLOCKED, mapping_reason)
    scope_reason = manual_review_reason(counts, policy_flag=policy_flag)
    if scope_reason is not None:
        return IntakeDecision(WorkflowState.MANUAL_REVIEW_REQUIRED, scope_reason)
    return IntakeDecision(WorkflowState.PENDING, None)
