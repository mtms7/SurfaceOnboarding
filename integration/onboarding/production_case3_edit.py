"""The one Edit Account after a verified Case 3 production create (owner go 2026-11-02, CO-0757).

Owner HARs 2026-11-02: right after the automated create the tenant row had ``campaignsTimeoutInHours: null`` and
``operatorAccounts: null`` although the create body carried 90; after the owner's Edit Account it had 90 and
``[{operatorName: "Yusuf Hassan Operator"}]``. BackOffice keeps both only through Edit Account
(``POST /api/v1/backoffice/account/{id}/edit``). The runner (``_pcr_case3_edit``) opens Edit for exactly the created tenant,
sets these two values, confirms once and re-reads the row. This module holds the pure rules: the operator name, the checks
on the row before and after, and the result codes. It reads and writes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import production_contract

SCAN_HOURS = 90
SCAN_HOURS_TEXT = str(SCAN_HOURS)
OPERATOR_SUFFIX = " Operator"  # seen for Frank Hoeffle (CO-0800) and Yusuf Hassan (CO-0757)
STEP = "bo_edit"  # the step name of the result lines in ``case3_followup.WRITES_FILE``
_PERSON = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'\-]){1,78}[^\W\d_.]?")

VERIFIED = "edit_verified"
ALREADY_SET = "edit_already_set"
# Outcomes after which the Edit form was confirmed (the row may have changed): never repeated.
WRITE_OUTCOMES = frozenset({VERIFIED, "edit_saved_unverified", "edit_save_failed", "edit_save_no_signal", "edit_id_mismatch",
                            "edit_other_fields_changed", "edit_values_not_kept"})
TEXT = {
    VERIFIED: "Operator and Maximum scan Duration 90 set in BackOffice by Edit Account and read back.",
    ALREADY_SET: "Operator and Maximum scan Duration 90 were already set on the tenant.",
    "edit_operator_unknown": "No Technical Advisor operator could be named; Maximum scan Duration 90 was set, assign the operator by hand.",
}


def operator_name(technical_advisor: Any) -> str | None:
    """``"<Technical Advisor> Operator"`` for a plain person name (letters, spaces, . ' -), else None (never a guess)."""
    if not isinstance(technical_advisor, str):
        return None
    name = " ".join(technical_advisor.split())
    return name + OPERATOR_SUFFIX if name and _PERSON.fullmatch(name) and not name.endswith(OPERATOR_SUFFIX) else None


def _row(reply: Any, intended: Mapping[str, Any], account_id: str) -> tuple[str, Mapping[str, Any] | None]:
    status = reply.get("status") if isinstance(reply, Mapping) else None
    if status in (401, 403):
        return "edit_session_expired", None
    rows = reply.get("rows") if isinstance(reply, Mapping) else None
    if status != 200 or not isinstance(rows, list):
        return "edit_row_unavailable", None
    row, _code = production_contract.select_unique_row(rows, str(intended.get("tenant_name") or ""),
                                                       str(intended.get("primary_domain") or ""))
    if row is None or row.get("id") != account_id:
        return "edit_row_not_found", None
    return "ok", row


def operator_names(row: Mapping[str, Any]) -> list[str] | None:
    """The operator account names on the row ([] for none / null); None when the value has an unknown shape."""
    value = row.get("operatorAccounts")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        return None
    names = [item.get("operatorName") for item in value]
    return names if all(isinstance(name, str) for name in names) else None


def before_edit(reply: Any, intended: Mapping[str, Any], account_id: str, operator: str | None) -> str:
    """``ok`` (edit), ``edit_already_set`` (nothing to do) or a code that stops before the form opens."""
    state, row = _row(reply, intended, account_id)
    if row is None:
        return state
    names = operator_names(row)
    if names is None:
        return "edit_row_unexpected"
    if row.get("campaignsTimeoutInHours") == SCAN_HOURS and names == ([operator] if operator else names):
        return ALREADY_SET
    if names and names != [operator]:
        return "edit_operator_already_other"  # someone set a different operator: never replaced automatically
    return "ok"


def after_edit(reply: Any, intended: Mapping[str, Any], expected_row: Mapping[str, Any], account_id: str,
               operator: str | None) -> str:
    """``edit_verified`` when the re-read row has 90 and exactly the operator (if one was set) and every field the create
    verified still matches; else a code naming what is wrong (names only, never values)."""
    state, row = _row(reply, intended, account_id)
    if row is None:
        return "edit_saved_unverified" if state != "edit_session_expired" else state
    mismatches, uuid_ok = production_contract.compare_readback(expected_row, row)
    if mismatches or not uuid_ok:
        return "edit_other_fields_changed"
    names = operator_names(row)
    if row.get("campaignsTimeoutInHours") != SCAN_HOURS or names is None or (operator and names != [operator]):
        return "edit_values_not_kept"
    return VERIFIED


def edit_response_problem(method: Any, path_id: Any, status: Any, account_id: str) -> str | None:
    """The app's own edit reply: exactly this tenant and 2xx, else the code."""
    if str(method).upper() != "POST" or path_id != account_id:
        return "edit_id_mismatch"
    if status in (401, 403):
        return "edit_session_expired"
    if not isinstance(status, int) or not 200 <= status < 300:
        return "edit_save_failed"
    return None
