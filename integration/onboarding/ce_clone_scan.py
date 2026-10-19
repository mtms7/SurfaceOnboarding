"""The Credential Exposure (CE) scan state of one CO, read from the production clone row (query 253, docs/56).

Pure functions, no network. A scan counts only when it belongs to this onboarding: finished means the newest
LEAKED_CREDENTIALS_DISCOVERY execution is DONE / DONE_WITH_ERRORS *and* ended after the create time recorded for the CO,
so an older execution of a re-used tenant never counts. Anything unexpected is ``UNKNOWN`` (fail closed).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

FINISHED, FINISHED_WITH_ERRORS, RUNNING, NOT_YET, UNKNOWN = (
    "finished", "finished_with_errors", "running", "not_yet", "unknown")
_UUID = re.compile(r"[0-9a-f]{32}")


def _ms(value: Any) -> int | None:
    return value if type(value) is int else None


def ce_scan_state(v2: Mapping[str, Any] | None, created_ms: Any) -> str:
    """State of this CO's CE scan from the clone's ``v2`` values (``ce_status/ce_start/ce_end/ce_count``)."""
    created = _ms(created_ms)
    if created is None or not isinstance(v2, Mapping):
        return UNKNOWN
    status, start, end = v2.get("ce_status"), _ms(v2.get("ce_start")), _ms(v2.get("ce_end"))
    if status is None:
        return NOT_YET  # no execution yet
    if status in ("DONE", "DONE_WITH_ERRORS"):
        if end is None or end <= created:
            return NOT_YET  # unfinished record, or an execution from before this onboarding
        return FINISHED if status == "DONE" else FINISHED_WITH_ERRORS
    if status == "PENDING":
        return RUNNING if start is not None and start > created else NOT_YET
    return UNKNOWN


def is_finished(state: str) -> bool:
    return state in (FINISHED, FINISHED_WITH_ERRORS)


def clone_v2_for_uuid(payload: Any, account_uuid: Any) -> Mapping[str, Any] | None:
    """The v2 values of the one clone tenant with this Account UUID; None if absent, ambiguous, or not a v2 clone."""
    if type(account_uuid) is not str or not _UUID.fullmatch(account_uuid.lower()) or not isinstance(payload, Mapping):
        return None
    if payload.get("row_schema") != "v2" or type(payload.get("tenants")) is not list:
        return None
    matches = [tenant for tenant in payload["tenants"]
               if isinstance(tenant, Mapping) and str(tenant.get("account_uuid", "")).lower() == account_uuid.lower()]
    if len(matches) != 1 or not isinstance(matches[0].get("v2"), Mapping):
        return None
    return matches[0]["v2"]
