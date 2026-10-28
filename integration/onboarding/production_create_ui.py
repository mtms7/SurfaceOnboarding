"""Dashboard side of the guarded production create and the guarded UUID write (docs/53, docs/45 D1/D3).

Pure and injectable: this module never starts a process, reads Salesforce, opens a browser or imports the dashboard.
The dashboard passes plain values and callables. Everything that could show or start something is unreachable while
either hard-coded constant (``session_readiness.PRODUCTION_RUNNER_SUPPORTED`` / ``PRODUCTION_DRY_RUN_SUPPORTED``) is
not exactly True:

* ``create_card_html`` returns the EMPTY string for a CO with no record and a read-only status paragraph (no form, no
  button, no input) for a CO with records; a control exists only when both constants are True AND every gate passes.
* ``handle_create_post`` / ``handle_uuid_post`` re-evaluate every gate at the moment of the POST and refuse first.

Gates for "Create in production" (first refusal wins, all re-judged at POST): desktop only (never the VM); both
constants True; session unlock for the approval; the operator is the approval issuer (Milton only); a valid unused
one-CO approval bound to the CURRENT Salesforce revision; CE-only New route; Salesforce data fresh and the Salesforce
session ready; no Account UUID yet; Redash clone fresh (< 6 h); a passing dry run recorded for this CO and approval
within 24 h; no earlier attempt waiting for the owner's review; no run already launched. One create click, no retry:
the one-time confirmation token is consumed by the first POST, whatever its outcome.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

from . import production_approval, production_create, production_launch_ticket, production_views, salesforce_uuid_writeback
from . import salesforce_case3_ids_writeback as case3_ids
from . import production_create_record as records
from . import salesforce_stage_writeback as stage_wb
from . import session_readiness as readiness

CREATE_PATH = "/attended/production-create"
UUID_REVIEW_PATH = "/attended/production-uuid-review"
UUID_CONFIRM_PATH = "/attended/production-uuid-confirm"
SCAN_CHECK_PATH = "/attended/production-scan-check"  # build 3: the owner's read-only CE scan check
FOLLOWUP_REVIEW_PATH = "/attended/production-case3-followup-review"  # Case 3 (path B step d): the owner's review ...
FOLLOWUP_CONFIRM_PATH = "/attended/production-case3-followup-confirm"  # ... and confirmation of the ID write and stage move
POST_PATHS = frozenset({CREATE_PATH, UUID_REVIEW_PATH, UUID_CONFIRM_PATH, SCAN_CHECK_PATH, FOLLOWUP_REVIEW_PATH,
                        FOLLOWUP_CONFIRM_PATH})
TOKEN_TTL_SECONDS = 10 * 60
DRY_RUN_MAX_AGE = timedelta(hours=24)
SALESFORCE_FRESH_SECONDS = 15 * 60
SESSIONS_ENV = "SURFACE_ONBOARDING_PRODUCTION_SESSIONS"
UUID_WRITES_FILE = "production-uuid-writes.jsonl"
STAGE_WRITES_FILE = "production-stage-writes.jsonl"  # owner decision 2026-10-19: CE-only stage moves (append-only)
PINNED_SF_ALIAS = "surface-onboarding"  # production paths never take the Salesforce alias from the environment
CO_PATTERN = re.compile(r"CO-[0-9]{4,10}")

# UI result words (the record outcomes keep their existing names; this is how the page and the runbook name them).
CREATED_VERIFIED = "created_verified"
CREATED_MISMATCH = "created_mismatch"
CREATE_OUTCOME_UNKNOWN = "create_outcome_unknown"
DRY_RUN_PASSED = "dry_run_passed"
NO_ATTEMPT = "no_attempt"
_MISMATCH_REASONS = frozenset({"readback_name_mismatch", "readback_domains_mismatch", "readback_account_id_missing",
                               "readback_account_id_mismatch", "readback_uuid_invalid", "readback_contract_mismatch",
                               "readback_payload_mismatch"})
STATUS_TEXT = {
    CREATED_VERIFIED: ("chip-ok", "Created and verified",
                       "The tenant exists and the readback matches the contract. Next: write the Account UUID to Salesforce."),
    CREATED_MISMATCH: ("chip-bad", "Created, readback differs: stop",
                       "A tenant exists but the readback differs from the contract. Do not create again. The owner reviews "
                       "the record and decides an edit in BackOffice."),
    CREATE_OUTCOME_UNKNOWN: ("chip-bad", "Create outcome unknown: do not retry",
                             "The click may or may not have created a tenant. This CO stays blocked until the owner has "
                             "checked BackOffice and reviewed the record."),
    DRY_RUN_PASSED: ("chip-info", "Dry run passed", "A production dry run passed. Nothing was created."),
}

# Reason code -> one plain sentence for the page (never free text from an exception).
GATE_TEXT = {
    "no_verified_create": "There is no verified production create for this CO, so there is nothing to check.",
    "uuid_not_written": "The Account UUID is not written to Salesforce yet; the CE scan check comes after it.",
    "stage_not_account_scanning": "The Salesforce stage is not Account Scanning (moved by this dashboard), so no scan check runs.",
    "onboarding_completed": "This CO is already Onboarding Completed.",
    "scan_check_running": "A CE scan check is already running.",
    "stage_not_expected": "The Salesforce stage was not the expected one, so it was left unchanged.",
    "stage_write_needs_review": "An earlier stage change has no confirmed result; check Salesforce by hand before anything else.",
    "vm_read_only": "The VM dashboard is read-only guidance; create runs from the owner's Windows desktop.",
    "production_runner_not_supported": "The production runner is locked in this build.",
    "production_dry_run_not_supported": "The production dry run is locked in this build.",
    "production_locked": "Production sessions are not unlocked on this desktop.",
    "operator_not_owner": "Only the owner who issued the approval can create.",
    "approval_missing": "No create approval is recorded for this CO.",
    "approval_expired": "The create approval is expired.",
    "approval_consumed": "The create approval was already used.",
    "approval_superseded": "The create approval was superseded.",
    "approval_unreadable": "The approval record cannot be read.",
    "approval_revision_drift": "The CO changed in Salesforce since the approval: issue a new approval.",
    "route_not_ce_only_new": "This CO is not a CE-only New onboarding.",
    "salesforce_not_fresh": "The Salesforce data is not fresh: refresh the page.",
    "salesforce_session_not_ready": "The Salesforce session is not ready: sign in again.",
    "salesforce_prior_uuid_present": "The CO already has an Account UUID.",
    "clone_stale": "The Redash clone is older than 6 hours or unusable: refresh it.",
    "dry_run_required": "No passing production dry run is recorded for this CO and approval.",
    "dry_run_old": "The passing dry run is older than 24 hours: run it again.",
    "prior_attempt_needs_review": "An earlier create attempt on this CO waits for the owner's review.",
    "records_unreadable": "The create records cannot be read: nothing is treated as clear.",
    "launch_in_progress": "A production create run is already in progress.",
    "confirmation_invalid": "The confirmation was missing, expired or already used. Review the page again.",
    "typed_reference_mismatch": "The typed CO reference does not match.",
    "authorization_missing": "The explicit authorization box was not ticked.",
    "launch_failed": "The runner could not be started. Nothing was created.",
    "uuid_write_needs_review": "An earlier UUID write is uncertain: check Salesforce, then record your review.",
    "uuid_write_already_recorded": "A UUID write is already recorded for this CO.",
    "gate_unreadable": "The create gate could not be evaluated: nothing is allowed.",
    "tenant_name_not_confirmed": "The tenant name is not confirmed: confirm the short name on the CO page first (long account names).",
    "approval_tenant_name_drift": "The tenant name differs from the one the approval was issued for: issue a new approval.",
    "approval_tenant_name_missing": "This approval has no tenant name bound to it: issue a new approval.",
    "surface_account_id_present": "The CO already has a Surface Account ID (or it could not be read).",
    "approval_route_mismatch": "The approval is for another route than this CO.",
    "tenant_not_verified": "There is no verified production create of this CO to take the IDs from.",
    "tenant_ids_invalid": "The verified tenant's Account ID or UUID is not in the expected format.",
    "different_value": "The CO already holds a different value in one of these fields: nothing is overwritten.",
    "ids_write_needs_review": "An earlier ID write is uncertain: check Salesforce by hand before anything else.",
    "ids_write_already_recorded": "An ID write is already recorded for this CO and is not repeated.",
    "already_complete": "The IDs and the stage are already in Salesforce.",
    "record_write_failed": "The write result could not be recorded: check the CO in Salesforce; further writes stay blocked.",
    "route_not_case3_new": "This CO is not a Surface & Credential Exposure (Case 3) New onboarding.",
    "co_row_unreadable": "The CO row could not be read completely: nothing is written.",
    "revision_changed": "The CO changed in Salesforce since it was read: review the page again.",
    "write_blocked": "The Salesforce write could not be prepared: nothing was sent.",
}


UUID_RESULT_TEXT = {
    "written_verified": "The Account UUID is written to Salesforce and was read back.",
    "write_uncertain": "The Salesforce write may or may not have been applied: check the CO in Salesforce. It is not retried.",
    "write_rejected": "Salesforce refused the update. Nothing changed.",
    "record_write_failed": "The write result could not be recorded. Check the CO in Salesforce; further writes stay blocked until you review.",
}


def gate_text(code: object) -> str:
    base = str(code).split(":", 1)[0]
    return GATE_TEXT.get(base, "Not allowed: " + base.replace("_", " ") + ".")


# --- Status of a CO from its records --------------------------------------------------------------------------------------

def ui_status(outcome: object, reason: object = "") -> str | None:
    """The UI word of one record outcome (None for refused and unknown outcomes)."""
    if outcome == production_create.OUTCOME_VERIFIED:
        return CREATED_VERIFIED
    if outcome == production_create.OUTCOME_CREATED_UNVERIFIED:
        return CREATED_MISMATCH if reason in _MISMATCH_REASONS else CREATE_OUTCOME_UNKNOWN
    if outcome in (production_create.OUTCOME_UNCERTAIN, production_create.OUTCOME_ATTEMPT_STARTED):
        return CREATE_OUTCOME_UNKNOWN
    if outcome == production_create.OUTCOME_DRY_RUN:
        return DRY_RUN_PASSED
    return None


def load_co_records(parent: Path, co: str) -> list[dict[str, Any]]:
    """Every validated record of one CO, oldest first. Raises on any unreadable record (callers fail closed)."""
    return [records.load(path) for path in records.list_records(parent, co)]


def _key(record: dict[str, Any]) -> tuple[Any, Any]:
    return record.get("approval_ref"), record.get("salesforce_revision")


def cleared_attempts(history: list[dict[str, Any]]) -> set[tuple[Any, Any]]:
    """(approval ref, Salesforce revision) pairs of attempts the OWNER cleared by a recorded review (history is one CO).

    Only an attempt that provably sent nothing can be cleared: every ``attempt_started`` of the pair is matched by a
    ``confirm_not_clicked`` refusal AND an ``owner_review_cleared`` record (counts, not mere presence, so a later crashed
    attempt under a reset or reused reference is never hidden), and no unknown, mismatched or verified outcome shares the pair.
    """
    started: dict[tuple[Any, Any], int] = {}
    refused: dict[tuple[Any, Any], int] = {}
    owner: dict[tuple[Any, Any], int] = {}
    others: set[tuple[Any, Any]] = set()
    for r in history:
        key, outcome = _key(r), r.get("outcome")
        if outcome == production_create.OUTCOME_ATTEMPT_STARTED:
            started[key] = started.get(key, 0) + 1
        elif outcome == production_create.OUTCOME_REFUSED and r.get("refusal_reason") == "confirm_not_clicked":
            refused[key] = refused.get(key, 0) + 1
        elif outcome == production_create.OUTCOME_OWNER_CLEARED:
            owner[key] = owner.get(key, 0) + 1
        elif outcome in production_create._NEEDS_REVIEW:
            others.add(key)
    return {k for k, n in started.items() if refused.get(k, 0) >= n and owner.get(k, 0) >= n and k not in others}


def active_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records that still count: a cleared attempt's ``attempt_started`` records no longer block."""
    cleared = cleared_attempts(history)
    return [r for r in history if not (r.get("outcome") == production_create.OUTCOME_ATTEMPT_STARTED
                                       and _key(r) in cleared)]


def review_cleared(history: list[dict[str, Any]]) -> bool:
    """True when something needed review and EVERY such record is an owner-cleared ``confirm_not_clicked`` attempt."""
    needs = [r for r in history if r.get("outcome") in production_create._NEEDS_REVIEW]
    left = [r for r in active_history(history) if r.get("outcome") in production_create._NEEDS_REVIEW]
    return bool(needs) and not left


def co_status(history: list[dict[str, Any]]) -> dict[str, Any]:
    """``{"code", "blocking", "uuid"}`` for one CO. A needs-review outcome anywhere blocks; the most final outcome of the attempt wins."""
    history = active_history(history)
    review = [r for r in history if r.get("outcome") in production_create._NEEDS_REVIEW]
    if review:
        # Rank, not file order: records written in the same second can sort either way, and any later outcome of an
        # attempt supersedes its first ``attempt_started`` record (a second attempt is blocked, so ranks never mix).
        rank = {production_create.OUTCOME_ATTEMPT_STARTED: 0, production_create.OUTCOME_UNCERTAIN: 1,
                production_create.OUTCOME_CREATED_UNVERIFIED: 2, production_create.OUTCOME_VERIFIED: 3}
        last = max(review, key=lambda r: rank.get(r.get("outcome"), 0))
        code = ui_status(last.get("outcome"), last.get("refusal_reason")) or CREATE_OUTCOME_UNKNOWN
        return {"code": code, "blocking": True, "uuid": last.get("account_uuid") if code == CREATED_VERIFIED else None,
                "revision": last.get("salesforce_revision"), "route": last.get("route")}
    if any(r.get("outcome") == production_create.OUTCOME_DRY_RUN for r in history):
        return {"code": DRY_RUN_PASSED, "blocking": False, "uuid": None, "revision": None, "route": None}
    return {"code": NO_ATTEMPT, "blocking": False, "uuid": None, "revision": None, "route": None}


def dry_run_problem(history: list[dict[str, Any]], approval_ref: str, now: datetime) -> str | None:
    """None when a dry run passed for this approval within 24 h of ``now``."""
    stamps = []
    for item in history:
        if item.get("outcome") == production_create.OUTCOME_DRY_RUN and item.get("approval_ref") == approval_ref:
            try:
                stamps.append(datetime.strptime(item["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
            except (KeyError, ValueError, TypeError):
                continue
    if not stamps:
        return "dry_run_required"
    age = now - max(stamps)
    return None if timedelta(0) <= age <= DRY_RUN_MAX_AGE else "dry_run_old"


# --- One-time confirmation tokens ------------------------------------------------------------------------------------------

def _binding(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


class OneTimeTokens:
    """Server-held single-use tokens bound to one CO + revision + approval (+ kind). In memory; lost on restart."""

    def __init__(self, ttl: float = TOKEN_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl, self._clock = ttl, clock
        self._items: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, kind: str, co: str, binding: str) -> str:
        """The active token for the same binding (so several tabs agree), else a new one replacing any older."""
        now = self._clock()
        with self._lock:
            current = self._items.get((kind, co))
            if current is not None and current[0] == binding and current[2] > now:
                return current[1]
            token = secrets.token_urlsafe(24)
            self._items[(kind, co)] = (binding, token, now + self._ttl)
            return token

    def consume(self, kind: str, co: str, binding: str, token: object) -> bool:
        """True for exactly one matching, unexpired token. Any attempt (right or wrong) discards the stored one."""
        with self._lock:
            current = self._items.pop((kind, co), None)
        return bool(current and isinstance(token, str) and current[0] == binding and current[2] > self._clock()
                    and hmac.compare_digest(current[1], token))


class LaunchRegistry:
    """At most one production run process at a time (in this dashboard process)."""

    def __init__(self) -> None:
        self._handle: Any = None
        self._lock = threading.Lock()

    def busy(self) -> bool:
        with self._lock:
            handle = self._handle
            if handle is None:
                return False
            try:
                if handle.poll() is None:
                    return True
            except Exception:
                return True  # an unreadable handle is treated as still running (fail closed)
            self._handle = None
            return False

    def claim(self, start: Callable[[], Any]) -> bool:
        """Start once under the lock; False when a run is active or ``start`` returned nothing."""
        with self._lock:
            handle = self._handle
            if handle is not None:
                try:
                    if handle.poll() is None:
                        return False
                except Exception:
                    return False
            started = start()
            self._handle = started
            return started is not None


TOKENS = OneTimeTokens()
LAUNCHES = LaunchRegistry()


# --- Gate ------------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Inputs:
    """Plain facts the dashboard gathers once per page or POST."""
    reference: str
    row: Mapping[str, Any] | None
    now: datetime
    environ: Mapping[str, str]
    operator: str | None
    vm: bool
    approvals_path: Path
    records_parent: Path
    clone: Mapping[str, Any] | None
    sf_ready: bool
    row_fresh: bool
    launching: bool = False
    tenant_names_path: Path | None = None  # the confirmed short tenant names (docs/60); None = none confirmed
    case3_tenant_name: str | None = None   # Case 3 only: the exact tenant name the create would type (confirmed), else None


def _owner_ok(operator: str | None, record: Mapping[str, Any]) -> bool:
    issuer = record.get("issued_by")
    return (isinstance(operator, str) and bool(operator.strip()) and isinstance(issuer, str)
            and operator.strip().casefold() == issuer.strip().casefold())


def _both_constants() -> bool:
    return readiness.PRODUCTION_RUNNER_SUPPORTED is True and readiness.PRODUCTION_DRY_RUN_SUPPORTED is True


def locked() -> bool:
    """True unless BOTH hard-coded constants are exactly True. Everything in this module keys off this."""
    return not _both_constants()


BUILD_LOCK_CODES = ("vm_read_only", "production_runner_not_supported", "production_dry_run_not_supported")


def build_lock(vm: bool) -> str | None:
    """The CO-independent first refusals of ``gate`` (this server and this build), else None. Pages without a CO use it."""
    if vm is True:
        return "vm_read_only"
    if readiness.PRODUCTION_RUNNER_SUPPORTED is not True:
        return "production_runner_not_supported"
    if readiness.PRODUCTION_DRY_RUN_SUPPORTED is not True:
        return "production_dry_run_not_supported"
    return None


def _clone_fresh(clone: Mapping[str, Any] | None) -> bool:
    """Fresh by the query's run time (< 6 h) and, when the status carries it, by the age of the cloned data (docs/56 step 4)."""
    if not isinstance(clone, Mapping) or clone.get("state") != "fresh":
        return False
    age = clone.get("age_seconds")
    if type(age) is not int or age < 0 or age > production_create.CLONE_MAX_AGE.total_seconds():
        return False
    if "data_age_seconds" in clone:
        data_age = clone["data_age_seconds"]
        return type(data_age) is int and 0 <= data_age <= production_views.DATA_LIMIT_SECONDS
    return True


def _row_route(row: Mapping[str, Any] | None) -> str | None:
    """The approvable route of a CO row (CE-only New or Case 3 New), else None."""
    if not isinstance(row, Mapping):
        return None
    pair = (row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    if pair == (salesforce_uuid_writeback.CE_ROUTE_PRODUCT, salesforce_uuid_writeback.CE_ROUTE_TYPE):
        return production_approval.ALLOWED_ROUTE
    if pair == (case3_ids.CASE3_PRODUCT, case3_ids.CASE3_TYPE):
        return production_approval.CASE3_ROUTE
    return None


def _create_binding(reference: str, revision: str, approval_ref: str, ctx: Mapping[str, Any]) -> str:
    """The one-time create token binding; a Case 3 token is also bound to the exact tenant name."""
    parts = [reference, revision, approval_ref, "create"]
    if ctx.get("route") == production_approval.CASE3_ROUTE:
        parts.append(str(ctx.get("tenant_name")))
    return _binding(*parts)


def gate(inputs: Inputs) -> tuple[str | None, dict[str, Any]]:
    """(first refusal code or None, context). Context: ``approval`` (the record), ``status`` (co_status), ``env``, ``route``
    (CE-only or Case 3) and ``tenant_name`` (Case 3 only)."""
    ctx: dict[str, Any] = {"approval": None, "status": None, "env": None, "route": None, "tenant_name": None}
    lock = build_lock(inputs.vm)
    if lock is not None:
        return lock, ctx
    if not isinstance(inputs.row, Mapping) or not CO_PATTERN.fullmatch(inputs.reference):
        return "salesforce_not_fresh", ctx
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
    ctx["approval"] = record
    if not _owner_ok(inputs.operator, record):
        return "operator_not_owner", ctx
    env = {**inputs.environ, **production_approval.unlock_environ(record)}
    ctx["env"] = env
    if env.get(SESSIONS_ENV) != "1" or readiness.production_unlock_problem(env, inputs.now) is not None:
        return "production_locked", ctx
    revision = inputs.row.get("LastModifiedDate")
    if not isinstance(revision, str) or not revision or revision != record.get("source_revision"):
        return "approval_revision_drift", ctx
    route = _row_route(inputs.row)
    if route is None:
        return "route_not_ce_only_new", ctx
    ctx["route"] = route
    case3 = route == production_approval.CASE3_ROUTE
    name_args: dict[str, Any] = {}
    if case3:
        name = inputs.case3_tenant_name
        if not production_approval.valid_tenant_name(name):
            return "tenant_name_not_confirmed", ctx
        ctx["tenant_name"] = name
        name_args = {"tenant_name": name}  # the approval must be bound to exactly this name
    problem = production_approval.check(inputs.approvals_path, record["approval_ref"], inputs.reference, route, revision,
                                        inputs.now, **name_args)
    if problem is not None:
        return problem, ctx
    if inputs.row_fresh is not True:
        return "salesforce_not_fresh", ctx
    if inputs.sf_ready is not True:
        return "salesforce_session_not_ready", ctx
    if "Account_UUID__c" not in inputs.row or str(inputs.row.get("Account_UUID__c") or "").strip():
        return "salesforce_prior_uuid_present", ctx
    if case3 and ("Surface_Account_ID__c" not in inputs.row or str(inputs.row.get("Surface_Account_ID__c") or "").strip()):
        return "surface_account_id_present", ctx
    if not _clone_fresh(inputs.clone):
        return "clone_stale", ctx
    try:
        history = load_co_records(inputs.records_parent, inputs.reference)
    except Exception:
        return "records_unreadable", ctx
    status = co_status(history)
    ctx["status"] = status
    # No dry-run gate (owner decision 2026-10-23: the dry run is not part of a CO's onboarding; the one-CO approval, the
    # fresh Salesforce read and the readback after the create are the gates).
    if status["blocking"]:
        return "prior_attempt_needs_review", ctx
    if inputs.launching is True or LAUNCHES.busy():
        return "launch_in_progress", ctx
    return None, ctx


def page_state(inputs: Inputs) -> dict[str, Any]:
    """The ONE create-gate result every panel of a production CO page shows (owner feedback 2026-10-18).

    ``{"phase": "ready" | "blocked" | "locked", "reason": code | None, "text": plain sentence}``. ``locked`` is the build or
    server lock (``BUILD_LOCK_CODES``); ``blocked`` names the first closed gate of an unlocked build. The session unlock is
    judged inside ``gate`` with the approval record's own values, never from the process environment alone. A gate that
    cannot be evaluated is ``blocked`` (fail closed) and never shows exception text.
    """
    try:
        reason, _ctx = gate(inputs)
    except Exception:  # noqa: BLE001 - fail closed
        reason = "gate_unreadable"
    if reason is None:
        return {"phase": "ready", "reason": None,
                "text": "Every gate passed: the create runs from the Create in production card on this page."}
    return {"phase": "locked" if reason in BUILD_LOCK_CODES else "blocked", "reason": reason, "text": gate_text(reason)}


def locked_card_html(state: Mapping[str, Any]) -> str:
    """Read-only create section for a CO whose create card is empty (no record and a closed gate): chip and the reason."""
    label = "Locked" if state.get("phase") == "locked" else "Blocked"
    return ("<section class='card' aria-labelledby='co-create-title'><div class='card-head'>"
            "<h2 id='co-create-title' class='pill'>Production create</h2>" + _chip("chip-warn", label) + "</div>"
            "<p class='note'>" + escape(str(state.get("text") or gate_text("gate_unreadable"))) + "</p></section>")


# --- HTML -------------------------------------------------------------------------------------------------------------------------

def _chip(css: str, text: str) -> str:
    return "<span class='chip " + css + "'>" + escape(text) + "</span>"


CASE3_VERIFIED_NOTE = ("The tenant exists and the readback matches the contract (Case 3). The Account ID and Account UUID are "
                       "written to Salesforce only by your review and confirmation on this page (or already entered by hand).")


def _status_html(code: str, route: object = None) -> str:
    css, label, note = STATUS_TEXT[code]
    if code == CREATED_VERIFIED and route == production_approval.CASE3_ROUTE:
        note = CASE3_VERIFIED_NOTE  # the CE-only text tells to write the UUID, which is not this route's next step
    return ("<div class='card-head'><h2 id='co-create-title' class='pill'>Production create</h2>" + _chip(css, label)
            + "</div><p class='note'>" + escape(note) + "</p>")


def _onboard_form(reference: str, token: str, issue_approval: bool) -> str:
    """The ONE button of a production CO page: no typed reference, no checkbox (owner decision 2026-10-23)."""
    return ("<p class='note'>The CO is Approved in Salesforce. One click checks everything, creates ONE tenant in BackOffice "
            "production (SSO and MFA in the app.pentera.io window), reads it back and records it in Salesforce. There is no retry.</p>"
            "<form class='start-form' method='post' action='" + CREATE_PATH + "'>"
            "<input type='hidden' name='reference' value='" + escape(reference) + "'>"
            "<input type='hidden' name='nonce' value='" + escape(token) + "'>"
            + ("<input type='hidden' name='issue_approval' value='1'>" if issue_approval else "")
            + "<button type='submit'>Onboard in production</button></form>")


def create_card_html(inputs: Inputs, uuid_write_state: str | None = None) -> str:
    """The create section of the production CO page.

    While either constant is not True: the empty string when the CO has no record, otherwise one read-only status
    section (no form, no button, no input, no link). Unlocked: the status, the refusal of the first failing gate, or the
    confirmation form when every gate passes (and the UUID write form after a verified create).
    """
    reference = inputs.reference
    try:
        history = load_co_records(inputs.records_parent, reference) if CO_PATTERN.fullmatch(reference) else []
    except Exception:
        history = None
    status = co_status(history) if history is not None else None
    status_html = ""
    if status is not None and status["code"] in STATUS_TEXT and status["code"] != DRY_RUN_PASSED:
        status_html = _status_html(status["code"], status.get("route"))
    summary = onboarded_summary(inputs.records_parent, reference) if status is not None and status["code"] == CREATED_VERIFIED else None
    if summary is not None and summary["label"] != "Created: UUID not written":
        status_html = ("<div class='card-head'><h2 id='co-create-title' class='pill'>Production create</h2>"
                       + _chip(summary["css"], summary["label"]) + "</div><p class='note'>Created and verified in BackOffice. "
                       + escape(summary["text"]) + ".</p>")
    if locked():
        return ("<section class='card' aria-labelledby='co-create-title'>" + status_html + "</section>") if status_html else ""
    if inputs.vm is True:
        return ""
    reason, ctx = gate(inputs)
    if reason is None:
        approval = ctx["approval"]
        revision = str(inputs.row["LastModifiedDate"])
        token = TOKENS.issue("create", reference, _create_binding(reference, revision, approval["approval_ref"], ctx))
        route_note = ""
        if ctx.get("route") == production_approval.CASE3_ROUTE:
            route_note = ("<p class='note'>Route: Surface &amp; Credential Exposure (Case 3). The tenant is created with the name "
                          "<code>" + escape(str(ctx.get("tenant_name"))) + "</code>, the one this approval is bound to. After a verified "
                          "create nothing is written to Salesforce until you review and confirm it on this page.</p>")
        return ("<section class='card' aria-labelledby='co-create-title'><div class='card-head'>"
                "<h2 id='co-create-title' class='pill'>Create in production</h2>" + _chip("chip-warn", "Production")
                + "</div>" + route_note + _onboard_form(reference, token, False) + "</section>")
    if reason == "approval_missing" and isinstance(inputs.row, Mapping) and inputs.row.get("Onboarding_Approval_Status__c") == "Approved":
        revision = str(inputs.row.get("LastModifiedDate") or "")
        token = TOKENS.issue("create", reference, _binding(reference, revision, "no-approval", "create"))
        return ("<section class='card' aria-labelledby='co-create-title'><div class='card-head'>"
                "<h2 id='co-create-title' class='pill'>Onboard in production</h2>" + _chip("chip-warn", "Production")
                + "</div>" + _onboard_form(reference, token, True) + "</section>")
    if status is not None and status["code"] == CREATED_VERIFIED and _row_route(inputs.row) == production_approval.CASE3_ROUTE:
        from . import case3_followup  # lazy: case3_followup imports this module
        return ("<section class='card' aria-labelledby='co-create-title'><div class='card-head'>"
                "<h2 id='co-create-title' class='pill'>Production create</h2>" + _chip("chip-ok", "Created and verified")
                + "</div><p class='note'>The tenant exists and the readback matches the contract (Case 3). Next: review and "
                  "confirm the Salesforce write of the Account ID and Account UUID, then the stage move to Account Scanning."
                  "</p>" + case3_followup.followup_card_html(inputs.records_parent, reference) + "</section>")
    if status is not None and status["code"] == CREATED_VERIFIED:
        if uuid_write_state == "written_verified":
            status_html = ("<div class='card-head'><h2 id='co-create-title' class='pill'>Production create</h2>"
                           + _chip("chip-ok", "Onboarded") + "</div><p class='note'>Created and verified in BackOffice; the "
                           "Account UUID is written to Salesforce and read back.</p>")
            return "<section class='card' aria-labelledby='co-create-title'>" + status_html + "</section>"
        return ("<section class='card' aria-labelledby='co-create-title'>" + status_html + _uuid_form_html(inputs, status, uuid_write_state)
                + "</section>")
    blocked = ("<section class='card' aria-labelledby='co-create-title'>"
               + (status_html or "<div class='card-head'><h2 id='co-create-title' class='pill'>Production create</h2>"
                  + _chip("chip-warn", "Blocked") + "</div>")
               + "<p class='note'>" + escape(gate_text(reason)) + "</p></section>")
    return blocked


# --- UUID write (decision D3): its own confirmation step ----------------------------------------------------------------

def _uuid_form_html(inputs: Inputs, status: Mapping[str, Any], state: str | None) -> str:
    if state in ("written_verified", "write_uncertain", "write_rejected", "write_started"):
        state = "write_uncertain" if state == "write_started" else state
        return "<p class='note'>" + escape(UUID_RESULT_TEXT[state]) + "</p>"
    if salesforce_uuid_writeback.normalise_uuid(status.get("uuid")) is None:
        return ""
    return ("<form class='start-form' method='post' action='" + UUID_REVIEW_PATH + "'>"
            "<input type='hidden' name='reference' value='" + escape(inputs.reference) + "'>"
            "<button type='submit'>Write Account UUID to Salesforce</button></form>"
            "<p class='note'>Opens a separate confirmation. It writes only <code>Account_UUID__c</code> on this CO.</p>")


def _uuid_lines(parent: Path, co: str) -> list[dict[str, Any]] | None:
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / UUID_WRITES_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        return None
    items = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            return None  # an unreadable log is never "no write"
        if isinstance(item, dict) and item.get("co_reference") == co and isinstance(item.get("result"), str):
            items.append(item)
    return items


def last_uuid_write(parent: Path, co: str) -> str | None:
    """The newest UUID write state of one CO: ``write_started`` / ``written_verified`` / ``write_uncertain`` /
    ``write_rejected``, ``write_uncertain`` for an unreadable log, or None (never written, or cleared by the owner's
    recorded review after an uncertain write)."""
    items = _uuid_lines(parent, co)
    if items is None:
        return "write_uncertain"
    state = None
    for item in items:
        state = None if item["result"] == "owner_review_cleared" else item["result"]
    return state


def record_uuid_write(parent: Path, co: str, uuid: str, result: str, now: datetime, operator: str = "",
                      reason: str = "") -> None:
    """Append one line (never rewritten). The UUID itself is not stored: only its SHA-256. Raises on any failure."""
    folder = Path(parent) / records.RECORDS_SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({"co_reference": co, "field": salesforce_uuid_writeback.WRITE_FIELD, "result": result,
                       "value_sha256": hashlib.sha256(uuid.encode("utf-8")).hexdigest(),
                       "operator": operator, "reason": reason,
                       "recorded_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, sort_keys=True)
    fd = os.open(folder / UUID_WRITES_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# --- POST handling ----------------------------------------------------------------------------------------------------------------

def _form(values: Mapping[str, Any], name: str) -> str | None:
    item = values.get(name)
    if isinstance(item, list):
        item = item[0] if len(item) == 1 else None
    return item if isinstance(item, str) else None


def remote_start_values(inputs: Inputs) -> tuple[dict[str, str] | None, str]:
    """The form values the page's ONE button would post, built server-side for a PC-side remote start (owner decision 2026-10-27,
    docs/62): ``(values, "")`` or ``(None, refusal code)``. It issues the same single-use token the page would, for the same facts;
    ``handle_create_post`` then re-evaluates EVERY gate. Never on the VM, never while the build is locked."""
    if inputs.vm is True:
        return None, "vm_read_only"
    lock = build_lock(False)
    if lock is not None:
        return None, lock
    reference = inputs.reference
    reason, ctx = gate(inputs)
    row = inputs.row if isinstance(inputs.row, Mapping) else {}
    revision = str(row.get("LastModifiedDate") or "")
    if reason is None:
        token = TOKENS.issue("create", reference, _create_binding(reference, revision, ctx["approval"]["approval_ref"], ctx))
        return {"reference": reference, "nonce": token}, ""
    if reason == "approval_missing" and row.get("Onboarding_Approval_Status__c") == "Approved":
        token = TOKENS.issue("create", reference, _binding(reference, revision, "no-approval", "create"))
        return {"reference": reference, "nonce": token, "issue_approval": "1"}, ""
    return None, reason


def handle_create_post(values: Mapping[str, Any], inputs: Inputs, launch: Callable[[str, str, dict[str, str]], Any],
                       issue: Callable[[str, str, str], bool] | None = None) -> str:
    """"launched" or the refusal code. Re-evaluates every gate with the facts of THIS request; one attempt per token.

    ``launch(reference, revision, env)`` starts the runner and returns its process handle (None on failure); it is called
    at most once, inside the single-run registry, and never after any refusal. ``issue(reference, revision, route)``
    records the one-CO approval when none exists (the single "Onboard in production" button); it is called only for an
    Approved CO whose only open gate is the missing approval.
    """
    reference = _form(values, "reference")
    if reference != inputs.reference or not isinstance(reference, str) or not CO_PATTERN.fullmatch(reference):
        return "invalid_request"
    reason, ctx = gate(inputs)
    token = _form(values, "nonce")
    if (reason == "approval_missing" and _form(values, "issue_approval") == "1" and issue is not None
            and isinstance(inputs.row, Mapping) and inputs.row.get("Onboarding_Approval_Status__c") == "Approved"):
        revision = str(inputs.row.get("LastModifiedDate") or "")
        if not TOKENS.consume("create", reference, _binding(reference, revision, "no-approval", "create"), token):
            return "confirmation_invalid"
        route = _row_route(inputs.row)
        if route is None or not issue(reference, revision, route):
            return "approval_not_issued"
        reason, ctx = gate(inputs)
        if reason is not None:
            return reason
        token = TOKENS.issue("create", reference, _create_binding(reference, revision, ctx["approval"]["approval_ref"], ctx))
    elif reason is not None:
        TOKENS.consume("create", reference, "", token)  # a refused attempt also spends the token
        return reason
    approval = ctx["approval"]
    revision = str(inputs.row["LastModifiedDate"])
    if not TOKENS.consume("create", reference, _create_binding(reference, revision, approval["approval_ref"], ctx), token):
        return "confirmation_invalid"
    def start() -> Any:
        try:  # the runner accepts ONLY this file-backed one-time ticket, never a flag or an environment value
            root = production_launch_ticket.ticket_root()  # the SAME helper the runner uses; never the repository
            production_launch_ticket.mint(root, reference, revision, approval["approval_ref"], str(inputs.operator), inputs.now)
        except (OSError, ValueError, production_launch_ticket.TicketRootError):
            return None
        # CE-only keeps its three-argument call; a Case 3 launch also names the route (the runner needs it).
        handle = (launch(reference, revision, ctx["env"], route=ctx["route"])
                  if ctx.get("route") == production_approval.CASE3_ROUTE else launch(reference, revision, ctx["env"]))
        if handle is None:
            production_launch_ticket.discard(root, reference)  # a failed launch leaves no usable ticket
        return handle
    started = LAUNCHES.claim(start)
    return "launched" if started else "launch_failed"


def handle_uuid_review(values: Mapping[str, Any], inputs: Inputs, wb_row: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """(code, view): ``"ready"`` with the confirmation view (token, field, last four characters) or a refusal code."""
    reference = _form(values, "reference")
    if reference != inputs.reference or not isinstance(reference, str):
        return "invalid_request", {}
    blocked = _uuid_blocker(inputs, wb_row)
    if blocked[0] is not None:
        return blocked[0], {}
    uuid, revision = blocked[1], blocked[2]
    token = TOKENS.issue("uuid", reference, _binding(reference, revision, uuid, "uuid"))
    return "ready", {"token": token, "field": salesforce_uuid_writeback.WRITE_FIELD, "tail": uuid[-4:], "revision": revision}


def _uuid_blocker(inputs: Inputs, wb_row: Mapping[str, Any] | None) -> tuple[str | None, str, str]:
    if inputs.vm is True:
        return "vm_read_only", "", ""
    if not _both_constants():
        return ("production_runner_not_supported" if readiness.PRODUCTION_RUNNER_SUPPORTED is not True
                else "production_dry_run_not_supported"), "", ""
    if not isinstance(wb_row, Mapping) or inputs.sf_ready is not True:
        return "salesforce_session_not_ready", "", ""
    try:
        history = load_co_records(inputs.records_parent, inputs.reference)
    except Exception:
        return "records_unreadable", "", ""
    status = co_status(history)
    if status["code"] != CREATED_VERIFIED:
        return "readback_not_verified", "", ""
    # Only the NEWEST needs-review record counts: a later attempt would have replaced it.
    uuid = salesforce_uuid_writeback.normalise_uuid(status.get("uuid"))
    if uuid is None:
        return "uuid_invalid", "", ""
    prior = last_uuid_write(inputs.records_parent, inputs.reference)
    if prior in ("write_uncertain", "write_started"):
        return "uuid_write_needs_review", "", ""
    if prior is not None:
        return "uuid_write_already_recorded", "", ""
    revision = wb_row.get("LastModifiedDate")
    if not isinstance(revision, str) or not revision:
        return "revision_changed", "", ""
    result = production_create.CreateResult(production_create.OUTCOME_VERIFIED, "", None, None, uuid,
                                            status.get("revision"), status.get("route"))
    plan = production_create.writeback_plan_after_verified(result, dict(wb_row), revision, True)
    if plan.status != "ready_to_write":
        return plan.reason if plan.status == "blocked" else "uuid_" + plan.status, "", ""
    return None, uuid, revision


def handle_uuid_confirm(values: Mapping[str, Any], inputs: Inputs, read_row: Callable[[], Mapping[str, Any] | None],
                        run_argv: Callable[[list[str]], bool], target_org: str) -> str:
    """"written_verified" / "write_rejected" / "write_uncertain" or a refusal code (nothing was sent).

    Fresh read, plan with the explicit go, ONE fixed-argv write, read-after-write verification, a recorded result. A
    result other than a clean rejection is never retried: a recorded write blocks any further attempt.
    """
    reference = _form(values, "reference")
    if reference != inputs.reference or not isinstance(reference, str):
        return "invalid_request"
    try:
        row = read_row()
    except Exception:
        row = None
    blocked = _uuid_blocker(inputs, row)
    token = _form(values, "nonce")
    if blocked[0] is not None:
        TOKENS.consume("uuid", reference, "", token)
        return blocked[0]
    uuid, revision = blocked[1], blocked[2]
    if not TOKENS.consume("uuid", reference, _binding(reference, revision, uuid, "uuid"), token):
        return "confirmation_invalid"
    return _write_uuid(inputs, read_row, run_argv, target_org, uuid, revision)


def _write_uuid(inputs: Inputs, read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool],
                target_org: str, uuid: str, revision: str) -> str:
    """The guarded write itself (after ``_uuid_blocker`` passed): re-read, identical plan, intent line, ONE write,
    read-after-write, result line. Shared by the owner's confirmation and the automatic CE-only follow-up."""
    reference = inputs.reference
    status = co_status(load_co_records(inputs.records_parent, reference))
    result = production_create.CreateResult(production_create.OUTCOME_VERIFIED, "", None, None, uuid,
                                            status.get("revision"), status.get("route"))
    # Narrow the plan-to-update gap: read once more; the plan must be identical (same revision, field still empty).
    try:
        again = read_row()
    except Exception:
        again = None
    recheck = _uuid_blocker(inputs, again)
    if recheck[0] is not None or recheck[1:] != (uuid, revision):
        return recheck[0] or "revision_changed"
    plan = production_create.writeback_plan_after_verified(result, dict(again or {}), revision, True)
    co_id = (again or {}).get("Id")
    try:
        argv = salesforce_uuid_writeback.update_argv(plan, co_id, target_org)
    except ValueError:
        return "write_blocked"
    # Intent line BEFORE the write: if it cannot be recorded nothing is sent; once written it blocks any second write.
    try:
        record_uuid_write(inputs.records_parent, reference, uuid, "write_started", inputs.now)
    except Exception:
        return "record_write_failed"
    try:
        accepted = run_argv(argv)
    except Exception:
        accepted = None
    if accepted is False:
        outcome = "write_rejected"
    elif accepted is not True:
        outcome = "write_uncertain"
    else:
        try:
            after = read_row()
        except Exception:
            after = None
        outcome = "written_verified" if salesforce_uuid_writeback.verify_after_write(after, uuid) == "verified" else "write_uncertain"
    try:
        record_uuid_write(inputs.records_parent, reference, uuid, outcome, inputs.now)
    except Exception:
        return "record_write_failed"  # the write_started line keeps blocking any further write
    return outcome


# --- CE-only follow-up after a verified create (owner decision 2026-10-19) --------------------------------------------------

def auto_uuid_write(inputs: Inputs, read_row: Callable[[], Mapping[str, Any] | None],
                    run_argv: Callable[[list[str]], bool], target_org: str) -> str:
    """The UUID write without a second click: the same ``_uuid_blocker`` gate (verified create, both constants, empty
    field, unchanged revision, no earlier write) and the same guarded write. Returns the result or the refusal code."""
    try:
        row = read_row()
    except Exception:
        row = None
    blocked = _uuid_blocker(inputs, row)
    if blocked[0] is not None:
        return blocked[0]
    return _write_uuid(inputs, read_row, run_argv, target_org, blocked[1], blocked[2])


def record_stage_write(parent: Path, co: str, to_stage: str, result: str, now: datetime, reason: str = "") -> None:
    """Append one stage-move line (never rewritten); raises on any failure."""
    folder = Path(parent) / records.RECORDS_SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({"co_reference": co, "field": stage_wb.STAGE_FIELD, "to_stage": to_stage, "result": result,
                       "reason": reason, "recorded_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                      sort_keys=True)
    fd = os.open(folder / STAGE_WRITES_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def last_stage_write(parent: Path, co: str) -> tuple[str, str] | None:
    """(to_stage, result) of the newest stage line of one CO; ("", "write_uncertain") for an unreadable log; None if none."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / STAGE_WRITES_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        return "", "write_uncertain"
    last = None
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            return "", "write_uncertain"
        if isinstance(item, dict) and item.get("co_reference") == co and isinstance(item.get("result"), str):
            last = (str(item.get("to_stage") or ""), item["result"])
    return last


def _stage_move(inputs: Inputs, read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool],
                target_org: str, to_stage: str) -> str:
    """One guarded stage move: never after an uncertain earlier move; exact from-stage; intent line; ONE write; read back."""
    if inputs.vm is True:
        return "vm_read_only"
    last = last_stage_write(inputs.records_parent, inputs.reference)
    if last is not None and last[1] in ("write_started", "write_uncertain"):
        return "stage_write_needs_review"
    try:
        row = read_row()
    except Exception:
        row = None
    plan = stage_wb.plan(row, to_stage)
    if plan.status == "already_present":
        return "already_present"
    if plan.status != "ready_to_write":
        return plan.reason
    try:
        argv = stage_wb.update_argv(plan, (row or {}).get("Id"), target_org)
    except ValueError:
        return "write_blocked"
    try:
        record_stage_write(inputs.records_parent, inputs.reference, to_stage, "write_started", inputs.now)
    except Exception:
        return "record_write_failed"
    try:
        accepted = run_argv(argv)
    except Exception:
        accepted = None
    if accepted is False:
        outcome = "write_rejected"
    elif accepted is not True:
        outcome = "write_uncertain"
    else:
        try:
            after = read_row()
        except Exception:
            after = None
        outcome = "written_verified" if stage_wb.verify_after_write(after, to_stage) == "verified" else "write_uncertain"
    try:
        record_stage_write(inputs.records_parent, inputs.reference, to_stage, outcome, inputs.now)
    except Exception:
        return "record_write_failed"
    return outcome


def _uuid_on_co(inputs: Inputs, row: Mapping[str, Any] | None) -> bool:
    """The verified readback UUID is the one on the CO (written_verified recorded and the row agrees)."""
    try:
        status = co_status(load_co_records(inputs.records_parent, inputs.reference))
    except Exception:
        return False
    uuid = salesforce_uuid_writeback.normalise_uuid(status.get("uuid")) if status["code"] == CREATED_VERIFIED else None
    return (uuid is not None and last_uuid_write(inputs.records_parent, inputs.reference) == "written_verified"
            and salesforce_uuid_writeback.verify_after_write(row, uuid) == "verified")


def after_verified_create(inputs: Inputs, read_row: Callable[[], Mapping[str, Any] | None],
                          run_argv: Callable[[list[str]], bool], target_org: str) -> dict[str, str]:
    """CE-only follow-up run by the dashboard when the create runner ends: the UUID write, then Request Approved ->
    Account Scanning only when the UUID on the CO is the verified one. Safe to call again: recorded results block repeats."""
    uuid = auto_uuid_write(inputs, read_row, run_argv, target_org)
    try:
        row = read_row()
    except Exception:
        row = None
    if not _uuid_on_co(inputs, row):
        return {"uuid": uuid, "stage": "skipped_uuid_not_verified"}
    return {"uuid": uuid, "stage": _stage_move(inputs, read_row, run_argv, target_org, "Account Scanning")}


def after_scan_finished(inputs: Inputs, read_row: Callable[[], Mapping[str, Any] | None],
                        run_argv: Callable[[list[str]], bool], target_org: str, *, scan_state: str | None) -> list[str]:
    """Account Scanning -> Scan Completed Successfully -> User Created -> Onboarding Completed (CE-only creates no user but
    passes through User Created: owner decision 2026-10-28), only for a ``scan_completed`` state from the runner's own
    read-only scan check or the clone, and only with the verified UUID on the CO. Stops at the first move that fails."""
    if scan_state != "scan_completed":
        return ["scan_not_finished"]
    try:
        row = read_row()
    except Exception:
        row = None
    if not _uuid_on_co(inputs, row):
        return ["uuid_not_on_co"]
    order = ("Account Scanning", "Scan Completed Successfully", "User Created", "Onboarding Completed")
    current = (row or {}).get(stage_wb.STAGE_FIELD)
    # Resume only from a stage THIS dashboard wrote and read back (e.g. left at User Created by a failed last move); a
    # stage changed by hand is left alone (the first move then refuses with stage_not_expected).
    resume = current if last_stage_write(inputs.records_parent, inputs.reference) == (current, "written_verified") else None
    results = []
    for to_stage in order[1:]:
        if resume in order and order.index(resume) >= order.index(to_stage):
            results.append("already_present")
            continue
        result = _stage_move(inputs, read_row, run_argv, target_org, to_stage)
        results.append(result)
        if result not in ("written_verified", "already_present"):
            break
    return results


# --- Automatic CE scan finish from the production clone (docs/56 step 3, owner decision 2026-10-19) --------------------------

AUTO_SCAN_WAITING = (("Account Scanning", "written_verified"), ("Scan Completed Successfully", "written_verified"),
                     ("User Created", "written_verified"))
AUTO_SCAN_TEXT = {
    "finished": "The clone shows the CE scan finished; the stage moves ran.",
    "finished_with_errors": "The clone shows the CE scan finished with errors; the stage moves ran.",
    "running": "The clone shows the CE scan still running.",
    "not_yet": "The clone shows no finished CE scan yet (it is a nightly snapshot); checked again later.",
    "unknown": "The clone shows a CE scan status this dashboard does not know; nothing moved.",
    "clone_row_unavailable": "The clone has no single readable row for this Account UUID; nothing moved.",
    "clone_stale": "The clone is stale or unusable; nothing moved.",
    "vm_read_only": "This is the VM; nothing moves here.",
    "no_verified_create": "No verified create and UUID write are recorded for this CO; nothing moved.",
    "create_time_unknown": "The create time of this CO is not recorded; nothing moved.",
    "follow_up_failed": "The automatic check failed; nothing moved.",
}


def auto_scan_candidates(parent: Path) -> list[str]:
    """COs whose newest stage line is a verified Account Scanning, Scan Completed Successfully or User Created (sorted);
    [] if unreadable."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / STAGE_WRITES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    newest: dict[str, tuple[str, str]] = {}
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            return []
        if isinstance(item, dict) and isinstance(item.get("co_reference"), str) and isinstance(item.get("result"), str):
            newest[item["co_reference"]] = (str(item.get("to_stage") or ""), item["result"])
    return sorted(co for co, last in newest.items() if last in AUTO_SCAN_WAITING and CO_PATTERN.fullmatch(co))


def _created_ms(history: list[dict[str, Any]]) -> int | None:
    """The create time recorded for this CO (its newest ``readback_verified``), epoch ms; None when absent or unreadable."""
    stamps = [r.get("recorded_at") for r in active_history(history) if r.get("outcome") == production_create.OUTCOME_VERIFIED]
    try:
        newest = max(datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ") for stamp in stamps)
    except (TypeError, ValueError):
        return None
    return int(newest.replace(tzinfo=timezone.utc).timestamp() * 1000)


def auto_scan_finish(inputs: Inputs, load_clone: Callable[[], Mapping[str, Any] | None],
                     read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool],
                     target_org: str) -> dict[str, Any]:
    """The background job for ONE CO: no browser, no login. ``{"scan": <state or refusal>, "stages": [...]}``.

    Acts only on a CO this dashboard moved to Account Scanning (the stage log's newest line), with its verified create and
    UUID write, on a desktop build with both constants True and a fresh clone. The clone row is matched by the verified
    Account UUID; only a finished CE scan that ended after the recorded create time runs ``after_scan_finished`` (the same
    guards as the BackOffice check). A stage move that is rejected or uncertain leaves the CO no candidate, so no retry."""
    from . import ce_clone_scan

    lock = build_lock(inputs.vm)
    if lock is not None:
        return {"scan": lock, "stages": []}
    if not _clone_fresh(inputs.clone):
        return {"scan": "clone_stale", "stages": []}
    if last_stage_write(inputs.records_parent, inputs.reference) not in AUTO_SCAN_WAITING:
        return {"scan": "not_a_candidate", "stages": []}
    try:
        history = load_co_records(inputs.records_parent, inputs.reference)
    except Exception:
        return {"scan": "records_unreadable", "stages": []}
    status = co_status(history)
    uuid = salesforce_uuid_writeback.normalise_uuid(status.get("uuid")) if status["code"] == CREATED_VERIFIED else None
    if uuid is None or last_uuid_write(inputs.records_parent, inputs.reference) != "written_verified":
        return {"scan": "no_verified_create", "stages": []}
    created = _created_ms(history)
    if created is None:
        return {"scan": "create_time_unknown", "stages": []}
    try:
        payload = load_clone()
    except Exception:
        payload = None
    v2 = ce_clone_scan.clone_v2_for_uuid(payload, uuid)
    if v2 is None:
        return {"scan": "clone_row_unavailable", "stages": []}
    state = ce_clone_scan.ce_scan_state(v2, created)
    if not ce_clone_scan.is_finished(state):
        return {"scan": state, "stages": []}
    return {"scan": state, "stages": after_scan_finished(inputs, read_row, run_argv, target_org, scan_state="scan_completed")}


SCAN_WORDS = {"finished": "finished", "finished_with_errors": "finished with errors", "running": "running",
              "not_yet": "no finished scan in the clone yet", "unknown": "status not recognised",
              "clone_row_unavailable": "this tenant is not in the clone yet (it is a nightly snapshot)"}


def onboarded_summary(parent: Path, co: str, clone_payload: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """What this dashboard recorded for a CO it created, or None when there is no verified create.

    ``{"label", "css", "text", "stage", "scan"}`` from the create record, the UUID-write log and the stage log; with the
    clone payload, also the CE scan state from the clone row (never guessed without it). A tenant created today is not in
    the nightly clone yet, so the clone is never what makes a CO "onboarded"."""
    from . import ce_clone_scan

    try:
        history = load_co_records(parent, co)
    except Exception:
        return None
    status = co_status(history)
    if status["code"] != CREATED_VERIFIED:
        return None
    if last_uuid_write(parent, co) != "written_verified":
        return {"label": "Created: UUID not written", "css": "chip-warn", "stage": None, "scan": None,
                "text": "Created and verified in BackOffice; the Account UUID is not written to Salesforce yet."}
    last = last_stage_write(parent, co)
    stage = last[0] if last is not None and last[1] == "written_verified" else None
    parts = ["Account UUID written and read back"]
    if stage:
        parts.append("Salesforce stage " + stage)
    scan = None
    if stage != "Onboarding Completed" and clone_payload is not None:
        uuid = salesforce_uuid_writeback.normalise_uuid(status.get("uuid"))
        created = _created_ms(history)
        v2 = ce_clone_scan.clone_v2_for_uuid(clone_payload, uuid) if uuid and created is not None else None
        scan = "clone_row_unavailable" if v2 is None else ce_clone_scan.ce_scan_state(v2, created)
        parts.append("CE scan (clone): " + SCAN_WORDS.get(scan, scan))
    done = stage == "Onboarding Completed"
    return {"label": "Onboarded: completed" if done else "Onboarded", "css": "chip-ok", "stage": stage, "scan": scan,
            "text": "; ".join(parts)}


# --- Same-page progress of one production create (owner request 2026-10-19) -------------------------------------------------

PROGRESS_STEPS = (
    "Signing in to BackOffice and checking duplicates and the form (sign in with SSO + MFA in the app.pentera.io window)",
    "Creating the tenant in BackOffice (one Confirm)",
    "Reading the tenant back",
    "Writing the Account UUID to Salesforce",
    "Moving the Salesforce stage to Account Scanning",
)
_FOLLOW_OK = ("written_verified", "already_present")


def production_progress(parent: Path, co: str, since: str, *, running: bool,
                        follow: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Steps of the create started at ``since`` from recorded evidence only (records, UUID and stage logs, follow-up).

    ``{"steps": [(label, done|active|pending|failed)], "percent", "current", "done", "failed": code | None}``. Records
    older than ``since`` (an earlier attempt or the dry run) never count. Nothing is simulated.
    """
    try:
        history = [r for r in load_co_records(parent, co) if str(r.get("recorded_at") or "") >= since]
    except Exception:
        history = []
    outcomes = [r.get("outcome") for r in history]
    reached, failed = 0, None
    refusal = next((r for r in reversed(history) if r.get("outcome") == "refused"), None)
    if "attempt_started" in outcomes:
        reached = 1
    if "readback_verified" in outcomes:
        reached = 3
        uuid_state = last_uuid_write(parent, co)
        if uuid_state == "written_verified":
            reached = 4
            stage = last_stage_write(parent, co)
            if stage == ("Account Scanning", "written_verified") or (follow or {}).get("stage") == "already_present":
                reached = 5
            elif stage is not None and stage[1] == "write_started" and running:
                pass  # the stage write is in flight in the follow-up: not a failure yet (CO-0811, 2026-10-23)
            elif stage is not None and stage[1] != "written_verified":
                failed = stage[1]
            elif follow is not None and follow.get("stage") not in _FOLLOW_OK:
                failed = str(follow.get("stage"))
        elif uuid_state in ("write_rejected", "write_uncertain", "write_started") and not running:
            failed = uuid_state if follow is not None or uuid_state != "write_started" else None
        elif follow is not None and follow.get("uuid") not in _FOLLOW_OK:
            failed = str(follow.get("uuid"))
    elif any(o in ("created_unverified", "uncertain_submit") for o in outcomes):
        reached, failed = 2, "create_not_verified"
    elif refusal is not None:
        failed = str(refusal.get("refusal_reason") or "refused")
    elif not running and reached == 0:
        failed = "runner_ended_without_a_record"
    steps = []
    for index, label in enumerate(PROGRESS_STEPS):
        if index < reached:
            state = "done"
        elif index == reached:
            state = "failed" if failed else "active"
        else:
            state = "pending"
        steps.append((label, state))
    done = reached == len(PROGRESS_STEPS)
    percent = 100 if done else min(95, 5 + reached * 18)
    current = ("Onboarded: tenant created and verified, Account UUID written, stage Account Scanning" if done
               else PROGRESS_STEPS[reached] if reached < len(PROGRESS_STEPS) else "")
    return {"steps": steps, "percent": percent, "current": current, "done": done, "failed": failed}


def progress_card_html(progress: Mapping[str, Any]) -> str:
    """CSS-only progress card (no script, no form, no value): step labels, percent and state only."""
    items = "".join("<li class='s-" + escape(state) + "'>" + escape(label) + (" (running)" if state == "active" else "")
                    + "</li>" for label, state in progress["steps"])
    if progress.get("done"):
        chip, head = _chip("chip-ok", "Onboarded"), "Done"
    elif progress.get("failed"):
        chip, head = _chip("chip-bad", "Stopped"), "Stopped: " + gate_text(progress["failed"])
    else:
        chip, head = _chip("chip-warn", "Running"), "In progress"
    percent = str(int(progress.get("percent") or 0))
    return ("<section class='card' aria-labelledby='co-create-title'><div class='card-head'>"
            "<h2 id='co-create-title' class='pill'>Production create</h2>" + chip + "</div>"
            "<div class='runprog' aria-live='polite'><div class='runprog-head'><span>" + escape(head) + ": "
            + escape(str(progress.get("current") or "")) + "</span><span>" + percent + "%</span></div>"
            "<div class='runprog-bar' role='progressbar' aria-label='Production create progress' aria-valuemin='0' "
            "aria-valuemax='100' aria-valuenow='" + percent + "'><span class='runprog-fill' style='width:" + percent
            + "%'></span></div><ol class='runprog-steps'>" + items + "</ol></div></section>")


# --- Build 3: read-only CE scan check and the last stage moves (owner decision 2026-10-19) ---------------------------------

SCAN_STATE_TEXT = {
    "scan_completed": "The CE scan is finished.",
    "unconfirmed": "Read the tenant's scan fields, but not its Duration Per Scan list; no stage moved.",
    "scan_running": "The CE scan (LEAKED_CREDENTIALS_DISCOVERY) is still running; check again later.",
    "no_ce_scan_yet": "No CE scan (LEAKED_CREDENTIALS_DISCOVERY) has run for this tenant yet; check again later.",
    "unrecognized": "The CE scan has a status this dashboard does not know; nothing moved. Check BackOffice by hand.",
    "executions_unavailable": "The Duration Per Scan list could not be read; nothing moved. Check again.",
    "row_not_found": "The tenant was not found by its Account UUID.",
    "row_ambiguous": "More than one row has this Account UUID; nothing moved.",
    "production_session_expired": "The BackOffice session expired; sign in again and re-check.",
    "read_unavailable": "BackOffice did not answer the read; re-check.",
    "reply_unexpected": "BackOffice answered with an unexpected shape; nothing moved.",
}


def scan_check_problem(parent: Path, co: str) -> str | None:
    """Why the owner cannot start a CE scan check for ``co`` now, else None (verified create, UUID written, Account Scanning)."""
    try:
        history = load_co_records(parent, co)
    except Exception:
        return "records_unreadable"
    if co_status(history)["code"] != CREATED_VERIFIED:
        return "no_verified_create"
    if last_uuid_write(parent, co) != "written_verified":
        return "uuid_not_written"
    stage = last_stage_write(parent, co)
    if stage == ("Onboarding Completed", "written_verified"):
        return "onboarding_completed"
    if stage is not None and stage[1] in ("write_started", "write_uncertain"):
        return "stage_write_needs_review"
    # No local stage line is fine (a stage moved by hand, e.g. CO-0798): the stage moves themselves re-read Salesforce and
    # move only from exactly Account Scanning.
    return None


def scan_card_html(parent: Path, co: str, *, running: bool, auto: Mapping[str, Any] | None = None) -> str:
    """The CE scan section of a created CO: the last recorded check and stage, and the owner's check button."""
    from . import production_scan_check as scan

    last = scan.latest(parent, co)
    stage = last_stage_write(parent, co)
    lines = ""
    if stage is not None:
        lines += "<p class='note'>Salesforce stage: <strong>" + escape(stage[0] or "unknown") + "</strong> (" + escape(stage[1]) + ")</p>"
    if last is not None:
        lines += ("<p class='note'>Last CE scan check " + escape(str(last.get("recorded_at", "")).replace("T", " "))
                  + ": " + escape(SCAN_STATE_TEXT.get(str(last.get("state")), str(last.get("state")))) + "</p>")
    if isinstance(auto, Mapping):
        lines += ("<p class='note'>Automatic clone check " + escape(str(auto.get("at", "")).replace("T", " ").replace("Z", ""))
                  + ": " + escape(AUTO_SCAN_TEXT.get(str(auto.get("scan")), str(auto.get("scan")))) + "</p>")
    problem = scan_check_problem(parent, co)
    if problem == "onboarding_completed":
        body = "<p class='note'>Onboarding Completed. Nothing else to do for this CO.</p>"
    elif running:
        body = "<p class='note'>A CE scan check is running: sign in with SSO and MFA in the app.pentera.io window.</p>"
    elif problem is None:
        body = ("<form class='start-form' method='post' action='" + SCAN_CHECK_PATH + "'>"
                "<input type='hidden' name='reference' value='" + escape(co) + "'>"
                "<button type='submit'>Check CE scan in BackOffice</button></form>"
                "<p class='note'>Read only: opens the BackOffice window (SSO and MFA), reads this tenant's scan fields and, "
                "only when the scan is finished, moves the stage to Scan Completed Successfully, User Created and then "
                "Onboarding Completed (CE-only creates no user; the stage still passes through User Created).</p>")
    else:
        body = "<p class='note'>" + escape(gate_text(problem)) + "</p>"
    return ("<section class='card' aria-labelledby='co-scan-title'><div class='card-head'><h2 id='co-scan-title' "
            "class='pill'>CE scan</h2></div>" + lines + body + "</section>")


def own_onboarding(parent: Path, co: str, row: Mapping[str, Any] | None) -> str | None:
    """The page headline when THIS dashboard onboarded the CO (verified create, UUID written and read back, and the CO
    holds that same UUID), else None: such a CO is onboarded, never "blocked: a tenant exists"."""
    try:
        history = load_co_records(parent, co)
    except Exception:
        return None
    status = co_status(history)
    uuid = salesforce_uuid_writeback.normalise_uuid(status.get("uuid")) if status["code"] == CREATED_VERIFIED else None
    if (uuid is None or last_uuid_write(parent, co) != "written_verified"
            or salesforce_uuid_writeback.verify_after_write(row, uuid) != "verified"):
        return None
    when = next((str(r.get("recorded_at")) for r in reversed(history) if r.get("outcome") == "readback_verified"), "")
    return ("Onboarded in production by this dashboard: tenant created and verified" + (" at " + when.replace("T", " ") if when else "")
            + "; Account UUID written to Salesforce and read back.")
