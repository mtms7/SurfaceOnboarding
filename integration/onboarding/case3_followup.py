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

def _record(parent: Path, co: str, step: str, result: str, now: datetime, detail: Mapping[str, str] | None = None,
            file: str = WRITES_FILE) -> None:
    folder = Path(parent) / records.RECORDS_SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({"co_reference": co, "step": step, "result": result, **(detail or {}),
                       "recorded_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, sort_keys=True)
    fd = os.open(folder / file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _last(parent: Path, co: str, step: str, file: str = WRITES_FILE) -> str | None:
    """Newest result of one step of one CO; ``write_uncertain`` for an unreadable log; None when there is none."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / file).read_text(encoding="utf-8").splitlines()
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


def last_stage(parent: Path, co: str) -> str | None:
    """The Salesforce stage the newest Case 3 ``stage`` line wrote and read back (``written_verified``), else None
    (no line, another result, or an unreadable log: never guessed)."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / WRITES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    last: dict[str, Any] | None = None
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            return None
        if isinstance(item, dict) and item.get("co_reference") == co and item.get("step") == "stage":
            last = item
    if last is None or last.get("result") != "written_verified" or not isinstance(last.get("to_stage"), str):
        return None
    return last["to_stage"]


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


def after_verified_create(inputs: FollowupInputs, verified_records: Any, read_row: Callable[[], Mapping[str, Any] | None],
                          run_argv: Callable[[list[str]], bool], target_org: str) -> dict[str, str]:
    """The automatic follow-up the dashboard runs when a Case 3 create runner ends (owner request 2026-11-02, CO-0757: the IDs and
    the stage waited ~8 min for a review click). ``{"ids", "stage"}``: the same gate, intent lines, fixed-argv writes and
    read-after-write as ``confirm``; never retried (a recorded write blocks a repeat). Nothing is written without a verified create."""
    lock = build_lock(inputs)
    if lock is not None:
        return {"ids": lock, "stage": "skipped_ids_not_verified"}
    try:
        row = read_row()
    except Exception:
        row = None
    code, evidence, revision = _blocker(inputs, row, verified_records)
    if code == "already_complete":
        return {"ids": "already_present", "stage": "already_present"}
    if code is not None:
        return {"ids": code, "stage": "skipped_ids_not_verified"}
    ids = _write_ids(inputs, read_row, run_argv, target_org, verified_records, evidence, revision)
    if ids not in ("written_verified", "already_present"):
        return {"ids": ids, "stage": "skipped_ids_not_verified"}
    return {"ids": ids, "stage": _write_stage(inputs, read_row, run_argv, target_org)}


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


# --- Account Scanning -> Scan Completed Successfully (owner go 2026-11-06: automatic once the audit shows the scans done) -----

SCAN_STAGE_FROM, SCAN_STAGE_TO = "Account Scanning", "Scan Completed Successfully"
USER_STAGE_FROM, USER_STAGE_TO = "Scan Completed Successfully", "User Created"


def _move_plan(co_row: Any, from_stage: str, to_stage: str) -> stage_wb.StagePlan:
    if not isinstance(co_row, Mapping) or stage_wb.STAGE_FIELD not in co_row or not isinstance(co_row.get("Id"), str):
        return stage_wb.StagePlan("blocked", "row_unreadable", "", to_stage)
    if (co_row.get("Onboarding_Product__c") != ids_wb.CASE3_PRODUCT or co_row.get("Onboarding_Type__c") != ids_wb.CASE3_TYPE):
        return stage_wb.StagePlan("blocked", "route_not_case3_new", "", to_stage)
    current = co_row.get(stage_wb.STAGE_FIELD)
    if current == to_stage:
        return stage_wb.StagePlan("already_present", "already_present", to_stage, to_stage)
    if current != from_stage:
        return stage_wb.StagePlan("blocked", "stage_not_expected", "", to_stage)
    return stage_wb.StagePlan("ready_to_write", "", from_stage, to_stage)


def scan_stage_plan(co_row: Any) -> stage_wb.StagePlan:
    """The Case 3 scan move: ready only from exactly ``Account Scanning`` on the Case 3 New route."""
    return _move_plan(co_row, SCAN_STAGE_FROM, SCAN_STAGE_TO)


def user_stage_plan(co_row: Any) -> stage_wb.StagePlan:
    """The Case 3 user move: ready only from exactly ``Scan Completed Successfully`` on the Case 3 New route."""
    return _move_plan(co_row, USER_STAGE_FROM, USER_STAGE_TO)


_MOVE_STAGES = frozenset({SCAN_STAGE_TO, "User Created"})


def _move_argv(plan: stage_wb.StagePlan, record_id: Any, target_org: str) -> list[str]:
    """The same fixed argument list as ``stage_wb.update_argv`` (that helper only knows the CE-only flow)."""
    if plan.status != "ready_to_write" or plan.to_stage not in _MOVE_STAGES:
        raise ValueError("stage plan is not ready_to_write")
    if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15}|[A-Za-z0-9]{18}", record_id):
        raise ValueError("invalid Salesforce record id")
    if not isinstance(target_org, str) or not re.fullmatch(r"[A-Za-z0-9_.@][A-Za-z0-9_.@-]*", target_org):
        raise ValueError("invalid target org alias")
    return ["sf", "data", "update", "record", "--sobject", stage_wb.SOBJECT, "--record-id", record_id,
            "--values", f"{stage_wb.STAGE_FIELD}='{plan.to_stage}'", "--target-org", target_org]


_scan_argv = _move_argv  # kept name (tests and callers of the scan move)


def _guarded_move(inputs: FollowupInputs, verified_records: Any, read_row: Callable[[], Mapping[str, Any] | None],
                  run_argv: Callable[[list[str]], bool], target_org: str, planner: Callable[[Any], stage_wb.StagePlan],
                  to_stage: str) -> str:
    if inputs.sf_ready is not True:
        return "salesforce_session_not_ready"
    if _newest_verified(verified_records, inputs.reference) is None:
        return "tenant_not_verified"
    if _last(inputs.records_parent, inputs.reference, "stage") not in (None, "written_verified"):
        return "stage_write_needs_review"
    try:
        row = read_row()
    except Exception:
        row = None
    plan = planner(row)
    if plan.status == "already_present":
        return "already_present"
    if plan.status != "ready_to_write":
        return plan.reason
    try:
        argv = _move_argv(plan, (row or {}).get("Id"), target_org)
    except ValueError:
        return "write_blocked"
    try:
        _record(inputs.records_parent, inputs.reference, "stage", "write_started", inputs.now, {"to_stage": to_stage})
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
        _record(inputs.records_parent, inputs.reference, "stage", outcome, inputs.now, {"to_stage": to_stage})
    except Exception:
        return "record_write_failed"
    return outcome


def after_scan_finished(inputs: FollowupInputs, verified_records: Any, read_row: Callable[[], Mapping[str, Any] | None],
                        run_argv: Callable[[list[str]], bool], target_org: str, *, scans_done: bool) -> str:
    """Move ONE Case 3 CO from Account Scanning to Scan Completed Successfully when the audit says both scans are done.

    Gates: build lock (PC, both constants), ``scans_done`` True, Salesforce session ready, a verified Case 3 create, the
    newest recorded stage write of this CO verified (a rejected, started or uncertain one is never followed by another),
    the exact current stage. Intent line before the ONE write, then read-after-write and a result line. Never retried."""
    lock = build_lock(inputs)
    if lock is not None:
        return lock
    if scans_done is not True:
        return "scan_not_finished"
    return _guarded_move(inputs, verified_records, read_row, run_argv, target_org, scan_stage_plan, SCAN_STAGE_TO)


def last_primary_user(parent: Path, co: str) -> str | None:
    """The newest recorded result of the primary-user step (``production_case3_primary_user.STEP``), else None."""
    return _last(parent, co, "primary_user")


def after_primary_user(inputs: FollowupInputs, verified_records: Any, read_row: Callable[[], Mapping[str, Any] | None],
                       run_argv: Callable[[list[str]], bool], target_org: str) -> str:
    """Move ONE Case 3 CO from Scan Completed Successfully to User Created (owner request 2026-11-06), only when the newest
    primary-user result is done (created and read back, already present, or confirmed by the owner). Same gates as the scan
    move; never retried."""
    from . import production_case3_primary_user as primary

    lock = build_lock(inputs)
    if lock is not None:
        return lock
    if not primary.is_done(last_primary_user(inputs.records_parent, inputs.reference)):
        return "primary_user_not_done"
    return _guarded_move(inputs, verified_records, read_row, run_argv, target_org, user_stage_plan, USER_STAGE_TO)


_PU_DIAG = re.compile(r"[0-9]|Button-Grid_[A-Za-z0-9_]{1,40}(?:,Button-Grid_[A-Za-z0-9_]{1,40}){0,11}")


def record_primary_user(parent: Path, co: str, result: str, now: datetime, domain: str = "",
                        diag: Mapping[str, str] | None = None) -> None:
    """One result line of the primary-user step: the email domain and fixed diagnostics (counts, grid tokens); never a
    name or an address."""
    detail = {"domain": domain} if isinstance(domain, str) and re.fullmatch(r"[a-z0-9.-]{1,253}", domain or "") else {}
    for key, value in (diag or {}).items():
        if isinstance(key, str) and re.fullmatch(r"[a-z_]{1,24}", key) and isinstance(value, str) and _PU_DIAG.fullmatch(value):
            detail["diag_" + key] = value
    _record(parent, co, "primary_user", result, now, detail)


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


def edit_note(parent: Path, co: str) -> str:
    """What the card says about Operator and Maximum scan Duration: the recorded post-create Edit Account result, or the manual
    steps for a create without one (before 2026-11-02, or a run that stopped before the edit)."""
    from . import production_case3_edit as edit

    result = _last(parent, co, edit.STEP)
    if result is None:
        return MANUAL_AFTER_TEXT
    if result in edit.TEXT:
        return edit.TEXT[result]
    if result in ("write_started", "write_uncertain"):
        result = "edit_save_no_signal"
    return ("The automatic Edit Account did not finish (" + result + "): open Edit Account in BackOffice, check Maximum scan "
            "Duration (hours) is 90 and the Operator Account is the Technical Advisor's; it is not repeated automatically.")


EDIT_DONE = frozenset({"edit_verified", "edit_already_set"})


def clone_tenant(parent: Path, co: str) -> Mapping[str, Any] | None:
    """The production clone row of this CO's verified tenant (matched by the recorded Account UUID), or None: not in the
    clone yet (it is a nightly copy), no verified create, or anything unreadable. Read-only, local files only."""
    from datetime import timedelta

    from . import leonardo_inventory as inventory
    from .production_create_ui import CREATED_VERIFIED, co_status, load_co_records

    try:
        status = co_status(load_co_records(parent, co))
        uuid = str(status.get("uuid") or "").lower() if status.get("code") == CREATED_VERIFIED else ""
        if not re.fullmatch(r"[0-9a-f]{32}", uuid):
            return None
        payload = inventory.load_latest(inventory.clone_root(), "prod-clone", max_age=timedelta(hours=48),
                                        now=datetime.now(timezone.utc))
        matches = [row for row in payload.get("tenants") or []
                   if isinstance(row, Mapping) and str(row.get("account_uuid") or "").lower() == uuid]
    except Exception:
        return None
    return matches[0] if len(matches) == 1 else None


def clone_shows(tenant: Mapping[str, Any] | None) -> dict[str, bool]:
    """What the clone row proves was done by hand after the run: ``edit`` (Maximum scan Duration 90 and an operator) and
    ``scan`` (a Surface scan status, or a recon scan after the tenant's creation). Unknown shapes prove nothing."""
    from . import production_case3_scan_now as scan_now

    if not isinstance(tenant, Mapping):
        return {"edit": False, "scan": False}
    scan = tenant.get("scan") if isinstance(tenant.get("scan"), Mapping) else {}
    last, created = scan.get("last_recon_scan_ms"), tenant.get("created")
    scanned = scan.get("status") in scan_now.STARTED_STATUSES or (
        type(last) is int and type(created) is int and last > created)
    edited = tenant.get("campaigns_timeout_hours") == 90 and tenant.get("operator_assigned") is True
    return {"edit": edited, "scan": scanned}


def action_items(parent: Path, co: str, tenant: Any = "load") -> list[str]:
    """What still needs a hand in BackOffice after a Case 3 create from 2026-11-02 on (owner request 2026-11-05, CO-0841):
    the Edit Account (Operator / Maximum scan Duration 90) when it did not verify, and the first scan when the run did not
    confirm it started. An item disappears once the production clone shows it done (``clone_shows``). [] when nothing is
    needed or the create predates the automatic edit (no line). ``tenant``: the clone row, or "load" to read it."""
    from . import production_case3_scan_now as scan_now

    edit_result = _last(parent, co, "bo_edit")
    scan_text = scan_now.action_needed(_last(parent, co, scan_now.STEP), edit_result)
    edit_open = edit_result is not None and edit_result not in EDIT_DONE
    if not edit_open and not scan_text:
        return []
    shown = clone_shows(clone_tenant(parent, co) if tenant == "load" else tenant)
    items: list[str] = []
    if edit_open and not shown["edit"]:
        items.append(edit_note(parent, co))
    if scan_text and not shown["scan"]:
        items.append(scan_text)
    return items


def scan_note(parent: Path, co: str) -> str | None:
    from . import production_case3_scan_now as scan_now

    return scan_now.note(_last(parent, co, scan_now.STEP))


def followup_card_html(parent: Path, co: str) -> str:
    """The Case 3 follow-up part of the create card after a verified create: one review button, or the recorded state."""
    from html import escape

    ids, stage = _last(parent, co, "ids"), _last(parent, co, "stage")
    actions = action_items(parent, co)
    if actions:
        manual = ("<div class='note'><strong>Action needed in BackOffice:</strong><ul>"
                  + "".join("<li>" + escape(text) + "</li>" for text in actions) + "</ul></div>")
    else:
        scanned = scan_note(parent, co)
        manual = ("<p class='note'>" + escape(edit_note(parent, co)) + "</p>"
                  + ("<p class='note'>" + escape(scanned) + "</p>" if scanned else ""))
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
    "Writing the Account ID and UUID to Salesforce",
    "Moving the Salesforce stage to Account Scanning",
)


def progress(parent: Path, co: str, since: str, *, running: bool, follow: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Steps of the Case 3 create started at ``since`` from recorded evidence only (records and the follow-up log).

    Same shape as ``production_create_ui.production_progress``. Since 2026-11-02 the IDs and the stage are written automatically
    when the runner ends (``after_verified_create``); ``follow`` is that result, so a follow-up that refused before any write
    shows as failed instead of waiting.
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
            refused = (follow or {}).get("ids") if reached == 3 else (follow or {}).get("stage") if reached == 4 else None
            if refused and refused not in ("written_verified", "already_present"):
                failed = str(refused)  # the automatic follow-up stopped before writing: shown, not an endless wait
        else:
            pending = next(r for r in (ids, stage) if r not in ("written_verified", None))
            reached = 3 + (ids == "written_verified")
            # 2026-11-10 (CO-0844): the intent line is written just before the Salesforce write and its result line right after,
            # so a page read in between saw "write_started" and showed "Stopped: Not allowed: write started"; a failed bar also
            # stops the page refresh, so it stayed that way although both writes verified seconds later. While the runner or the
            # follow-up is still running, that line is a write in flight (active), as on the CE-only bar (CO-0811). Once both have
            # ended, a lone intent line means no result was recorded: shown as uncertain, and still never repeated.
            if pending == "write_started":
                failed = None if running else "write_uncertain"
            else:
                failed = pending
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
