"""Dashboard side of the production DRY RUN of a Case 3 CO (Surface & Credential Exposure, New; CO-0800).

Owner decision 2026-10-20: the dry run starts from a button on the CO page and shows a progress bar. Pure and injectable like
``production_create_ui`` (it never starts a process, reads Salesforce, opens a browser or imports the dashboard); it reuses that
module's ``Inputs``, one-time tokens and single-run registry.

* Keyed on ``PRODUCTION_DRY_RUN_SUPPORTED`` ONLY (the runner constant stays False): the dry run fills the real production form
  and Cancels, never clicking Confirm, never consuming the approval, never writing to Salesforce. It never opens the create.
* Gates (first refusal wins, all re-judged at the POST): desktop only; the dry-run constant True; a Case 3 CO; a valid unused
  Case 3 approval for this CO bound to the CURRENT revision; the operator is the approval's issuer; the session unlock;
  Onboarding Approval Status Approved; Salesforce data fresh and the session ready; no Account UUID and no Surface Account ID;
  Redash clone fresh; no run already launched. One click, one one-time token, an explicit authorization box.
* The progress bar is built only from the milestones the runner recorded and from its recorded result (``run_progress``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from . import production_approval, production_create, production_views, run_progress
from . import production_create_ui as create_ui
from . import session_readiness as readiness

DRY_RUN_PATH = "/attended/production-dry-run"
CASE3_ROUTE = production_create.CASE3_ROUTE
CASE3_PRODUCT = "Surface & Credential Exposure"
CASE3_TYPE = "New Product Onboarding"
RESULT_OUTCOMES = (production_create.OUTCOME_DRY_RUN, production_create.OUTCOME_REFUSED)
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

GATE_TEXT = {
    "vm_read_only": "The VM dashboard is read-only guidance; the dry run runs from the owner's Windows desktop.",
    "production_dry_run_not_supported": "The production dry run is locked in this build.",
    "salesforce_not_fresh": "The Salesforce data is not fresh: refresh the page.",
    "route_not_case3": "This CO is not a Case 3 (Surface & Credential Exposure, New) onboarding.",
    "approval_missing": "No Case 3 production approval is recorded for this CO (the owner issues one with "
                        "tools/issue_production_approval.py).",
    "approval_route_mismatch": "The approval on record is not a Case 3 approval for this CO.",
    "approval_expired": "The Case 3 approval is expired.",
    "approval_consumed": "The Case 3 approval was already used.",
    "approval_superseded": "The Case 3 approval was superseded.",
    "approval_unreadable": "The approval record cannot be read.",
    "approval_revision_drift": "The CO changed in Salesforce since the approval: issue a new approval.",
    "operator_not_owner": "Only the owner who issued the approval can start the dry run.",
    "production_locked": "Production sessions are not unlocked on this desktop.",
    "salesforce_not_approved": "Onboarding Approval Status is not Approved in Salesforce.",
    "salesforce_session_not_ready": "The Salesforce session is not ready: sign in again.",
    "salesforce_prior_uuid_present": "The CO already has an Account UUID or a Surface Account ID.",
    "clone_stale": "The Redash clone is older than 6 hours, its data older than 36 hours, or unusable: refresh it.",
    "launch_in_progress": "A production run is already in progress.",
    "confirmation_invalid": "The confirmation was missing, expired or already used. Review the page again.",
    "authorization_missing": "The explicit authorization box was not ticked.",
    "launch_failed": "The runner could not be started. Nothing was filled or created.",
    "invalid_request": "The request did not match this CO.",
    "gate_unreadable": "The dry-run gate could not be evaluated: nothing is allowed.",
}
OBSERVATION_LABELS = (("max_scan_duration_control", "Maximum scan Duration control"),
                      ("scan_now_control", "Scan now control once an interval is chosen"))


def text(code: object) -> str:
    base = str(code).split(":", 1)[0]
    return GATE_TEXT.get(base) or create_ui.gate_text(base)


def route_for_row(row: Mapping[str, Any] | None) -> str | None:
    """``case_3_combined_baseline`` for a Surface & Credential Exposure / New Product Onboarding CO, else None."""
    if (isinstance(row, Mapping) and row.get("Onboarding_Product__c") == CASE3_PRODUCT
            and row.get("Onboarding_Type__c") == CASE3_TYPE):
        return CASE3_ROUTE
    return None


def build_lock(vm: bool) -> str | None:
    if vm is True:
        return "vm_read_only"
    if readiness.PRODUCTION_DRY_RUN_SUPPORTED is not True:
        return "production_dry_run_not_supported"
    return None


def gate(inputs: create_ui.Inputs) -> tuple[str | None, dict[str, Any]]:
    """(first refusal code or None, context with ``approval`` and ``env``). Pure; the runner constant is never consulted."""
    ctx: dict[str, Any] = {"approval": None, "env": None}
    lock = build_lock(inputs.vm)
    if lock is not None:
        return lock, ctx
    if not isinstance(inputs.row, Mapping) or not create_ui.CO_PATTERN.fullmatch(inputs.reference):
        return "salesforce_not_fresh", ctx
    if route_for_row(inputs.row) != CASE3_ROUTE:
        return "route_not_case3", ctx
    listing = production_views.list_approvals(inputs.approvals_path, inputs.now)
    if listing["state"] != "ok":
        return "approval_unreadable", ctx
    mine = [i for i in listing["items"] if i["co_reference"] == inputs.reference]
    valid = [i for i in mine if i["state"] == "valid"]
    if not valid:
        state = mine[0]["state"] if mine else "missing"
        return "approval_" + state if state in ("expired", "consumed", "superseded") else "approval_missing", ctx
    record = production_approval.record_for(inputs.approvals_path, valid[0]["approval_ref"])
    if record is None:
        return "approval_unreadable", ctx
    if record.get("route") != CASE3_ROUTE:
        return "approval_route_mismatch", ctx
    ctx["approval"] = record
    if not create_ui._owner_ok(inputs.operator, record):
        return "operator_not_owner", ctx
    env = {**inputs.environ, **production_approval.unlock_environ(record)}
    ctx["env"] = env
    if env.get(create_ui.SESSIONS_ENV) != "1" or readiness.production_dry_run_unlock_problem(env, inputs.now) is not None:
        return "production_locked", ctx
    revision = inputs.row.get("LastModifiedDate")
    if not isinstance(revision, str) or not revision or revision != record.get("source_revision"):
        return "approval_revision_drift", ctx
    problem = production_approval.check(inputs.approvals_path, record["approval_ref"], inputs.reference, CASE3_ROUTE,
                                        revision, inputs.now)
    if problem is not None:
        return problem, ctx
    if inputs.row.get("Onboarding_Approval_Status__c") != "Approved":
        return "salesforce_not_approved", ctx
    if inputs.row_fresh is not True:
        return "salesforce_not_fresh", ctx
    if inputs.sf_ready is not True:
        return "salesforce_session_not_ready", ctx
    for field in ("Account_UUID__c", "Surface_Account_ID__c"):
        if field not in inputs.row or str(inputs.row.get(field) or "").strip():
            return "salesforce_prior_uuid_present", ctx
    if not create_ui._clone_fresh(inputs.clone):
        return "clone_stale", ctx
    if inputs.launching is True or create_ui.LAUNCHES.busy():
        return "launch_in_progress", ctx
    return None, ctx


# --- recorded results -------------------------------------------------------------------------------------------------------------

def _stamp(record: Mapping[str, Any]) -> str:
    value = record.get("recorded_at")
    return value if isinstance(value, str) else ""


def _case3_results(parent: Path, co: str) -> list[dict[str, Any]]:
    try:
        history = create_ui.load_co_records(parent, co)
    except Exception:  # noqa: BLE001 - an unreadable record folder shows no result, never a pass
        return []
    return sorted((r for r in history if r.get("route") == CASE3_ROUTE and r.get("outcome") in RESULT_OUTCOMES), key=_stamp)


def result_since(parent: Path, co: str, since: str) -> dict[str, Any] | None:
    """The newest Case 3 dry-run result recorded at or after ``since`` (records of an earlier run never count)."""
    found = [r for r in _case3_results(parent, co) if _stamp(r) >= since]
    return found[-1] if found else None


def findings(observations: Any) -> list[str]:
    """Plain lines for the learn-only observations (names and short tokens only)."""
    if not isinstance(observations, Mapping):
        return []
    lines = []
    for key, label in OBSERVATION_LABELS:
        value = observations.get(key)
        if isinstance(value, str) and value:
            extra = ""
            if key == "max_scan_duration_control" and isinstance(observations.get("max_scan_duration_default"), str):
                extra = " (default " + observations["max_scan_duration_default"] + " h)"
            lines.append(label + ": " + value + extra)
    return lines


def _result_html(parent: Path, co: str) -> str:
    results = _case3_results(parent, co)
    if not results:
        return "<p class='note'>No Case 3 dry run has been recorded for this CO.</p>"
    last = results[-1]
    passed = last.get("outcome") == production_create.OUTCOME_DRY_RUN
    when = _stamp(last)[:16].replace("T", " ") + " UTC" if _stamp(last) else ""
    head = ("Last dry run (" + when + "): " + ("passed. Nothing was created." if passed else "stopped: "
            + create_ui.gate_text(last.get("refusal_reason") or "refused") + " (" + str(last.get("refusal_reason") or "refused") + ")"))
    lines = findings(last.get("observations"))
    return ("<p class='note'>" + escape(head) + "</p>"
            + ("<ul class='note'>" + "".join("<li>" + escape(line) + "</li>" for line in lines) + "</ul>" if lines else ""))


# --- progress ---------------------------------------------------------------------------------------------------------------------

def progress_view(events: Any, record: Mapping[str, Any] | None, *, running: bool) -> dict[str, Any]:
    """``{"steps", "percent", "current", "done", "failed"}`` from the recorded milestones and the recorded result only."""
    result, success = None, False
    if isinstance(record, Mapping) and record.get("outcome") in RESULT_OUTCOMES:
        success = record["outcome"] == production_create.OUTCOME_DRY_RUN
        result = "dry_run_passed" if success else str(record.get("refusal_reason") or "refused")
    elif not running:
        result = "runner_ended_without_a_record"
    progress = run_progress.build_progress(run_progress.PRODUCTION_DRY_RUN_ROUTE, events, result, success)
    return {"steps": [(step.label, step.state) for step in progress.steps], "percent": progress.percent,
            "current": progress.current, "done": progress.status == "done",
            "failed": result if progress.status == "failed" else None}


def progress_card_html(view: Mapping[str, Any]) -> str:
    items = "".join("<li class='s-" + escape(state) + "'>" + escape(label) + (" (running)" if state == "active" else "") + "</li>"
                    for label, state in view["steps"])
    if view.get("done"):
        chip, head = create_ui._chip("chip-ok", "Dry run passed"), "Done"
    elif view.get("failed"):
        chip, head = create_ui._chip("chip-bad", "Stopped"), "Stopped: " + create_ui.gate_text(view["failed"])
    else:
        chip, head = create_ui._chip("chip-warn", "Running"), "In progress"
    percent = str(int(view.get("percent") or 0))
    return ("<div class='card-head'><h2 id='co-dry-run-title' class='pill'>Production dry run (Case 3)</h2>" + chip + "</div>"
            "<div class='runprog' aria-live='polite'><div class='runprog-head'><span>" + escape(head) + ": "
            + escape(str(view.get("current") or "")) + "</span><span>" + percent + "%</span></div>"
            "<div class='runprog-bar' role='progressbar' aria-label='Production dry run progress' aria-valuemin='0' "
            "aria-valuemax='100' aria-valuenow='" + percent + "'><span class='runprog-fill' style='width:" + percent
            + "%'></span></div><ol class='runprog-steps'>" + items + "</ol></div>")


# --- the card ---------------------------------------------------------------------------------------------------------------------

def _card(body: str) -> str:
    return "<section class='card' aria-labelledby='co-dry-run-title'>" + body + "</section>"


def _head(chip: str) -> str:
    return "<div class='card-head'><h2 id='co-dry-run-title' class='pill'>Production dry run (Case 3)</h2>" + chip + "</div>"


def card_html(inputs: create_ui.Inputs, run: Mapping[str, Any] | None = None) -> str:
    """The dry-run section of a Case 3 CO page; "" for any other CO and on the VM. ``run`` (a run this dashboard started):
    ``{"since", "running", "events"}``. A control exists only when the dry-run constant is True AND every gate passes."""
    if inputs.vm is True or route_for_row(inputs.row) != CASE3_ROUTE:
        return ""
    reference = inputs.reference
    lock = build_lock(inputs.vm)
    if lock is not None:
        return _card(_head(create_ui._chip("chip-warn", "Locked")) + "<p class='note'>" + escape(text(lock)) + "</p>")
    shown = ""
    if run is not None:
        since = str(run.get("since") or "")
        view = progress_view(run.get("events") or [], result_since(inputs.records_parent, reference, since),
                             running=bool(run.get("running")))
        if run.get("running"):
            return _card(progress_card_html(view))
        shown = progress_card_html(view) + _result_html(inputs.records_parent, reference)
    try:
        reason, ctx = gate(inputs)
    except Exception:  # noqa: BLE001 - fail closed
        reason, ctx = "gate_unreadable", {}
    if reason is not None:
        return _card((shown or _head(create_ui._chip("chip-warn", "Blocked")) + _result_html(inputs.records_parent, reference))
                     + "<p class='note'>" + escape(text(reason)) + "</p>")
    revision = str(inputs.row["LastModifiedDate"])
    token = create_ui.TOKENS.issue("dry_run", reference, create_ui._binding(reference, revision, ctx["approval"]["approval_ref"],
                                                                           "dry_run"))
    return _card((shown or _head(create_ui._chip("chip-info", "Ready")) + _result_html(inputs.records_parent, reference))
                 + "<p class='note'>Every gate passed. This opens the real production BackOffice sign-in (SSO and MFA in the "
                   "app.pentera.io automation window), fills the Add Account form, reads it back and cancels it. Nothing is "
                   "submitted or created, Salesforce is not changed and the approval is not used up.</p>"
                   "<form class='start-form' method='post' action='" + DRY_RUN_PATH + "'>"
                   "<input type='hidden' name='reference' value='" + escape(reference) + "'>"
                   "<input type='hidden' name='nonce' value='" + escape(token) + "'>"
                   "<div class='confirm'><label><input type='checkbox' name='production_dry_run_authorized' value='1' required> "
                   "I will sign in with SSO and MFA, and I authorize ONE production dry run for this CO</label></div>"
                   "<button type='submit'>Run production dry run</button></form>")


# --- POST -------------------------------------------------------------------------------------------------------------------------

def handle_post(values: Mapping[str, Any], inputs: create_ui.Inputs,
                launch: Callable[[str, str, str, dict[str, str]], Any]) -> str:
    """"launched" or the refusal code. Every gate is re-evaluated with the facts of THIS request; one attempt per token.

    ``launch(reference, revision, route, env)`` starts the runner and returns its process handle (None on failure); it is
    called at most once, inside the single-run registry, and never after any refusal."""
    reference = create_ui._form(values, "reference")
    if reference != inputs.reference or not isinstance(reference, str) or not create_ui.CO_PATTERN.fullmatch(reference):
        return "invalid_request"
    token = create_ui._form(values, "nonce")
    reason, ctx = gate(inputs)
    if reason is not None:
        create_ui.TOKENS.consume("dry_run", reference, "", token)  # a refused attempt also spends the token
        return reason
    revision = str(inputs.row["LastModifiedDate"])
    binding = create_ui._binding(reference, revision, ctx["approval"]["approval_ref"], "dry_run")
    if not create_ui.TOKENS.consume("dry_run", reference, binding, token):
        return "confirmation_invalid"
    if create_ui._form(values, "production_dry_run_authorized") != "1":
        return "authorization_missing"
    started = create_ui.LAUNCHES.claim(lambda: launch(reference, revision, CASE3_ROUTE, ctx["env"]))
    return "launched" if started else "launch_failed"


def now_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(_STAMP)
