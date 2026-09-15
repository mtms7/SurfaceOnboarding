"""Pure pre-execution gate evaluation; it cannot invoke an adapter or worker."""

from __future__ import annotations

from dataclasses import dataclass

from .mappings import mapping_blocker
from .models import ReasonCode, WorkflowItem, WorkflowState
from .origin_policy import require_development_origin
from .readiness import ApprovalGates


@dataclass(frozen=True, slots=True)
class ExecutionGateDecision:
    allowed: bool
    reason_code: ReasonCode | None = None

    def __post_init__(self) -> None:
        if self.allowed != (self.reason_code is None):
            raise ValueError("execution_gate_requires_consistent_decision")


def evaluate_execution_gate(item: WorkflowItem, *, approvals: ApprovalGates,
                            origin: str) -> ExecutionGateDecision:
    """Evaluate static prerequisites only; no account operation can occur here."""
    if item.state is not WorkflowState.READY:
        return ExecutionGateDecision(False, ReasonCode.VERIFICATION_FAILED)
    mapping_reason = mapping_blocker(item.engine_value)
    if mapping_reason is not None:
        return ExecutionGateDecision(False, mapping_reason)
    try:
        require_development_origin(origin)
    except ValueError:
        return ExecutionGateDecision(False, ReasonCode.VERIFICATION_FAILED)
    if not approvals.leonardo_auth_approved:
        return ExecutionGateDecision(False, ReasonCode.AUTHENTICATION_BLOCKED)
    return ExecutionGateDecision(True)
