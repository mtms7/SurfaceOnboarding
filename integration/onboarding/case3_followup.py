"""Guarded Case 3 follow-up after a verified create (path B step d; owner go 2026-10-22).

Two Salesforce writes on ONE Customer_Onboarding__c, behind a review step and a separate owner confirmation, mirroring the CE-only UUID
write (``production_create_ui.handle_uuid_review`` / ``handle_uuid_confirm``) but never sharing its CE-only gates:

1. the empty ``Surface_Account_ID__c`` / ``Account_UUID__c`` from the newest verified Case 3 readback (plan and fixed argument list in
   ``salesforce_case3_ids_writeback``; a different existing value is a conflict, nothing is overwritten);
2. ``Request Approved -> Account Scanning``, only after (1) read back as verified. This is the ONLY Case 3 stage move; later stages stay
   manual. The CE-only stage plan still refuses Case 3.

Every write: fresh read, identical plan twice, an intent line on disk BEFORE the write, ONE fixed-argv write, read-after-write, a result
line. A failed, rejected or uncertain step is never retried by this module. Both production lock constants must be True; the VM is read
only. Records never hold the full IDs (last four characters only). Nothing here runs a process: the caller passes ``read_row`` and
``run_argv``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import production_create_record as records
from . import salesforce_case3_ids_writeback as ids_wb
from . import salesforce_stage_writeback as stage_wb
from . import session_readiness as readiness
from .production_create_ui import OneTimeTokens

WRITES_FILE = "case3-followup-writes.jsonl"
STAGE_FROM, STAGE_TO = "Request Approved", "Account Scanning"
VERIFIED_OUTCOME = "readback_verified"
TOKENS = OneTimeTokens()
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


@dataclass(frozen=True)
class FollowupInputs:
    reference: str
    records_parent: Path
    now: datetime
    vm: bool
    sf_ready: bool


def _binding(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _both_constants() -> bool:
    return readiness.PRODUCTION_RUNNER_SUPPORTED is True and readiness.PRODUCTION_DRY_RUN_SUPPORTED is True


def stage_plan(co_row: Any) -> stage_wb.StagePlan:
    """The one Case 3 stage move: ready only from exactly ``Request Approved`` on the Case 3 New route."""
    def blocked(reason: str) -> stage_wb.StagePlan:
        return stage_wb.StagePlan("blocked", reason, "", STAGE_TO)

    if not isinstance(co_row, Mapping) or stage_wb.STAGE_FIELD not in co_row or not isinstance(co_row.get("Id"), str):
        return blocked("row_unreadable")
    if (co_row.get("Onboarding_Product__c") != ids_wb.CASE3_PRODUCT or co_row.get("Onboarding_Type__c") != ids_wb.CASE3_TYPE):
        return blocked("route_not_case3_new")
    current = co_row.get(stage_wb.STAGE_FIELD)
    if current == STAGE_TO:
        return stage_wb.StagePlan("already_present", "already_present", STAGE_TO, STAGE_TO)
    if current != STAGE_FROM:
        return blocked("stage_not_expected")
    return stage_wb.StagePlan("ready_to_write", "", STAGE_FROM, STAGE_TO)


# --- append-only result lines (no full IDs) -----------------------------------------------------------------------------

def _record(parent: Path, co: str, step: str, result: str, now: datetime, detail: Mapping[str, str] | None = None) -> None:
    folder = Path(parent) / records.RECORDS_SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({"co_reference": co, "step": step, "result": result, **(detail or {}),
                       "recorded_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, sort_keys=True)
    fd = os.open(folder / WRITES_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _last(parent: Path, co: str, step: str) -> str | None:
    """Newest result of one step of one CO; ``write_uncertain`` for an unreadable log; None when there is none."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / WRITES_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        return "write_uncertain"
    last = None
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            return "write_uncertain"
        if isinstance(item, dict) and item.get("co_reference") == co and item.get("step") == step \
                and isinstance(item.get("result"), str):
            last = item["result"]
    return last


# --- the shared gate ------------------------------------------------------------------------------------------------------

def _newest_verified(verified_records: Any, co: str) -> dict[str, Any] | None:
    found = []
    for item in verified_records if isinstance(verified_records, Sequence) and not isinstance(verified_records, str) else ():
        stamp = item.get("recorded_at") if isinstance(item, Mapping) else None
        if (isinstance(item, Mapping) and item.get("outcome") == VERIFIED_OUTCOME and item.get("route") == ids_wb.CASE3_ROUTE
                and item.get("co_reference") == co and isinstance(stamp, str) and _STAMP.fullmatch(stamp)):
            found.append(item)
    if not found:
        return None
    best = max(found, key=lambda item: item["recorded_at"])
    return {"account_id": best.get("account_id"), "account_uuid": best.get("account_uuid")}


def build_lock(inputs: FollowupInputs) -> str | None:
    """The CO-independent first refusals (the VM, either lock constant), checked BEFORE any Salesforce read; else None."""
    if inputs.vm is True:
        return "vm_read_only"
    if not _both_constants():
        return ("production_runner_not_supported" if readiness.PRODUCTION_RUNNER_SUPPORTED is not True
                else "production_dry_run_not_supported")
    return None


def _blocker(inputs: FollowupInputs, row: Any, verified_records: Any) -> tuple[str | None, dict[str, Any], str]:
    """(refusal code or None, the verified ids, the CO revision). Pure apart from reading the result log."""
    lock = build_lock(inputs)
    if lock is not None:
        return lock, {}, ""
    if not isinstance(row, Mapping) or inputs.sf_ready is not True:
        return "salesforce_session_not_ready", {}, ""
    evidence = _newest_verified(verified_records, inputs.reference)
    if evidence is None:
        return "tenant_not_verified", {}, ""
    revision = row.get("LastModifiedDate")
    if not isinstance(revision, str) or not revision:
        return "revision_changed", {}, ""
    plan = ids_wb.plan("production", ids_wb.CASE3_ROUTE, row, evidence, revision, revision, True)
    if plan.status in ("blocked", "conflict"):
        return plan.reason, {}, ""
    moved = stage_plan(row)
    if moved.status == "blocked":
        return moved.reason, {}, ""
    if plan.status == "already_present" and moved.status == "already_present":
        return "already_complete", {}, ""
    if plan.status == "ready_to_write":
        last = _last(inputs.records_parent, inputs.reference, "ids")
        if last in ("write_started", "write_uncertain"):
            return "ids_write_needs_review", {}, ""
        if last is not None:
            return "ids_write_already_recorded", {}, ""
    if moved.status == "ready_to_write" and _last(inputs.records_parent, inputs.reference, "stage") not in (
            None, "written_verified"):
        return "stage_write_needs_review", {}, ""  # a rejected, started or uncertain stage write is never repeated
    return None, evidence, revision


def _ids_binding(reference: str, revision: str, evidence: Mapping[str, Any]) -> str:
    return _binding(reference, revision, str(evidence.get("account_id")), str(evidence.get("account_uuid")), "case3_followup")


def review(inputs: FollowupInputs, verified_records: Any, row: Any, *, tokens: Any = None) -> tuple[str, dict[str, Any]]:
    """("ready", view) with the confirmation view (token, fields, last four characters, stages) or (refusal code, {}). Writes nothing."""
    code, evidence, revision = _blocker(inputs, row, verified_records)
    if code is not None:
        return code, {}
    token = (tokens or TOKENS).issue("case3_followup", inputs.reference, _ids_binding(inputs.reference, revision, evidence))
    return "ready", {"token": token, "fields": [ids_wb.ID_FIELD, ids_wb.UUID_FIELD], "revision": revision,
                     "id_tail": str(evidence["account_id"])[-4:], "uuid_tail": str(evidence["account_uuid"])[-4:],
                     "stage_from": STAGE_FROM, "stage_to": STAGE_TO}


def _refused(reason: str) -> dict[str, str]:
    return {"result": "refused", "reason": reason}


def _form(values: Mapping[str, Any], key: str) -> Any:
    value = values.get(key) if isinstance(values, Mapping) else None
    return value[0] if isinstance(value, (list, tuple)) and value else value


def confirm(values: Mapping[str, Any], inputs: FollowupInputs, verified_records: Any,
            read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool], target_org: str,
            *, tokens: Any = None) -> dict[str, str]:
    """The owner's confirmation: ``{"result": "refused", "reason"}`` (nothing sent) or ``{"result": "completed"|"stopped", "ids",
    "stage"}``. Never retries; a recorded write blocks any further attempt of the same step."""
    tokens = tokens or TOKENS
    reference = _form(values, "reference")
    if not isinstance(reference, str) or reference != inputs.reference:
        return _refused("invalid_request")
    if _form(values, "confirm_reference") != reference:
        return _refused("typed_reference_mismatch")
    if _form(values, "case3_followup_authorized") != "1":
        return _refused("authorization_missing")
    lock = build_lock(inputs)
    if lock is not None:
        tokens.consume("case3_followup", reference, "", _form(values, "nonce"))
        return _refused(lock)  # before any Salesforce read
    try:
        row = read_row()
    except Exception:
        row = None
    token = _form(values, "nonce")
    code, evidence, revision = _blocker(inputs, row, verified_records)
    if code is not None:
        tokens.consume("case3_followup", reference, "", token)
        return _refused(code)
    if not tokens.consume("case3_followup", reference, _ids_binding(reference, revision, evidence), token):
        return _refused("confirmation_invalid")
    ids = _write_ids(inputs, read_row, run_argv, target_org, verified_records, evidence, revision)
    if ids not in ("written_verified", "already_present", "write_rejected", "write_uncertain"):
        return _refused(ids)  # a gate or record problem before the write: nothing was sent
    if ids not in ("written_verified", "already_present"):
        return {"result": "stopped", "ids": ids, "stage": "skipped_ids_not_verified"}
    stage = _write_stage(inputs, read_row, run_argv, target_org)
    done = stage in ("written_verified", "already_present")
    return {"result": "completed" if done else "stopped", "ids": ids, "stage": stage}


def _write_ids(inputs: FollowupInputs, read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool],
               target_org: str, verified_records: Any, evidence: Mapping[str, Any], revision: str) -> str:
    try:
        again = read_row()
    except Exception:
        again = None
    code, evidence_again, revision_again = _blocker(inputs, again, verified_records)
    if code is not None and code != "already_complete":
        return code
    if code is None and (evidence_again != evidence or revision_again != revision):
        return "revision_changed"
    if code == "already_complete":
        return "already_present"
    plan = ids_wb.plan("production", ids_wb.CASE3_ROUTE, again, evidence, revision, revision, True)
    if plan.status == "already_present":
        return "already_present"
    try:
        argv = ids_wb.update_argv(plan, (again or {}).get("Id"), target_org)
    except ValueError:
        return "write_blocked"
    detail = {"fields": ",".join(sorted(plan.values)), "id_tail": str(evidence["account_id"])[-4:],
              "uuid_tail": str(evidence["account_uuid"])[-4:]}
    try:
        _record(inputs.records_parent, inputs.reference, "ids", "write_started", inputs.now, detail)
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
        outcome = "written_verified" if ids_wb.verify_after_write(after, evidence) == "verified" else "write_uncertain"
    try:
        _record(inputs.records_parent, inputs.reference, "ids", outcome, inputs.now, detail)
    except Exception:
        return "record_write_failed"
    return outcome


def _write_stage(inputs: FollowupInputs, read_row: Callable[[], Mapping[str, Any] | None],
                 run_argv: Callable[[list[str]], bool], target_org: str) -> str:
    if _last(inputs.records_parent, inputs.reference, "stage") not in (None, "written_verified"):
        return "stage_write_needs_review"
    try:
        row = read_row()
    except Exception:
        row = None
    plan = stage_plan(row)
    if plan.status == "already_present":
        return "already_present"
    if plan.status != "ready_to_write":
        return plan.reason
    try:
        argv = stage_wb.update_argv(plan, (row or {}).get("Id"), target_org)
    except ValueError:
        return "write_blocked"
    try:
        _record(inputs.records_parent, inputs.reference, "stage", "write_started", inputs.now, {"to_stage": STAGE_TO})
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
        outcome = "written_verified" if stage_wb.verify_after_write(after, STAGE_TO) == "verified" else "write_uncertain"
    try:
        _record(inputs.records_parent, inputs.reference, "stage", outcome, inputs.now, {"to_stage": STAGE_TO})
    except Exception:
        return "record_write_failed"
    return outcome


# --- the card and the progress bar (recorded evidence only) -----------------------------------------------------------------

FOLLOWUP_TEXT = {
    "ids_uncertain": "The Account ID / UUID write may or may not have been applied: check the CO in Salesforce. It is not repeated.",
    "ids_rejected": "Salesforce refused the Account ID / UUID update. Nothing changed; it is not repeated by this page.",
    "stage_uncertain": "The stage move may or may not have been applied: check the CO in Salesforce. It is not repeated.",
    "stage_rejected": "Salesforce refused the stage change. The IDs are written; the stage stays as it was.",
    "record_failed": "A write result could not be recorded: check the CO in Salesforce; further writes stay blocked.",
}


MANUAL_AFTER_TEXT = ("Still manual in BackOffice after the create: Edit Account, set Maximum scan Duration (hours) to 90 (the create "
                     "does not keep it: CO-0800 and CO-0802 both showed 24), then pick the Operator Account of the Technical Advisor.")


def followup_card_html(parent: Path, co: str) -> str:
    """The Case 3 follow-up part of the create card after a verified create: one review button, or the recorded state."""
    from html import escape

    ids, stage = _last(parent, co, "ids"), _last(parent, co, "stage")
    manual = "<p class='note'>" + escape(MANUAL_AFTER_TEXT) + "</p>"
    if ids == "written_verified" and stage == "written_verified":
        return ("<p class='note'><strong>Onboarded:</strong> the Account ID and UUID are written to Salesforce and read back, "
                "and the stage is Account Scanning.</p>" + manual)
    for result, text in ((ids, "ids"), (stage, "stage")):
        if result in ("write_started", "write_uncertain"):
            return "<p class='note'>" + escape(FOLLOWUP_TEXT[text + "_uncertain"]) + "</p>"
        if result == "write_rejected":
            return "<p class='note'>" + escape(FOLLOWUP_TEXT[text + "_rejected"]) + "</p>"
        if result == "record_write_failed":
            return "<p class='note'>" + escape(FOLLOWUP_TEXT["record_failed"]) + "</p>"
    from .production_create_ui import FOLLOWUP_REVIEW_PATH

    return (manual + "<form class='start-form' method='post' action='" + FOLLOWUP_REVIEW_PATH + "'>"
            "<input type='hidden' name='reference' value='" + escape(co) + "'>"
            "<button type='submit'>Review the Salesforce write</button></form>")


PROGRESS_STEPS = (
    "Signing in to BackOffice and checking duplicates and the form (sign in with SSO + MFA in the app.pentera.io window)",
    "Creating the tenant in BackOffice (one Confirm)",
    "Reading the tenant back",
    "Your review: writing the Account ID and UUID to Salesforce",
    "Your review: moving the Salesforce stage to Account Scanning",
)


def progress(parent: Path, co: str, since: str, *, running: bool) -> dict[str, Any]:
    """Steps of the Case 3 create started at ``since`` from recorded evidence only (records and the follow-up log).

    Same shape as ``production_create_ui.production_progress``. A verified create waits for the owner's review (active, not
    failed): nothing is written to Salesforce automatically on this route.
    """
    from .production_create_ui import load_co_records

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
        ids, stage = _last(parent, co, "ids"), _last(parent, co, "stage")
        if ids in ("written_verified", None) and stage in ("written_verified", None):
            reached = 3 + (ids == "written_verified") + (stage == "written_verified")
        else:
            failed = next(r for r in (ids, stage) if r not in ("written_verified", None))
            reached = 3 + (ids == "written_verified")
    elif any(o in ("created_unverified", "uncertain_submit") for o in outcomes):
        reached, failed = 2, "create_not_verified"
    elif refusal is not None:
        failed = str(refusal.get("refusal_reason") or "refused")
    elif not running and reached == 0:
        failed = "runner_ended_without_a_record"
    steps = []
    for index, label in enumerate(PROGRESS_STEPS):
        state = "done" if index < reached else ("failed" if failed else "active") if index == reached else "pending"
        steps.append((label, state))
    done = reached == len(PROGRESS_STEPS)
    current = ("Onboarded: tenant created and verified, IDs written, stage Account Scanning" if done
               else PROGRESS_STEPS[reached] if reached < len(PROGRESS_STEPS) else "")
    return {"steps": steps, "percent": 100 if done else min(95, 5 + reached * 18), "current": current, "done": done,
            "failed": failed}
