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
from . import production_create_record as records
from . import salesforce_stage_writeback as stage_wb
from . import session_readiness as readiness

CREATE_PATH = "/attended/production-create"
UUID_REVIEW_PATH = "/attended/production-uuid-review"
UUID_CONFIRM_PATH = "/attended/production-uuid-confirm"
POST_PATHS = frozenset({CREATE_PATH, UUID_REVIEW_PATH, UUID_CONFIRM_PATH})
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


def gate(inputs: Inputs) -> tuple[str | None, dict[str, Any]]:
    """(first refusal code or None, context). Context: ``approval`` (the record), ``status`` (co_status), ``env``."""
    ctx: dict[str, Any] = {"approval": None, "status": None, "env": None}
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
    problem = production_approval.check(inputs.approvals_path, record["approval_ref"], inputs.reference,
                                        production_approval.ALLOWED_ROUTE, revision, inputs.now)
    if problem is not None:
        return problem, ctx
    if (inputs.row.get("Onboarding_Product__c") != salesforce_uuid_writeback.CE_ROUTE_PRODUCT
            or inputs.row.get("Onboarding_Type__c") != salesforce_uuid_writeback.CE_ROUTE_TYPE):
        return "route_not_ce_only_new", ctx
    if inputs.row_fresh is not True:
        return "salesforce_not_fresh", ctx
    if inputs.sf_ready is not True:
        return "salesforce_session_not_ready", ctx
    if "Account_UUID__c" not in inputs.row or str(inputs.row.get("Account_UUID__c") or "").strip():
        return "salesforce_prior_uuid_present", ctx
    clone = inputs.clone
    age = clone.get("age_seconds") if isinstance(clone, Mapping) else None
    if (not isinstance(clone, Mapping) or clone.get("state") != "fresh" or type(age) is not int or age < 0
            or age > production_create.CLONE_MAX_AGE.total_seconds()):
        return "clone_stale", ctx
    try:
        history = load_co_records(inputs.records_parent, inputs.reference)
    except Exception:
        return "records_unreadable", ctx
    status = co_status(history)
    ctx["status"] = status
    problem = dry_run_problem(history, record["approval_ref"], inputs.now)
    if problem is not None:
        return problem, ctx
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


def _status_html(code: str) -> str:
    css, label, note = STATUS_TEXT[code]
    return ("<div class='card-head'><h2 id='co-create-title' class='pill'>Production create</h2>" + _chip(css, label)
            + "</div><p class='note'>" + escape(note) + "</p>")


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
        status_html = _status_html(status["code"])
    if locked():
        return ("<section class='card' aria-labelledby='co-create-title'>" + status_html + "</section>") if status_html else ""
    if inputs.vm is True:
        return ""
    reason, ctx = gate(inputs)
    if reason is None:
        approval = ctx["approval"]
        revision = str(inputs.row["LastModifiedDate"])
        token = TOKENS.issue("create", reference, _binding(reference, revision, approval["approval_ref"], "create"))
        return ("<section class='card' aria-labelledby='co-create-title'><div class='card-head'>"
                "<h2 id='co-create-title' class='pill'>Create in production</h2>" + _chip("chip-warn", "Production")
                + "</div><p class='note'>Every gate passed: fresh clone, fresh Salesforce data, a valid one-CO approval bound to "
                  "this revision and a passing dry run. One click creates ONE tenant in BackOffice production, checks it by "
                  "readback and consumes the approval. There is no retry.</p>"
                  "<form class='start-form' method='post' action='" + CREATE_PATH + "'>"
                  "<input type='hidden' name='reference' value='" + escape(reference) + "'>"
                  "<input type='hidden' name='nonce' value='" + escape(token) + "'>"
                  "<div class='confirm'><label>Type the CO reference <code>" + escape(reference) + "</code> to confirm "
                  "<input type='text' name='confirm_reference' autocomplete='off' required maxlength='16'></label></div>"
                  "<div class='confirm'><label><input type='checkbox' name='production_create_authorized' value='1' required> "
                  "I authorize ONE production create for this CO and approval</label></div>"
                  "<button type='submit'>Create in production</button></form></section>")
    if status is not None and status["code"] == CREATED_VERIFIED:
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


def handle_create_post(values: Mapping[str, Any], inputs: Inputs, launch: Callable[[str, str, dict[str, str]], Any]) -> str:
    """"launched" or the refusal code. Re-evaluates every gate with the facts of THIS request; one attempt per token.

    ``launch(reference, revision, env)`` starts the runner and returns its process handle (None on failure); it is called
    at most once, inside the single-run registry, and never after any refusal.
    """
    reference = _form(values, "reference")
    if reference != inputs.reference or not isinstance(reference, str) or not CO_PATTERN.fullmatch(reference):
        return "invalid_request"
    reason, ctx = gate(inputs)
    token = _form(values, "nonce")
    if reason is not None:
        TOKENS.consume("create", reference, "", token)  # a refused attempt also spends the token
        return reason
    approval = ctx["approval"]
    revision = str(inputs.row["LastModifiedDate"])
    if not TOKENS.consume("create", reference, _binding(reference, revision, approval["approval_ref"], "create"), token):
        return "confirmation_invalid"
    if _form(values, "confirm_reference") != reference:
        return "typed_reference_mismatch"
    if _form(values, "production_create_authorized") != "1":
        return "authorization_missing"
    def start() -> Any:
        try:  # the runner accepts ONLY this file-backed one-time ticket, never a flag or an environment value
            root = production_launch_ticket.ticket_root()  # the SAME helper the runner uses; never the repository
            production_launch_ticket.mint(root, reference, revision, approval["approval_ref"], str(inputs.operator), inputs.now)
        except (OSError, ValueError, production_launch_ticket.TicketRootError):
            return None
        handle = launch(reference, revision, ctx["env"])
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
    """Account Scanning -> Scan Completed Successfully -> Onboarding Completed (CE-only skips User Created), only for a
    ``scan_completed`` state from the runner's own read-only scan check and only with the verified UUID on the CO."""
    if scan_state != "scan_completed":
        return ["scan_not_finished"]
    try:
        row = read_row()
    except Exception:
        row = None
    if not _uuid_on_co(inputs, row):
        return ["uuid_not_on_co"]
    results = []
    for to_stage in ("Scan Completed Successfully", "Onboarding Completed"):
        result = _stage_move(inputs, read_row, run_argv, target_org, to_stage)
        results.append(result)
        if result not in ("written_verified", "already_present"):
            break
    return results


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
