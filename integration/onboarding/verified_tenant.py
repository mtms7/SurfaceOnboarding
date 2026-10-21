"""A tenant verified live by the read-only Case 3 readback counts as onboarded before the clone catches up (owner request 2026-10-21).

The Case 3 readback leaves one ``readback_verified`` record: route, tenant name, Account ID, Account UUID. The production match of a CO
whose Account UUID is set but not yet in the Redash clone (``uuid_not_in_clone``) becomes ``onboarded`` only when such a record carries
exactly the CO's own Account UUID. Pure: no I/O, no network, no Salesforce, no browser. Anything else is left untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

CASE3_ROUTE = "case_3_combined_baseline"
OUTCOME = "readback_verified"
_UUID = re.compile(r"[0-9a-f]{32}")
_ID = re.compile(r"[0-9a-f]{24}")
_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}Z")


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned if _UUID.fullmatch(cleaned) else None


def latest_verified(records: Any, co_reference: str, salesforce_uuid: Any) -> dict[str, Any] | None:
    """The newest valid Case 3 ``readback_verified`` record of this CO whose UUID equals the CO's own, else None. Never raises."""
    wanted = _uuid(salesforce_uuid)
    if wanted is None or not isinstance(records, (list, tuple)):
        return None
    found = []
    for item in records:
        if not isinstance(item, Mapping):
            continue
        stamp, name = item.get("recorded_at"), item.get("tenant_name")
        if (item.get("outcome") == OUTCOME and item.get("route") == CASE3_ROUTE and item.get("co_reference") == co_reference
                and _uuid(item.get("account_uuid")) == wanted and isinstance(item.get("account_id"), str)
                and _ID.fullmatch(item["account_id"]) and isinstance(name, str) and name.strip()
                and isinstance(stamp, str) and _STAMP.fullmatch(stamp)):
            found.append(item)
    if not found:
        return None
    best = max(found, key=lambda item: item["recorded_at"])
    return {"recorded_at": best["recorded_at"], "account_id": best["account_id"], "account_uuid": wanted,
            "tenant_name": best["tenant_name"].strip()}


def apply(result: Mapping[str, Any], verified: Mapping[str, Any] | None) -> dict[str, Any]:
    """``result`` with ``uuid_not_in_clone`` replaced by ``onboarded`` when ``verified`` is the live evidence; else a copy."""
    out = dict(result)
    if verified is None or out.get("status") != "uuid_not_in_clone":
        return out
    moment = _STAMP.fullmatch(verified["recorded_at"])
    when = (moment.group(1) + " " + moment.group(2) + " UTC") if moment else verified["recorded_at"]
    line = ("Onboarded - tenant verified live in BackOffice by a read-only readback (" + when + "); the clone has not caught up yet.")
    out.update(status="onboarded", reason=line, review={"line": line, "warnings": []},
               target={"id": verified["account_id"], "name": verified["tenant_name"], "domain": None, "created": None,
                       "deleted": False, "enabled": True, "live_paid": True, "license_type": None},
               verified_live=dict(verified))
    return out
