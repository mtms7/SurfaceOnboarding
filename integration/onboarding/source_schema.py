"""Strict in-memory parser for metadata-only synthetic source-reference documents."""

from __future__ import annotations

import json

from .adapters import SourceReference, SyncScope
from .source_read import SourceReadFailure, SourceReadResult

_REFERENCE_FIELDS = frozenset({"salesforce_record_id", "co_number", "source_revision"})


def parse_reference_document(scope: SyncScope, document: str) -> SourceReadResult:
    """Parse one synthetic JSON list and immediately reduce it to safe references.

    Input text is never attached to the result, written to disk, or logged.
    """
    if not isinstance(document, str):
        return SourceReadResult(scope, (), SourceReadFailure.INVALID_JSON)
    try:
        records = json.loads(document)
    except json.JSONDecodeError:
        return SourceReadResult(scope, (), SourceReadFailure.INVALID_JSON)
    if not isinstance(records, list):
        return SourceReadResult(scope, (), SourceReadFailure.SCHEMA_DRIFT)
    references: list[SourceReference] = []
    try:
        for record in records:
            if not isinstance(record, dict) or set(record) != _REFERENCE_FIELDS:
                raise ValueError("unexpected_reference_schema")
            references.append(SourceReference(
                record["salesforce_record_id"], record["co_number"], record["source_revision"],
            ))
        return SourceReadResult(scope, tuple(references))
    except (TypeError, ValueError):
        return SourceReadResult(scope, (), SourceReadFailure.SCHEMA_DRIFT)
