"""Guarded Onboarding Stage moves for CE-only New onboardings (pure; owner decision 2026-10-19, after the CO-0798 create).

CE-only never creates a customer user, but it still passes THROUGH "User Created" (owner decision 2026-10-28, supersedes
"CE-only skips User Created"). The only moves allowed are:

    Request Approved            -> Account Scanning              (right after a verified create and a verified UUID write)
    Account Scanning            -> Scan Completed Successfully   (after the CE scan is confirmed finished)
    Scan Completed Successfully -> User Created                  (immediately after; no user is created for CE-only)
    User Created                -> Onboarding Completed          (immediately after the previous move)

Each move writes ONE field (``Onboarding_Stage__c``) on ONE CO, only from the exact expected stage and only on the
CE-only New route. Nothing here performs I/O or runs a process: the caller runs the returned argument list, then checks
the re-read row with ``verify_after_write``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import salesforce_uuid_writeback as uuid_wb

STAGE_FIELD = "Onboarding_Stage__c"
SOBJECT = uuid_wb.SOBJECT
CE_ONLY_FLOW = (("Request Approved", "Account Scanning"),
                ("Account Scanning", "Scan Completed Successfully"),
                ("Scan Completed Successfully", "User Created"),
                ("User Created", "Onboarding Completed"))
_FROM = {to: frm for frm, to in CE_ONLY_FLOW}
_SF_ID = re.compile(r"(?:[A-Za-z0-9]{15}|[A-Za-z0-9]{18})")
_ORG = re.compile(r"[A-Za-z0-9_.@][A-Za-z0-9_.@-]*")


@dataclass(frozen=True)
class StagePlan:
    status: str  # ready_to_write | already_present | blocked
    reason: str
    from_stage: str
    to_stage: str


def _blocked(reason: str, to_stage: str = "") -> StagePlan:
    return StagePlan("blocked", reason, "", to_stage)


def plan(co_row: Any, to_stage: Any) -> StagePlan:
    """The move of one CE-only CO to ``to_stage``: ready only from the exact preceding stage of ``CE_ONLY_FLOW``."""
    if not isinstance(to_stage, str) or to_stage not in _FROM:
        return _blocked("transition_not_allowed")
    if not isinstance(co_row, Mapping) or STAGE_FIELD not in co_row or not isinstance(co_row.get("Id"), str):
        return _blocked("row_unreadable", to_stage)
    if (co_row.get("Onboarding_Product__c") != uuid_wb.CE_ROUTE_PRODUCT
            or co_row.get("Onboarding_Type__c") != uuid_wb.CE_ROUTE_TYPE):
        return _blocked("route_not_ce_only_new", to_stage)
    current = co_row.get(STAGE_FIELD)
    if current == to_stage:
        return StagePlan("already_present", "already_present", to_stage, to_stage)
    if current != _FROM[to_stage]:
        return _blocked("stage_not_expected", to_stage)
    return StagePlan("ready_to_write", "", current, to_stage)


def update_argv(stage_plan: Any, co_salesforce_id: Any, target_org: Any) -> list[str]:
    """Fixed argument list (never a shell string) writing only Onboarding_Stage__c on one CO."""
    if not isinstance(stage_plan, StagePlan) or stage_plan.status != "ready_to_write" or stage_plan.to_stage not in _FROM:
        raise ValueError("stage plan is not ready_to_write")
    if not isinstance(co_salesforce_id, str) or not _SF_ID.fullmatch(co_salesforce_id):
        raise ValueError("invalid Salesforce record id")
    if not isinstance(target_org, str) or not _ORG.fullmatch(target_org):
        raise ValueError("invalid target org alias")
    return ["sf", "data", "update", "record", "--sobject", SOBJECT, "--record-id", co_salesforce_id,
            "--values", f"{STAGE_FIELD}='{stage_plan.to_stage}'", "--target-org", target_org]


def verify_after_write(row_after: Any, expected_stage: Any) -> str:
    """Return "verified", "mismatch", or "unreadable" for the CO row re-read after the write."""
    if not isinstance(row_after, Mapping) or STAGE_FIELD not in row_after:
        return "unreadable"
    return "verified" if row_after.get(STAGE_FIELD) == expected_stage else "mismatch"
