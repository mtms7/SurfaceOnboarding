"""Progress bar and final banner of the operator Edit Account (owner request 2026-11-13, CO-0843).

The owner clicked "Set Operator from Technical Advisor", a BackOffice window opened and the page showed nothing until the
run ended. The runner records fixed milestones (``run_progress.OPERATOR_EDIT_MILESTONES``: step and result words only, never a
name); this module turns them into ordered steps, a percentage and one banner saying whether the operator and the Maximum
scan Duration WERE set or NOT, and why. The same milestones are written by the Edit Account right after a verified create.
Pure: no I/O.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from html import escape
from typing import Any

from . import production_case3_edit as edit

STEPS: tuple[str, ...] = (
    "Sign in to BackOffice and find the tenant",
    "Open Edit Account",
    "Pick the Technical Advisor's operator",
    "Maximum scan Duration (CE-only 24 h, Case 3 90 h)",
    "Confirm once and read the tenant back",
)
DONE, ACTIVE, PENDING, FAILED, SKIPPED = "done", "active", "pending", "failed", "skipped"
RUNNING_MAX_PERCENT = 95
STALE_SECONDS = 25 * 60  # longer than the sign-in wait plus the edit; a crashed run stops showing as running after this
WORD = re.compile(r"[a-z0-9][a-z0-9_]{0,47}")
OK_RESULTS = frozenset({edit.VERIFIED, edit.ALREADY_SET})
PARTIAL_RESULTS = frozenset({edit.UNMATCHED, "edit_operator_unknown"})  # hours saved, operator by hand


def safe_word(value: Any) -> str:
    return value if isinstance(value, str) and WORD.fullmatch(value) else "other"


def current_run(events: Any) -> list[dict[str, str]]:
    """The milestones of the newest operator run: from its newest ``op_start`` on ([] when there is none)."""
    items = [e for e in (events if isinstance(events, (list, tuple)) else ())
             if isinstance(e, Mapping) and isinstance(e.get("step"), str) and e["step"].startswith("op_")]
    starts = [i for i, e in enumerate(items) if e["step"] == "op_start"]
    return [dict(e) for e in items[starts[-1]:]] if starts else []


def _found(events: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    return {str(e.get("step")): safe_word(e.get("outcome")) for e in events}


def progress(events: Any, *, running: bool) -> dict[str, Any]:
    """``{"steps": [(label, state)], "percent", "status": running|done|partial|failed, "result", "current"}``."""
    found = _found(current_run(events))
    result = found.get("op_result")
    finished = result is not None or not running
    if finished and result is None:
        result = "runner_ended_without_a_result"
    operator = found.get("op_operator")
    evidence: list[str | None] = [
        DONE if "op_tenant_found" in found else None,
        DONE if "op_form_open" in found else None,
        (DONE if operator == "picked" else FAILED if operator in ("unmatched", "failed") else SKIPPED if operator == "none"
         else None),
        DONE if found.get("op_scan") in ("set", "form_default") else FAILED if "op_scan" in found else None,
        (DONE if found.get("op_verified") == edit.VERIFIED else FAILED if "op_verified" in found
         else FAILED if "op_saved" in found and found["op_saved"] != "ok" else None),
    ]
    if result == edit.ALREADY_SET:
        evidence = [DONE, SKIPPED, SKIPPED, SKIPPED, SKIPPED]
    states: list[str] = []
    blocked = False
    for index, seen in enumerate(evidence):
        if seen is not None:
            # an unmatched operator does not block the rest (owner decision 2026-11-13)
            states.append(seen)
            blocked = blocked or (seen == FAILED and index != 2)
        elif not finished:
            states.append(PENDING if blocked or ACTIVE in states else ACTIVE)
        elif result in OK_RESULTS or result in PARTIAL_RESULTS:
            states.append(SKIPPED)
        elif not blocked:
            states.append(FAILED)
            blocked = True
        else:
            states.append(SKIPPED)
    complete = sum(state in (DONE, SKIPPED) for state in states)
    if finished and result in OK_RESULTS:
        status, percent = "done", 100
    else:
        percent = min(RUNNING_MAX_PERCENT, 5 + (100 - 5) * complete // len(STEPS))
        status = ("running" if not finished else "partial" if result in PARTIAL_RESULTS else "failed")
    current = next((STEPS[i] for i, s in enumerate(states) if s in (ACTIVE, FAILED)), "")
    return {"steps": list(zip(STEPS, states)), "percent": percent, "status": status, "result": result, "current": current}


REFUSAL_TEXT = {
    "production_sign_in_timeout": "the BackOffice sign-in was not completed in time; nothing was changed.",
    "production_window_signed_out": "the kept BackOffice window is signed out; nothing was changed. Click again and sign in.",
    "operator_edit_failed": "the run stopped with an error; check the tenant's Edit Account in BackOffice.",
    "operator_edit_needs_review": "an earlier run may have saved the form; check the tenant in BackOffice before clicking again.",
    "technical_advisor_missing": "the Salesforce Account has no Technical Advisor; assign the operator by hand.",
    "no_verified_create": "this dashboard has no verified create for this CO.",
    "edit_operator_already_other": "the tenant already has another operator; it is never replaced automatically.",
    "edit_row_not_found": "the tenant row was not found by its name and ID; nothing was changed.",
    "edit_session_expired": "the BackOffice session expired; nothing was changed.",
    "edit_form_unavailable": "the Edit Account form did not open as expected; nothing was saved.",
    "edit_scan_control_absent": "the Maximum scan Duration field was not found; nothing was saved.",
    "runner_ended_without_a_result": "the run ended without recording a result; check the tenant in BackOffice.",
}


def banner(result: str | None, hours: int = edit.SCAN_HOURS) -> tuple[str, str] | None:
    """(css, text) of the final result, else None."""
    if not result:
        return None
    word = safe_word(result)
    if word in OK_RESULTS:
        return "ok", edit.result_text(word, hours) or "Operator set."
    if word in PARTIAL_RESULTS:
        return "warn", edit.result_text(word, hours) or "Operator NOT set: assign it by hand."
    base = word[:-len("_form_left_open")] if word.endswith("_form_left_open") else word
    text = "Operator NOT set: " + (REFUSAL_TEXT.get(base) or "stopped (" + base + ").")
    if base != word:
        text += " The Edit Account form was left open in the BackOffice window: close it with Cancel."
    return "bad", text


def card_html(view: Mapping[str, Any], hours: int = edit.SCAN_HOURS) -> str:
    """CSS-only progress block (no script): steps, percentage and, once finished, the banner."""
    status = view.get("status")
    if status == "running":
        chip, head = "<span class='chip chip-warn'>Running</span>", "In progress: " + str(view.get("current") or "")
    elif status == "done":
        chip, head = "<span class='chip chip-ok'>Done</span>", "Finished"
    elif status == "partial":
        chip, head = "<span class='chip chip-warn'>Operator by hand</span>", "Finished: operator not matched"
    else:
        chip, head = "<span class='chip chip-bad'>Stopped</span>", "Stopped at: " + str(view.get("current") or "the start")
    percent = str(int(view.get("percent") or 0))
    css = " runprog-done" if status == "done" else " runprog-failed" if status == "failed" else ""
    items = "".join("<li class='s-" + escape(state) + "'>" + escape(label)
                    + (" (running)" if state == ACTIVE else " (skipped)" if state == SKIPPED else "") + "</li>"
                    for label, state in view.get("steps") or ())
    final = banner(view.get("result"), hours) if status != "running" else None
    return ("<div class='runprog" + css + "' aria-live='polite'><b>Set Operator from Technical Advisor</b> " + chip
            + "<div class='runprog-head'><span>" + escape(head) + "</span><span>" + percent + "%</span></div>"
            "<div class='runprog-bar' role='progressbar' aria-label='Operator edit progress' aria-valuemin='0' "
            "aria-valuemax='100' aria-valuenow='" + percent + "'><span class='runprog-fill' style='width:" + percent
            + "%'></span></div><ol class='runprog-steps'>" + items + "</ol></div>"
            + ("<p class='banner banner-" + final[0] + "' role='status'>" + escape(final[1]) + "</p>" if final else ""))
