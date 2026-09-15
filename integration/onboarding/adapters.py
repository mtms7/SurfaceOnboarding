"""Safe interfaces and test doubles only; no external transport is implemented."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_CO_NUMBER_PATTERN = re.compile(r"^CO-[0-9]+$")


@dataclass(frozen=True, slots=True)
class SyncScope:
    kind: str
    identifier: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"all", "record_id", "co_number"}:
            raise ValueError("unsupported_sync_scope")
        if (self.kind == "all") != (self.identifier is None):
            raise ValueError("invalid_sync_scope_identifier")
        if self.kind == "record_id" and not _RECORD_ID_PATTERN.fullmatch(self.identifier or ""):
            raise ValueError("invalid_salesforce_record_id_scope")
        if self.kind == "co_number" and not _CO_NUMBER_PATTERN.fullmatch(self.identifier or ""):
            raise ValueError("invalid_co_number_scope")


@dataclass(frozen=True, slots=True)
class SyncResult:
    accepted: bool
    job_id: str
    failure_category: str | None = None


@dataclass(frozen=True, slots=True)
class SourceReference:
    """A minimal synthetic source pointer; it intentionally carries no source fields."""

    salesforce_record_id: str
    co_number: str
    source_revision: str

    def __post_init__(self) -> None:
        if not _RECORD_ID_PATTERN.fullmatch(self.salesforce_record_id):
            raise ValueError("invalid_source_reference_record_id")
        if not _CO_NUMBER_PATTERN.fullmatch(self.co_number):
            raise ValueError("invalid_source_reference_co_number")
        if not self.source_revision or any(character.isspace() for character in self.source_revision):
            raise ValueError("invalid_source_reference_revision")


class SalesforceReadAdapter(Protocol):
    def request_sync(self, scope: SyncScope, *, requested_by: str) -> SyncResult: ...


class LeonardoReadAdapter(Protocol):
    def health(self) -> str: ...


class MockSalesforceAdapter:
    """Deterministic local double; it cannot invoke `sf` or access a network."""

    def request_sync(self, scope: SyncScope, *, requested_by: str) -> SyncResult:
        if not requested_by:
            raise ValueError("requested_by_required")
        key = scope.identifier or "all"
        return SyncResult(True, f"mock-sync-{scope.kind}-{key}")


class SyntheticSalesforceCatalog:
    """In-memory source-reference double; it cannot query Salesforce or run SOQL."""

    def __init__(self, references: tuple[SourceReference, ...]) -> None:
        if len({reference.salesforce_record_id for reference in references}) != len(references):
            raise ValueError("duplicate_synthetic_record_id")
        if len({reference.co_number for reference in references}) != len(references):
            raise ValueError("duplicate_synthetic_co_number")
        self._references = references

    def read_references(self, scope: SyncScope) -> tuple[SourceReference, ...]:
        """Return only preloaded synthetic references selected by the fixed scope."""
        if scope.kind == "all":
            return self._references
        if scope.kind == "record_id":
            return tuple(reference for reference in self._references
                         if reference.salesforce_record_id == scope.identifier)
        return tuple(reference for reference in self._references if reference.co_number == scope.identifier)


class DisabledLeonardoAdapter:
    def health(self) -> str:
        return "authentication_blocked"
