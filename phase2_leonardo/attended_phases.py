"""Pure-local gate model for an attended Leonardo Development workflow.

This module deliberately accepts only policy constants, booleans, counts, and
attestation states.  It has no fields for customer data or authentication
material and performs no network, browser, filesystem-write, create, confirm,
readback, or writeback operation.

The returned plan is advisory.  Every phase is marked ``local_callable=False``;
external adapters and the human confirmation remain separate security
boundaries.  The current Case 3 v5 preflight therefore produces a blocked plan
because its contract intentionally returns ``proceed=false``,
``leonardo_request_allowed=false``, and six unapproved field groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


PREFLIGHT_CONTRACT_VERSION: Final = "surface-case3-preflight-v2"
V5_MAPPING_POLICY_VERSION: Final = (
    "surface-case3-partial-owner-mapping-2026-08-31-v5"
)

PHASE_NAMES: Final = (
    "mapping_source_reread",
    "duplicate_preflight",
    "fill_and_pause",
    "human_confirm",
    "readback",
    "writeback",
)


@dataclass(frozen=True, slots=True)
class AttendedPhaseEvidence:
    """Redacted gate evidence; values must not contain customer information."""

    preflight_contract_version: str = PREFLIGHT_CONTRACT_VERSION
    mapping_policy_version: str = V5_MAPPING_POLICY_VERSION
    preflight_proceed: bool = False
    leonardo_request_allowed: bool = False
    unapproved_field_group_count: int = 6
    source_reread_matches: bool = False
    primary_user_binding_verified: bool = False
    separated_approval_verified: bool = False
    duplicate_authority_verified: bool = False
    duplicate_count: int | None = None
    human_confirmation_attested: bool = False
    readback_authority_verified: bool = False
    readback_verified: bool = False
    writeback_approval_verified: bool = False


@dataclass(frozen=True, slots=True)
class PhaseGate:
    """One phase decision.  A local gate is never an action capability."""

    name: str
    status: str
    local_callable: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttendedPhasePlan:
    """Ordered, redacted phase plan with external actions always disabled."""

    proceed: bool
    external_action_callable: bool
    phases: tuple[PhaseGate, ...]
    blockers: tuple[str, ...]


def _is_true(value: object) -> bool:
    """Reject truthy non-booleans such as 1 or a nonempty string."""

    return type(value) is bool and value is True


def _valid_nonnegative_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _gate(name: str, status: str, reasons: list[str]) -> PhaseGate:
    return PhaseGate(
        name=name,
        status=status,
        local_callable=False,
        reasons=tuple(sorted(set(reasons))),
    )


def evaluate_attended_phases(evidence: AttendedPhaseEvidence) -> AttendedPhasePlan:
    """Evaluate guarded phases without making any external action callable.

    A syntactically complete plan can at most identify readiness for a future,
    separately authorized adapter.  Human confirmation is always an external
    human-only boundary.  ``proceed`` and ``external_action_callable`` are
    intentionally constant ``False``.
    """

    if not isinstance(evidence, AttendedPhaseEvidence):
        invalid = _gate(
            "mapping_source_reread",
            "blocked",
            ["evidence:invalid_type"],
        )
        unevaluated = tuple(
            _gate(name, "not_evaluated", ["prior_phase:blocked"])
            for name in PHASE_NAMES[1:]
        )
        return AttendedPhasePlan(
            proceed=False,
            external_action_callable=False,
            phases=(invalid, *unevaluated),
            blockers=("evidence:invalid_type",),
        )

    mapping_reasons: list[str] = []
    if evidence.preflight_contract_version != PREFLIGHT_CONTRACT_VERSION:
        mapping_reasons.append("preflight:unsupported_contract")
    if evidence.mapping_policy_version != V5_MAPPING_POLICY_VERSION:
        mapping_reasons.append("mapping:unexpected_policy")
    else:
        # v5 is a partial, validate-only mapping. Its response schema fixes
        # these values at false/false/six; caller-supplied variation cannot
        # convert that partial policy into execution readiness.
        mapping_reasons.append("mapping:v5_execution_unapproved")
        if (
            evidence.preflight_proceed is not False
            or evidence.leonardo_request_allowed is not False
            or type(evidence.unapproved_field_group_count) is not int
            or evidence.unapproved_field_group_count != 6
        ):
            mapping_reasons.append("preflight:v5_invariant_violation")
    if not _is_true(evidence.preflight_proceed):
        mapping_reasons.append("preflight:proceed_false")
    if not _is_true(evidence.leonardo_request_allowed):
        mapping_reasons.append("preflight:leonardo_request_not_allowed")
    if not _valid_nonnegative_count(evidence.unapproved_field_group_count):
        mapping_reasons.append("mapping:unapproved_group_count_invalid")
    elif evidence.unapproved_field_group_count != 0:
        mapping_reasons.append("mapping:unapproved_field_groups_present")
    if not _is_true(evidence.source_reread_matches):
        mapping_reasons.append("source_reread:mismatch_or_missing")
    if not _is_true(evidence.primary_user_binding_verified):
        mapping_reasons.append("primary_user:binding_not_verified")

    mapping_complete = not mapping_reasons
    mapping_gate = _gate(
        "mapping_source_reread",
        "evidence_complete" if mapping_complete else "blocked",
        mapping_reasons,
    )

    duplicate_reasons: list[str] = []
    if not mapping_complete:
        duplicate_reasons.append("prior_phase:blocked")
    if not _is_true(evidence.duplicate_authority_verified):
        duplicate_reasons.append("duplicate_preflight:authority_not_verified")
    if type(evidence.duplicate_count) is not int or evidence.duplicate_count != 0:
        duplicate_reasons.append("duplicate_preflight:zero_matches_required")
    duplicate_complete = not duplicate_reasons
    duplicate_gate = _gate(
        "duplicate_preflight",
        "evidence_complete" if duplicate_complete else "blocked",
        duplicate_reasons,
    )

    fill_reasons: list[str] = []
    if not duplicate_complete:
        fill_reasons.append("prior_phase:blocked")
    if not _is_true(evidence.separated_approval_verified):
        fill_reasons.append("approval:separated_approval_not_verified")
    fill_ready = not fill_reasons
    fill_gate = _gate(
        "fill_and_pause",
        "ready_for_separately_authorized_adapter" if fill_ready else "blocked",
        fill_reasons,
    )

    human_reasons: list[str] = []
    if not fill_ready:
        human_reasons.append("prior_phase:blocked")
    if not _is_true(evidence.human_confirmation_attested):
        human_reasons.append("human_confirm:external_attestation_required")
    human_complete = not human_reasons
    human_gate = _gate(
        "human_confirm",
        "completed_by_external_human" if human_complete else "external_human_only",
        human_reasons,
    )

    readback_reasons: list[str] = []
    if not human_complete:
        readback_reasons.append("prior_phase:human_confirmation_required")
    if not _is_true(evidence.readback_authority_verified):
        readback_reasons.append("readback:authority_not_verified")
    if not _is_true(evidence.readback_verified):
        readback_reasons.append("readback:exact_match_not_verified")
    readback_complete = not readback_reasons
    readback_gate = _gate(
        "readback",
        "evidence_complete" if readback_complete else "blocked",
        readback_reasons,
    )

    writeback_reasons: list[str] = []
    if not readback_complete:
        writeback_reasons.append("prior_phase:readback_not_verified")
    if not _is_true(evidence.writeback_approval_verified):
        writeback_reasons.append("writeback:separate_approval_not_verified")
    writeback_gate = _gate(
        "writeback",
        (
            "ready_for_separately_authorized_adapter"
            if not writeback_reasons
            else "blocked"
        ),
        writeback_reasons,
    )

    phases = (
        mapping_gate,
        duplicate_gate,
        fill_gate,
        human_gate,
        readback_gate,
        writeback_gate,
    )
    blockers = tuple(
        sorted({reason for phase in phases for reason in phase.reasons})
    )
    return AttendedPhasePlan(
        proceed=False,
        external_action_callable=False,
        phases=phases,
        blockers=blockers,
    )
