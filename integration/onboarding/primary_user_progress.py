"""Progress bar and final banner of the Case 3 primary-user run (owner request 2026-11-11, CO-0844).

The owner clicked "Create primary user in BackOffice, then User Created", a BackOffice window opened and the page kept
showing an older "Last result" line. This module turns the runner's own milestones (``run_progress.PRIMARY_USER_MILESTONES``,
fixed step and result words only, never a name or an email) into ordered steps, a percentage and one clear banner saying
whether the customer's Admin user WAS created or NOT, and why. Pure: no I/O; the dashboard passes the events, whether the
runner is still running, and the result word of its Salesforce stage move.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from html import escape
from typing import Any

from . import production_case3_primary_user as primary

STEPS: tuple[str, ...] = (
    "Sign in to BackOffice and find the tenant",
    "Take control of the tenant (Access)",
    "Read the tenant's Users list",
    "Add the Primary User with role Admin (only if missing)",
    "Read the user back",
    "Give control back",
    "Salesforce stage → User Created",
)
DONE, ACTIVE, PENDING, FAILED, SKIPPED = "done", "active", "pending", "failed", "skipped"
RUNNING_MAX_PERCENT = 95
# A run that recorded its start but no result is treated as still running for this long (single-flight across a dashboard
# restart); longer than the 15-minute sign-in wait plus the BackOffice steps. A crashed run blocks a new click until then.
STALE_SECONDS = 25 * 60
WORD = re.compile(r"[a-z0-9][a-z0-9_]{0,47}(?:\+[a-z_]{1,24})?")
OK_RESULTS = frozenset({primary.CREATED, primary.ALREADY, primary.BY_HAND, "already_done"})
UNCERTAIN_RESULTS = frozenset({"user_created_unverified", "add_no_signal"})
STAGE_OK = frozenset({"written_verified", "already_present"})


def safe_word(value: Any) -> str:
    """A fixed result/state word as recorded, else "other" (a value that is not a plain word is never shown or stored)."""
    return value if isinstance(value, str) and WORD.fullmatch(value) else "other"


def current_run(events: Any) -> list[dict[str, str]]:
    """The milestones of the newest primary-user run: from its newest ``pu_start`` on ([] when there is none)."""
    items = [e for e in (events if isinstance(events, (list, tuple)) else ())
             if isinstance(e, Mapping) and isinstance(e.get("step"), str) and e["step"].startswith("pu_")]
    starts = [i for i, e in enumerate(items) if e["step"] == "pu_start"]
    return [dict(e) for e in items[starts[-1]:]] if starts else []


def run_started_at(events: Any) -> str:
    run = current_run(events)
    return str(run[0].get("t") or "") if run else ""


def _found(events: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for event in events:
        found[str(event.get("step"))] = safe_word(event.get("outcome"))
    return found


def run_result(events: Any) -> str | None:
    """The final result word of the newest run (``pu_result``), else None."""
    return _found(current_run(events)).get("pu_result")


def progress(events: Any, *, running: bool, stage: str | None = None, follow_running: bool = False) -> dict[str, Any]:
    """Steps of the newest run from its recorded milestones only.

    ``running``: the runner process is still running. ``stage``: the result word of the dashboard's User Created move (None
    while it has not run). ``follow_running``: the runner ended and the dashboard's stage move is still to come.
    Returns ``{"steps": [(label, state)], "percent", "status": running|done|failed, "result", "current"}``."""
    found = _found(current_run(events))
    result = found.get("pu_result")
    finished = result is not None or not running
    if finished and result is None:
        result = "runner_ended_without_a_result"
    base = (result or "").partition("+")[0]
    users = found.get("pu_users_read")
    evidence: list[str | None] = [
        DONE if "pu_tenant_found" in found else None,
        DONE if "pu_control_taken" in found else None,
        DONE if users in ("present_admin", "absent", "present_other_role") else FAILED if users else None,
        (SKIPPED if users == "present_admin" else FAILED if users == "present_other_role"
         else DONE if found.get("pu_add_sent") == "add_accepted" else FAILED if "pu_add_sent" in found else None),
        (SKIPPED if users in ("present_admin", "present_other_role")
         else DONE if found.get("pu_add_verified") == "present_admin" else FAILED if "pu_add_verified" in found else None),
        (DONE if found.get("pu_control_released") == "ok" else FAILED if "pu_control_released" in found
         else FAILED if finished and "pu_control_taken" in found else None),
    ]
    states: list[str] = []
    blocked = False
    for index, seen in enumerate(evidence):
        if index == 5 and seen is None and finished:
            states.append(SKIPPED)  # control was never taken: nothing to give back
            continue
        if seen is not None:
            states.append(seen)
            if seen == FAILED and index != 5:
                blocked = True
            continue
        if not finished:
            states.append(PENDING if blocked or ACTIVE in states else ACTIVE)
        elif not blocked and base not in OK_RESULTS:
            states.append(FAILED)
            blocked = True
        else:
            states.append(SKIPPED)
    if stage in STAGE_OK:
        states.append(DONE)
    elif stage is not None:
        states.append(FAILED if base in OK_RESULTS else SKIPPED)
    elif finished and base in OK_RESULTS:
        states.append(ACTIVE if follow_running else PENDING)
    else:
        states.append(SKIPPED if finished else PENDING)
    complete = sum(state in (DONE, SKIPPED) for state in states)
    all_ok = finished and base in OK_RESULTS and stage in STAGE_OK and FAILED not in states
    if all_ok:
        percent = 100
    else:
        percent = min(RUNNING_MAX_PERCENT, 5 + (100 - 5) * complete // len(STEPS))
    status = ("running" if not finished or (base in OK_RESULTS and follow_running and stage is None)
              else "done" if base in OK_RESULTS and FAILED not in states else "failed")
    current = next((STEPS[i] for i, s in enumerate(states) if s in (ACTIVE, FAILED)), "")
    return {"steps": list(zip(STEPS, states)), "percent": percent, "status": status, "result": result, "current": current}


STAGE_TEXT = {
    "written_verified": "Salesforce stage moved to User Created and read back.",
    "already_present": "Salesforce stage is already User Created.",
    "primary_user_not_done": "Salesforce stage NOT moved: the Admin user is not confirmed in the tenant.",
    "follow_up_failed": "Salesforce stage NOT moved: the stage step failed; check the CO in Salesforce.",
}
RELEASE_WARNING = "Control of the tenant was NOT given back: open BackOffice and click Give up control."


def banner(result: str | None, stage: str | None = None) -> list[tuple[str, str]]:
    """``[(css, text)]``: the user verdict (ok / bad), the release warning (warn) and the stage line, in that order."""
    if not result:
        return []
    word = safe_word(result)
    base, _, suffix = word.partition("+")
    out: list[tuple[str, str]] = []
    if base == primary.CREATED:
        out.append(("ok", "Customer Admin user created and verified: " + primary.RESULT_TEXT[base]))
    elif base == primary.ALREADY:
        out.append(("ok", "Customer Admin user already present: " + primary.RESULT_TEXT[base]))
    elif base == primary.BY_HAND:
        out.append(("ok", "Confirmed by hand: " + primary.RESULT_TEXT[base]))
    elif base == "already_done":
        out.append(("ok", "Customer Admin user already done: an earlier run recorded it; nothing was repeated."))
    else:
        reason = primary.RESULT_TEXT.get(base) or _refusal_text(base)
        head = "User creation NOT confirmed: " if base in UNCERTAIN_RESULTS else "User NOT created: "
        out.append(("bad", head + reason))
    if suffix == primary.CONTROL_NOT_RELEASED:
        out.append(("warn", RELEASE_WARNING))
    if stage:
        stage_word = safe_word(stage)
        text = STAGE_TEXT.get(stage_word) or "Salesforce stage NOT moved: " + _refusal_text(stage_word)
        out.append(("ok" if stage_word in STAGE_OK else "bad", text))
    return out


def _refusal_text(code: str) -> str:
    from .production_create_ui import gate_text

    texts = {
        "production_sign_in_timeout": "the BackOffice sign-in was not completed in time; nothing was done.",
        "production_window_signed_out": "the kept BackOffice window is signed out; nothing was done. Click again and sign in.",
        "primary_user_failed": "the run stopped with an error before any change; check the tenant's Users in BackOffice.",
        "primary_user_already_running": "another primary-user run for this CO is still running; nothing was started.",
        "primary_user_needs_review": "an earlier run may have sent the add request; check the tenant's Users in BackOffice.",
        "runner_ended_without_a_result": "the run ended without recording a result; check the tenant's Users in BackOffice.",
        "primary_user_record_failed": "the intent line could not be recorded, so nothing was sent.",
    }
    return texts.get(code) or gate_text(code)


def _chip(css: str, text: str) -> str:
    return "<span class='chip " + css + "'>" + escape(text) + "</span>"


def banner_html(result: str | None, stage: str | None = None) -> str:
    return "".join("<p class='banner banner-" + css + "' role='status'>" + escape(text) + "</p>"
                   for css, text in banner(result, stage))


def card_html(view: Mapping[str, Any], stage: str | None = None) -> str:
    """CSS-only progress block (no script): the steps, the percentage and, once finished, the banner."""
    status = view.get("status")
    if status == "running":
        chip, head = _chip("chip-warn", "Running"), "In progress: " + str(view.get("current") or "")
    elif status == "done":
        chip, head = _chip("chip-ok", "Done"), "Finished"
    else:
        chip, head = _chip("chip-bad", "Stopped"), "Stopped at: " + str(view.get("current") or "the start")
    percent = str(int(view.get("percent") or 0))
    css = " runprog-done" if status == "done" else " runprog-failed" if status == "failed" else ""
    items = "".join("<li class='s-" + escape(state) + "'>" + escape(label)
                    + (" (running)" if state == ACTIVE else " (skipped)" if state == SKIPPED else "") + "</li>"
                    for label, state in view.get("steps") or ())
    return ("<div class='runprog" + css + "' aria-live='polite'>" + chip + "<div class='runprog-head'><span>" + escape(head)
            + "</span><span>" + percent + "%</span></div><div class='runprog-bar' role='progressbar' "
            "aria-label='Primary user progress' aria-valuemin='0' aria-valuemax='100' aria-valuenow='" + percent + "'>"
            "<span class='runprog-fill' style='width:" + percent + "%'></span></div><ol class='runprog-steps'>" + items
            + "</ol></div>" + (banner_html(view.get("result"), stage) if status != "running" else ""))
