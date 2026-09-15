"""Fail-closed contracts for a future Salesforce read adapter, with no parser or client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .adapters import SourceReference, SyncScope
from .models import ReasonCode


class SourceReadFailure(StrEnum):
    AUTHENTICATION = "authentication"
    INVALID_JSON = "invalid_json"
    SCHEMA_DRIFT = "schema_drift"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    AMBIGUOUS_RESULT = "ambiguous_result"


@dataclass(frozen=True, slots=True)
class SourceReadResult:
    """Metadata-only result for one fixed source-read scope."""

    scope: SyncScope
    references: tuple[SourceReference, ...]
    failure: SourceReadFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SyncScope):
            raise ValueError("source_read_requires_fixed_scope")
        if not all(isinstance(reference, SourceReference) for reference in self.references):
            raise ValueError("source_read_requires_source_references")
        if self.failure is not None and not isinstance(self.failure, SourceReadFailure):
            raise ValueError("invalid_source_read_failure")
        if self.failure is not None and self.references:
            raise ValueError("failed_source_read_cannot_return_references")
        if len({reference.salesforce_record_id for reference in self.references}) != len(self.references):
            raise ValueError("source_read_duplicate_record_id")
        if len({reference.co_number for reference in self.references}) != len(self.references):
            raise ValueError("source_read_duplicate_co_number")
        if self.scope.kind in {"record_id", "co_number"} and len(self.references) > 1:
            raise ValueError("targeted_source_read_must_be_unambiguous")
        if self.references and self.scope.kind == "record_id":
            if self.references[0].salesforce_record_id != self.scope.identifier:
                raise ValueError("source_read_record_scope_mismatch")
        if self.references and self.scope.kind == "co_number":
            if self.references[0].co_number != self.scope.identifier:
                raise ValueError("source_read_co_scope_mismatch")


def source_read_blocker(result: SourceReadResult) -> ReasonCode | None:
    """Map a masked source failure to a safe workflow blocker without retrying."""
    if result.failure is None:
        return None
    if result.failure is SourceReadFailure.AUTHENTICATION:
        return ReasonCode.AUTHENTICATION_BLOCKED
    if result.failure in {SourceReadFailure.TIMEOUT, SourceReadFailure.TRANSPORT}:
        return ReasonCode.RETRYABLE_READ_FAILURE
    return ReasonCode.SOURCE_DATA_MISMATCH


def require_successful_source_read(result: SourceReadResult) -> tuple[SourceReference, ...]:
    """Return references only for an unambiguous successful local result."""
    blocker = source_read_blocker(result)
    if blocker is not None:
        raise ValueError(f"source_read_blocked:{blocker}")
    return result.references
