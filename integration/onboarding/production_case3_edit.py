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
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from . import production_contract

SCAN_HOURS = 90
SCAN_HOURS_TEXT = str(SCAN_HOURS)
# Owner decision 2026-11-13 (CO-0845 HAR): the same Edit Account also runs after a CE-only create, with the Technical Advisor's
# operator and Maximum scan Duration 24 h; anything with Surface keeps 90 h.
CE_ONLY_SCAN_HOURS = 24
ROUTE_SCAN_HOURS = {"case_2_new_ce_only": CE_ONLY_SCAN_HOURS, "case_3_combined_baseline": SCAN_HOURS}
OPERATOR_SUFFIX = " Operator"  # seen for Frank Hoeffle (CO-0800) and Yusuf Hassan (CO-0757)
STEP = "bo_edit"  # the step name of the result lines in ``case3_followup.WRITES_FILE``
_PERSON = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'\-]){1,78}[^\W\d_.]?")

VERIFIED = "edit_verified"
ALREADY_SET = "edit_already_set"
# Outcomes after which the Edit form was confirmed (the row may have changed): never repeated.
WRITE_OUTCOMES = frozenset({VERIFIED, "edit_saved_unverified", "edit_save_failed", "edit_save_no_signal", "edit_id_mismatch",
                            "edit_other_fields_changed", "edit_values_not_kept", "edit_operator_unknown",
                            "edit_operator_unmatched"})
UNMATCHED = "edit_operator_unmatched"  # Maximum scan Duration set; no single BackOffice operator matched the Technical Advisor
TEXT = {
    VERIFIED: "Operator and Maximum scan Duration 90 set in BackOffice by Edit Account and read back.",
    ALREADY_SET: "Operator and Maximum scan Duration 90 were already set on the tenant.",
    "edit_operator_unknown": "No Technical Advisor operator could be named; Maximum scan Duration 90 was set, assign the operator by hand.",
    UNMATCHED: ("No single BackOffice operator account matches the Technical Advisor; Maximum scan Duration 90 was set. Create "
                "or pick the Technical Advisor's operator account in BackOffice (Add Operator Account, then Edit Account)."),
}


def result_text(result: str, hours: int = SCAN_HOURS) -> str | None:
    """The owner-facing sentence of an edit result for the route's hours (TEXT is the Case 3 / 90 h wording)."""
    text = TEXT.get(result)
    return text.replace("Duration 90", "Duration " + str(hours)) if text else None


# --- the Technical Advisor -> BackOffice operator match (owner decision 2026-11-13) -------------------------------------------
# Salesforce "Alejandro Espinosa Álvarez" is BackOffice "Alejandro Espinosa Alvarez Operator"; other labels read
# "Alex Rosniewski - Operator". Compared without accents, case, punctuation, extra spaces and the word "operator"; exactly one
# option must match, never a guess.

def person_key(value: Any) -> str:
    """The comparison key of a person or operator label ("" for anything unusable)."""
    if not isinstance(value, str):
        return ""
    folded = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).casefold()
    words = [word for word in re.split(r"[^0-9a-z]+", folded) if word and word != "operator"]
    return " ".join(words)


def same_person(label: Any, advisor: Any) -> bool:
    key = person_key(advisor)
    return bool(key) and person_key(label) == key


def search_text(advisor: Any) -> str:
    """What to type in the Operator Account search: the person's name without accents (the options carry none)."""
    return person_key(advisor).title()


def label_candidates(advisor: Any) -> list[str]:
    """The option labels this person's operator account may carry (seen: "<Name> Operator", "<Name> - Operator"), with the
    name as Salesforce writes it and without accents; used when the list exposes no ``role=option`` items (CO-0843,
    2026-11-13: a checkbox list). Every candidate is still the same person (``same_person``)."""
    if not isinstance(advisor, str) or not person_key(advisor):
        return []
    plain = " ".join(advisor.split())
    for suffix in (OPERATOR_SUFFIX, " - Operator"):
        if plain.endswith(suffix):
            plain = plain[: -len(suffix)].strip()
    names = [plain, "".join(c for c in unicodedata.normalize("NFKD", plain) if not unicodedata.combining(c))]
    out: list[str] = []
    for name in names:
        for label in (name + OPERATOR_SUFFIX, name + " - Operator"):
            if label not in out and same_person(label, advisor):
                out.append(label)
    return out


def pick_operator(labels: Iterable[Any], advisor: Any) -> tuple[str | None, str]:
    """(the one option label matching ``advisor``, "ok") or (None, "edit_operator_not_found" | "edit_operator_ambiguous")."""
    found = sorted({" ".join(label.split()) for label in labels if isinstance(label, str) and same_person(label, advisor)})
    if len(found) == 1:
        return found[0], "ok"
    return None, "edit_operator_not_found" if not found else "edit_operator_ambiguous"


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


def before_edit(reply: Any, intended: Mapping[str, Any], account_id: str, operator: str | None,
                scan_hours: int = SCAN_HOURS) -> str:
    """``ok`` (edit), ``edit_already_set`` (nothing to do) or a code that stops before the form opens."""
    state, row = _row(reply, intended, account_id)
    if row is None:
        return state
    names = operator_names(row)
    if names is None:
        return "edit_row_unexpected"
    named = len(names) == 1 and bool(operator) and same_person(names[0], operator)
    if row.get("campaignsTimeoutInHours") == scan_hours and (named or not operator):
        return ALREADY_SET
    if names and not named:
        return "edit_operator_already_other"  # someone set a different operator: never replaced automatically
    return "ok"


def after_edit(reply: Any, intended: Mapping[str, Any], expected_row: Mapping[str, Any], account_id: str,
               operator: str | None, scan_hours: int = SCAN_HOURS) -> str:
    """``edit_verified`` when the re-read row has 90 and exactly the operator (if one was set) and every field the create
    verified still matches; else a code naming what is wrong (names only, never values)."""
    state, row = _row(reply, intended, account_id)
    if row is None:
        return "edit_saved_unverified" if state != "edit_session_expired" else state
    mismatches, uuid_ok = production_contract.compare_readback(expected_row, row)
    if mismatches or not uuid_ok:
        return "edit_other_fields_changed"
    names = operator_names(row)
    if (row.get("campaignsTimeoutInHours") != scan_hours or names is None
            or (operator and not (len(names) == 1 and same_person(names[0], operator)))
            or (not operator and names)):  # 2026-11-13: no operator chosen = none may appear (never a wrong one saved)
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
