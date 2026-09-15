"""Deterministic, sanitised text for a future Salesforce rejection-note field.

This module never calls Salesforce. It creates text only; a separate,
explicitly approved writeback recipe will decide whether to append it to
``Onboarding_Rejection_Notes__c``.
"""

from __future__ import annotations

from typing import Iterable, Mapping


MAX_NOTE_CHARACTERS = 1800
POLICY_VERSION = "phase1-shadow-v1"


def _value(value: object) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()[:256]
    return "not provided"


def render_rejection_note(
    *,
    co_number: str,
    mismatches: Iterable[Mapping[str, object]],
    blocking_gates: Iterable[str] = (),
) -> str:
    """Render a bounded, audit-friendly manual-review summary.

    Inputs are normalised facts only. Do not pass filenames, URLs, raw OCR
    text, contacts, or document content to this function.
    """
    safe_co_number = _value(co_number)
    mismatch_lines = []
    for mismatch in mismatches:
        field = _value(mismatch.get("field"))
        dealhub_value = _value(mismatch.get("dealhub_value"))
        contract_value = _value(mismatch.get("contract_value"))
        mismatch_lines.append(
            f"- {field}: DealHub={dealhub_value}; contract={contract_value}"
        )

    lines = [
        f"[Phase 1 shadow validation | {POLICY_VERSION}] {safe_co_number} is blocked.",
        "Commercial evidence mismatches:",
        *(mismatch_lines or ["- no comparison details provided"]),
    ]
    safe_gates = [_value(gate) for gate in blocking_gates if _value(gate) != "not provided"]
    if safe_gates:
        lines.extend(["Additional blocking gates:", *(f"- {gate}" for gate in safe_gates)])
    lines.append(
        "Required action: CSM must reconcile Salesforce/DealHub and commercial evidence. "
        "No provisioning is authorized."
    )
    return "\n".join(lines)[:MAX_NOTE_CHARACTERS]
