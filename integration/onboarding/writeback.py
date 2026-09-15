"""Fail-closed Salesforce writeback boundary; no client implementation exists."""

from __future__ import annotations

from dataclasses import dataclass
import re

_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class SalesforceWritebackBlockedError(PermissionError):
    """Raised before any unapproved writeback could be attempted."""


@dataclass(frozen=True, slots=True)
class WritebackReceiptRequest:
    """Correlation-only request; it deliberately excludes field values and UUIDs."""

    salesforce_record_id: str
    source_revision: str
    verification_hash: str

    def __post_init__(self) -> None:
        if not _RECORD_ID_PATTERN.fullmatch(self.salesforce_record_id):
            raise ValueError("invalid_writeback_record_id")
        if not self.source_revision or any(character.isspace() for character in self.source_revision):
            raise ValueError("invalid_writeback_source_revision")
        if not _SHA256_PATTERN.fullmatch(self.verification_hash):
            raise ValueError("invalid_writeback_verification_hash")


class DisabledSalesforceWriteback:
    """Permanent local stub until field mapping and write authority are approved."""

    def request(self, request: WritebackReceiptRequest) -> None:
        if not isinstance(request, WritebackReceiptRequest):
            raise ValueError("writeback_requires_receipt_request")
        raise SalesforceWritebackBlockedError("salesforce_writeback_not_approved")
