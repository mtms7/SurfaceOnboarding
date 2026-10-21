"""Guarded Case 3 ID write-back PLAN (owner request 2026-10-21; pure, like ``salesforce_uuid_writeback`` for CE-only).

After a Case 3 tenant is verified live (``verified_tenant.latest_verified``), the Account ID and the Account UUID may be written to ONE
Customer_Onboarding__c record: two fields, only the ones that are empty, no stage change, no other field. A different existing value
is a conflict and is never overwritten. Nothing here performs I/O, runs a process or touches the network: the caller runs the returned
argument list later (only after a separate explicit go) and then checks the re-read row with ``verify_after_write``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ID_FIELD = "Surface_Account_ID__c"
UUID_FIELD = "Account_UUID__c"
SOBJECT = "Customer_Onboarding__c"
ENVIRONMENTS = ("production", "development")
CASE3_ROUTE = "case_3_combined_baseline"
CASE3_PRODUCT = "Surface & Credential Exposure"
CASE3_TYPE = "New Product Onboarding"

_UUID = re.compile(r"[0-9a-f]{32}")
_ID = re.compile(r"[0-9a-f]{24}")
_SF_ID = re.compile(r"(?:[A-Za-z0-9]{15}|[A-Za-z0-9]{18})")
_ORG = re.compile(r"[A-Za-z0-9_.@][A-Za-z0-9_.@-]*")


@dataclass(frozen=True)
class WritebackPlan:
    status: str  # ready_to_write | already_present | blocked | conflict
    reason: str
    values: Mapping[str, str] = field(default_factory=dict)  # field -> value, only the fields that are empty today


def _blocked(reason: str) -> WritebackPlan:
    return WritebackPlan("blocked", reason)


def _norm(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned if pattern.fullmatch(cleaned) else None


def plan(environment: Any, route: Any, co_row: Any, verified: Any, source_revision_now: Any, source_revision_at_approval: Any,
         explicit_go: Any) -> WritebackPlan:
    if not isinstance(environment, str) or environment not in ENVIRONMENTS:
        return _blocked("environment_not_named")
    if not isinstance(co_row, Mapping):
        return _blocked("co_row_unreadable")
    if (route != CASE3_ROUTE or co_row.get("Onboarding_Product__c") != CASE3_PRODUCT
            or co_row.get("Onboarding_Type__c") != CASE3_TYPE):
        return _blocked("route_not_case3_new")
    if not isinstance(verified, Mapping):
        return _blocked("tenant_not_verified")
    account_id, uuid = _norm(verified.get("account_id"), _ID), _norm(verified.get("account_uuid"), _UUID)
    if account_id is None or uuid is None:
        return _blocked("tenant_ids_invalid")
    if (not isinstance(source_revision_now, str) or not source_revision_now
            or source_revision_now != source_revision_at_approval):
        return _blocked("revision_changed")
    if explicit_go is not True:
        return _blocked("explicit_go_missing")
    wanted = {ID_FIELD: (account_id, _ID), UUID_FIELD: (uuid, _UUID)}
    write: dict[str, str] = {}
    conflict = False
    for name, (value, _pattern) in wanted.items():
        if name not in co_row:  # the field was not read: never treat "not read" as "empty"
            return _blocked("co_row_unreadable")
        current = co_row[name]
        if current is not None and not isinstance(current, str):
            return _blocked("co_row_unreadable")
        current = (current or "").strip().lower()
        if not current:
            write[name] = value
        elif current != value:
            conflict = True
    if conflict:
        return WritebackPlan("conflict", "different_value")
    if not write:
        return WritebackPlan("already_present", "equal")
    return WritebackPlan("ready_to_write", "ready", write)


def update_argv(write_plan: Any, co_salesforce_id: Any, target_org: Any) -> list[str]:
    """Fixed argument list (never a shell string) writing only the planned fields on one CO. Only ``ready_to_write`` is accepted."""
    if not isinstance(write_plan, WritebackPlan) or write_plan.status != "ready_to_write" or not write_plan.values:
        raise ValueError("write-back plan is not ready_to_write")
    if not isinstance(co_salesforce_id, str) or not _SF_ID.fullmatch(co_salesforce_id):
        raise ValueError("invalid Salesforce record id")
    if not isinstance(target_org, str) or not _ORG.fullmatch(target_org):
        raise ValueError("invalid target org alias")
    pairs = []
    for name, value in write_plan.values.items():
        pattern = _ID if name == ID_FIELD else _UUID if name == UUID_FIELD else None
        if pattern is None or _norm(value, pattern) is None:
            raise ValueError("invalid planned value")
        pairs.append(f"{name}={value}")
    return ["sf", "data", "update", "record", "--sobject", SOBJECT, "--record-id", co_salesforce_id,
            "--values", " ".join(pairs), "--target-org", target_org]


def verify_after_write(row_after: Any, verified: Any) -> str:
    """"verified", "mismatch" or "unreadable" for the CO row re-read after the write (both fields must equal the tenant's)."""
    if not isinstance(verified, Mapping) or not isinstance(row_after, Mapping):
        return "unreadable"
    expected = {ID_FIELD: _norm(verified.get("account_id"), _ID), UUID_FIELD: _norm(verified.get("account_uuid"), _UUID)}
    if None in expected.values():
        return "unreadable"
    for name, want in expected.items():
        if name not in row_after:
            return "unreadable"
        value = row_after[name]
        if value is not None and not isinstance(value, str):
            return "unreadable"
    return "verified" if all((row_after[name] or "").strip().lower() == want for name, want in expected.items()) else "mismatch"
