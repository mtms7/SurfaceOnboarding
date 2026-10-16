"""Guarded Account UUID write-back plan for CE-only New onboardings (pure; owner decision D3, 2026-10-16).

After a verified BackOffice create, the Account UUID from the readback may be written to ONE field
(Customer_Onboarding__c.Account_UUID__c) on ONE CO: no stage change, no other fields. Surface-only and
renewal routes are excluded. Nothing here performs I/O, runs a process, or touches the network: the
caller runs the returned argument list later, then checks the re-read row with ``verify_after_write``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

WRITE_FIELD = "Account_UUID__c"
SOBJECT = "Customer_Onboarding__c"
ENVIRONMENTS = ("production", "development")
# Same constants as tools/attended_ce_only_playwright.py (CE_ENGINE, CE_ROUTE_PRODUCT, CE_ROUTE_TYPE).
CE_ONLY_ROUTE = "case_2_new_ce_only"
CE_ROUTE_PRODUCT = "Credential Exposure"
CE_ROUTE_TYPE = "New Product Onboarding"

_UUID = re.compile(r"[0-9a-f]{32}")
_SF_ID = re.compile(r"(?:[A-Za-z0-9]{15}|[A-Za-z0-9]{18})")
_ORG = re.compile(r"[A-Za-z0-9_.@][A-Za-z0-9_.@-]*")


@dataclass(frozen=True)
class WritebackPlan:
    status: str  # ready_to_write | already_present | blocked | conflict
    reason: str
    uuid: str | None = None
    field: str = WRITE_FIELD


def normalise_uuid(value: Any) -> str | None:
    """Trim and lower-case; return the 32-hex UUID or None."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned if _UUID.fullmatch(cleaned) else None


def _blocked(reason: str) -> WritebackPlan:
    return WritebackPlan("blocked", reason)


def plan(environment: Any, route: Any, co_row: Any, readback_uuid: Any, readback_verified: Any,
         source_revision_now: Any, source_revision_at_approval: Any, explicit_go: Any) -> WritebackPlan:
    if not isinstance(environment, str) or environment not in ENVIRONMENTS:
        return _blocked("environment_not_named")
    if not isinstance(co_row, Mapping):
        return _blocked("co_row_unreadable")
    if (route != CE_ONLY_ROUTE or co_row.get("Onboarding_Product__c") != CE_ROUTE_PRODUCT
            or co_row.get("Onboarding_Type__c") != CE_ROUTE_TYPE):
        return _blocked("route_not_ce_only_new")
    if readback_verified is not True:
        return _blocked("readback_not_verified")
    uuid = normalise_uuid(readback_uuid)
    if uuid is None:
        return _blocked("uuid_invalid")
    if (not isinstance(source_revision_now, str) or not source_revision_now
            or source_revision_now != source_revision_at_approval):
        return _blocked("revision_changed")
    if explicit_go is not True:
        return _blocked("explicit_go_missing")
    if WRITE_FIELD not in co_row:  # the field was not read: never treat "not read" as "empty"
        return _blocked("co_row_unreadable")
    current = co_row[WRITE_FIELD]
    if current is not None and not isinstance(current, str):
        return _blocked("co_row_unreadable")
    current = (current or "").strip().lower()
    if not current:
        return WritebackPlan("ready_to_write", "ready", uuid)
    if current == uuid:
        return WritebackPlan("already_present", "equal", uuid)
    return WritebackPlan("conflict", "different_value", uuid)


def update_argv(write_plan: Any, co_salesforce_id: Any, target_org: Any) -> list[str]:
    """Fixed argument list (never a shell string) writing only Account_UUID__c on one CO.

    Only a ``WritebackPlan`` with status ``ready_to_write`` is accepted; the UUID comes from the plan itself.
    """
    if (not isinstance(write_plan, WritebackPlan) or write_plan.status != "ready_to_write"
            or write_plan.field != WRITE_FIELD):
        raise ValueError("write-back plan is not ready_to_write")
    if not isinstance(co_salesforce_id, str) or not _SF_ID.fullmatch(co_salesforce_id):
        raise ValueError("invalid Salesforce record id")
    normalised = normalise_uuid(write_plan.uuid)
    if normalised is None:
        raise ValueError("invalid Account UUID")
    if not isinstance(target_org, str) or not _ORG.fullmatch(target_org):
        raise ValueError("invalid target org alias")
    return ["sf", "data", "update", "record", "--sobject", SOBJECT, "--record-id", co_salesforce_id,
            "--values", f"{WRITE_FIELD}={normalised}", "--target-org", target_org]


def verify_after_write(row_after: Any, expected_uuid: Any) -> str:
    """Return "verified", "mismatch", or "unreadable" for the CO row re-read after the write."""
    expected = normalise_uuid(expected_uuid)
    if expected is None or not isinstance(row_after, Mapping) or WRITE_FIELD not in row_after:
        return "unreadable"
    value = row_after[WRITE_FIELD]
    if value is not None and not isinstance(value, str):
        return "unreadable"
    return "verified" if (value or "").strip().lower() == expected else "mismatch"
