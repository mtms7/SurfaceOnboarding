"""End-to-end local intake using synthetic, metadata-only source references."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .adapters import SourceReference
from .intake_decision import decide_intake
from .models import EngineValue, WorkflowItem, WorkflowState
from .repository import InMemoryWorkflowRepository
from .scope_policy import ScopeCounts
from .source_read import SourceReadResult, require_successful_source_read
from .state_machine import transition


@dataclass(frozen=True, slots=True)
class SyntheticIntakeCandidate:
    """Synthetic-only metadata required to exercise the guarded intake flow."""

    source: SourceReference
    engine_value: EngineValue
    policy_version: str
    intent_hash: str
    idempotency_key: str
    counts: ScopeCounts
    observed_at: datetime
    policy_flag: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.engine_value, EngineValue):
            raise ValueError("synthetic_intake_requires_engine_value")
        if not all((self.policy_version, self.intent_hash, self.idempotency_key)):
            raise ValueError("synthetic_intake_requires_metadata")
        if any(any(character.isspace() for character in value)
               for value in (self.policy_version, self.intent_hash, self.idempotency_key)):
            raise ValueError("synthetic_intake_metadata_cannot_contain_whitespace")
        if not isinstance(self.counts, ScopeCounts):
            raise ValueError("synthetic_intake_requires_scope_counts")
        if self.observed_at.tzinfo is None:
            raise ValueError("synthetic_intake_timestamp_must_be_timezone_aware")


@dataclass(frozen=True, slots=True)
class SyntheticIntakeResult:
    item: WorkflowItem
    created: bool
    changed: bool


def ingest_synthetic(candidate: SyntheticIntakeCandidate,
                     repository: InMemoryWorkflowRepository) -> SyntheticIntakeResult:
    """Persist a synthetic reference then apply only local validation gates.

    No source read, external call, execution, or writeback is possible here.
    """
    if not isinstance(repository, InMemoryWorkflowRepository):
        raise ValueError("synthetic_intake_requires_local_repository")
    discovered = WorkflowItem(
        candidate.source.salesforce_record_id,
        candidate.source.co_number,
        candidate.source.source_revision,
        candidate.engine_value,
        candidate.policy_version,
        candidate.intent_hash,
        candidate.idempotency_key,
        WorkflowState.DISCOVERED,
        None,
        candidate.observed_at,
    )
    existing = repository.get(candidate.source.salesforce_record_id)
    if existing is not None and existing.source_revision == candidate.source.source_revision:
        immutable_metadata_matches = (
            existing.co_number == candidate.source.co_number
            and existing.engine_value is candidate.engine_value
            and existing.policy_version == candidate.policy_version
            and existing.intent_hash == candidate.intent_hash
            and existing.idempotency_key == candidate.idempotency_key
        )
        if immutable_metadata_matches:
            return SyntheticIntakeResult(existing, created=False, changed=False)
        raise ValueError("same_source_revision_metadata_conflict")
    upsert = repository.upsert(
        discovered,
        expected_source_revision=existing.source_revision if existing is not None else None,
    )
    if not upsert.created and not upsert.changed:
        return SyntheticIntakeResult(upsert.item, created=False, changed=False)

    validating = transition(discovered, WorkflowState.VALIDATING,
                            source_revision=discovered.source_revision)
    repository.save(validating, expected_source_revision=discovered.source_revision,
                    expected_state=WorkflowState.DISCOVERED)
    decision = decide_intake(candidate.engine_value, candidate.counts, policy_flag=candidate.policy_flag)
    finalized = transition(validating, decision.state, source_revision=validating.source_revision,
                           reason_code=decision.reason_code)
    repository.save(finalized, expected_source_revision=finalized.source_revision,
                    expected_state=WorkflowState.VALIDATING)
    return SyntheticIntakeResult(finalized, created=upsert.created, changed=upsert.changed)


def validate_synthetic_batch(result: SourceReadResult,
                             candidates: tuple[SyntheticIntakeCandidate, ...]) -> None:
    """Require an exact, successful synthetic source snapshot before intake."""
    references = require_successful_source_read(result)
    candidate_references = tuple(candidate.source for candidate in candidates)
    if len(candidate_references) != len(set(candidate_references)):
        raise ValueError("synthetic_batch_duplicate_candidate_reference")
    if set(candidate_references) != set(references):
        raise ValueError("synthetic_batch_source_snapshot_mismatch")


def ingest_synthetic_batch(result: SourceReadResult,
                           candidates: tuple[SyntheticIntakeCandidate, ...],
                           repository: InMemoryWorkflowRepository) -> tuple[SyntheticIntakeResult, ...]:
    """Ingest only an exact, successful synthetic source snapshot.

    A failed, incomplete, or mismatched read cannot create any workflow item.
    """
    validate_synthetic_batch(result, candidates)
    return tuple(ingest_synthetic(candidate, repository) for candidate in candidates)
