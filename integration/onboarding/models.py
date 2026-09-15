"""Contracts that intentionally exclude customer and authentication data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class EngineValue(StrEnum):
    CASE_1 = "case_1_new_surface_only"
    CASE_2 = "case_2_new_ce_only"
    CASE_3 = "case_3_combined_baseline"
    CASE_4 = "case_4_renew_surface_new_ce"
    CASE_5 = "case_5_renew_ce_new_surface"
    CASE_6 = "case_6_renew_both"


class WorkflowState(StrEnum):
    DISCOVERED = "discovered"
    VALIDATING = "validating"
    PENDING = "pending"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    READY = "ready"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SCANNING = "scanning"
    FINISHED = "finished"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    UNCERTAIN_EXTERNAL_RESULT = "uncertain_external_result"


class ReasonCode(StrEnum):
    SOURCE_DATA_MISMATCH = "source_data_mismatch"
    CASE_NOT_MAPPED_YET = "case_not_mapped_yet"
    MAPPING_NOT_APPROVED = "mapping_not_approved"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    AUTHENTICATION_BLOCKED = "authentication_blocked"
    DUPLICATE_OR_COLLISION = "duplicate_or_collision"
    VERIFICATION_FAILED = "verification_failed"
    RETRYABLE_READ_FAILURE = "retryable_read_failure"
    UNCERTAIN_EXTERNAL_RESULT = "uncertain_external_result"


@dataclass(frozen=True, slots=True)
class WorkflowItem:
    """Minimal local persistence contract; do not add source or auth data."""

    salesforce_record_id: str
    co_number: str
    source_revision: str
    engine_value: EngineValue
    policy_version: str
    intent_hash: str
    idempotency_key: str
    state: WorkflowState
    reason_code: ReasonCode | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if not all(
            (self.salesforce_record_id, self.co_number, self.source_revision,
             self.policy_version, self.intent_hash, self.idempotency_key)
        ):
            raise ValueError("workflow_item_missing_required_identifier")
        if not _SHA256_PATTERN.fullmatch(self.intent_hash):
            raise ValueError("workflow_item_invalid_intent_hash")
        if not _SHA256_PATTERN.fullmatch(self.idempotency_key):
            raise ValueError("workflow_item_invalid_idempotency_key")
        if self.state is WorkflowState.UNCERTAIN_EXTERNAL_RESULT and self.reason_code is not ReasonCode.UNCERTAIN_EXTERNAL_RESULT:
            raise ValueError("uncertain_state_requires_uncertain_reason")
