"""The Salesforce follow-up of a verified production Surface renewal edit (owner 2026-11-03, CO-0829).

A renewal CO carries no Surface IDs. Once the one Edit Account is read back as verified (``production_surface_renewal``), the
runner writes, on ONE Customer_Onboarding__c, in this order:

1. the empty ``Surface_Account_ID__c`` / ``Account_UUID__c`` from the verified row (``salesforce_case3_ids_writeback`` plan on the
   ``surface_renewal`` route; a different existing value is a conflict, nothing is overwritten);
2. ``Request Approved -> User Created`` (a renewal keeps its users: owner 2026-11-03), only after (1) read back as verified.

Every write: fresh read, a plan, an intent line on disk BEFORE the write, ONE fixed-argv write, read-after-write, a result line.
A failed, rejected or uncertain step is never retried. The result lines (``WRITES_FILE``) hold the last four characters of the IDs
only. Nothing here runs a process: the caller passes ``read_row`` and ``run_argv``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from . import case3_followup as base
from . import production_surface_renewal as renewal
from . import salesforce_case3_ids_writeback as ids_wb
from . import salesforce_stage_writeback as stage_wb

WRITES_FILE = "surface-renewal-writes.jsonl"
STAGE_FROM, STAGE_TO = "Request Approved", "User Created"


def record(parent: Path, co: str, step: str, result: str, now: datetime, detail: Mapping[str, str] | None = None) -> None:
    base._record(parent, co, step, result, now, detail, file=WRITES_FILE)


def last(parent: Path, co: str, step: str) -> str | None:
    return base._last(parent, co, step, file=WRITES_FILE)


def stage_plan(co_row: Any) -> stage_wb.StagePlan:
    """The one renewal stage move: ready only from exactly ``Request Approved`` on the Surface renewal route."""
    def blocked(reason: str) -> stage_wb.StagePlan:
        return stage_wb.StagePlan("blocked", reason, "", STAGE_TO)

    if not isinstance(co_row, Mapping) or stage_wb.STAGE_FIELD not in co_row or not isinstance(co_row.get("Id"), str):
        return blocked("row_unreadable")
    if not renewal.route_matches(co_row.get("Onboarding_Product__c"), co_row.get("Onboarding_Type__c")):
        return blocked("route_not_surface_renewal")
    current = co_row.get(stage_wb.STAGE_FIELD)
    if current == STAGE_TO:
        return stage_wb.StagePlan("already_present", "already_present", STAGE_TO, STAGE_TO)
    if current != STAGE_FROM:
        return blocked("stage_not_expected")
    return stage_wb.StagePlan("ready_to_write", "", STAGE_FROM, STAGE_TO)


def _read(read_row: Callable[[], Mapping[str, Any] | None]) -> Any:
    try:
        return read_row()
    except Exception:
        return None


def _run(run_argv: Callable[[list[str]], bool], argv: list[str]) -> bool | None:
    try:
        return run_argv(argv)
    except Exception:
        return None


def after_verified_edit(parent: Path, reference: str, verified: Mapping[str, Any], now: datetime,
                        read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool],
                        target_org: str) -> dict[str, str]:
    """``{"ids", "stage"}`` after a verified renewal edit. ``verified`` = the row's ``account_id`` / ``account_uuid`` (memory only).
    Never retried: a recorded write of a step blocks a repeat of that step."""
    if base.build_lock(base.FollowupInputs(reference, parent, now, False, True)) is not None:
        return {"ids": "production_locked", "stage": "skipped_ids_not_verified"}
    ids = _write_ids(parent, reference, verified, now, read_row, run_argv, target_org)
    if ids not in ("written_verified", "already_present"):
        return {"ids": ids, "stage": "skipped_ids_not_verified"}
    return {"ids": ids, "stage": _write_stage(parent, reference, now, read_row, run_argv, target_org)}


def _write_ids(parent: Path, reference: str, verified: Mapping[str, Any], now: datetime,
               read_row: Callable[[], Mapping[str, Any] | None], run_argv: Callable[[list[str]], bool], target_org: str) -> str:
    previous = last(parent, reference, "ids")
    if previous in ("write_started", "write_uncertain"):
        return "ids_write_needs_review"
    row = _read(read_row)
    if not isinstance(row, Mapping) or row.get("Name") != reference:
        return "salesforce_row_unreadable"
    revision = row.get("LastModifiedDate")
    plan = ids_wb.plan("production", ids_wb.SURFACE_RENEWAL_ROUTE, row, verified, revision, revision, True)
    if plan.status == "already_present":
        return "already_present"
    if plan.status != "ready_to_write":
        return plan.reason
    if previous is not None:
        return "ids_write_already_recorded"
    try:
        argv = ids_wb.update_argv(plan, row.get("Id"), target_org)
    except ValueError:
        return "write_blocked"
    detail = {"fields": ",".join(sorted(plan.values)), "id_tail": str(verified["account_id"])[-4:],
              "uuid_tail": str(verified["account_uuid"])[-4:]}
    try:
        record(parent, reference, "ids", "write_started", now, detail)
    except Exception:
        return "record_write_failed"
    accepted = _run(run_argv, argv)
    if accepted is False:
        outcome = "write_rejected"
    elif accepted is not True:
        outcome = "write_uncertain"
    else:
        outcome = ("written_verified" if ids_wb.verify_after_write(_read(read_row), verified) == "verified"
                   else "write_uncertain")
    try:
        record(parent, reference, "ids", outcome, now, detail)
    except Exception:
        return "record_write_failed"
    return outcome


def _write_stage(parent: Path, reference: str, now: datetime, read_row: Callable[[], Mapping[str, Any] | None],
                 run_argv: Callable[[list[str]], bool], target_org: str) -> str:
    if last(parent, reference, "stage") not in (None, "written_verified"):
        return "stage_write_needs_review"
    row = _read(read_row)
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
        record(parent, reference, "stage", "write_started", now, {"to_stage": STAGE_TO})
    except Exception:
        return "record_write_failed"
    accepted = _run(run_argv, argv)
    if accepted is False:
        outcome = "write_rejected"
    elif accepted is not True:
        outcome = "write_uncertain"
    else:
        outcome = "written_verified" if stage_wb.verify_after_write(_read(read_row), STAGE_TO) == "verified" else "write_uncertain"
    try:
        record(parent, reference, "stage", outcome, now, {"to_stage": STAGE_TO})
    except Exception:
        return "record_write_failed"
    return outcome


# --- the card and the progress bar (recorded evidence only) ------------------------------------------------------------------

PROGRESS_STEPS = (
    "Signing in to BackOffice and finding the tenant (sign in with SSO + MFA in the app.pentera.io window)",
    "Editing the tenant in BackOffice (Scanning interval and Expiration date, one Confirm)",
    "Reading the tenant back",
    "Writing the Account ID and UUID to Salesforce",
    "Moving the Salesforce stage to User Created",
)


def _results_since(parent: Path, co: str, since: str) -> dict[str, str]:
    """The newest result per step of one CO recorded at or after ``since`` (an unreadable log reads as uncertain)."""
    import json

    from . import production_create_record as records

    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / WRITES_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except OSError:
        return {renewal.STEP: "write_uncertain"}
    found: dict[str, str] = {}
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            return {renewal.STEP: "write_uncertain"}
        if (isinstance(item, dict) and item.get("co_reference") == co and str(item.get("recorded_at") or "") >= since
                and isinstance(item.get("step"), str) and isinstance(item.get("result"), str)):
            found[item["step"]] = item["result"]
    return found


def progress(parent: Path, co: str, since: str, *, running: bool, refusal: str | None = None) -> dict[str, Any]:
    """Steps of the renewal run started at ``since``, same shape as ``case3_followup.progress``. ``refusal`` is the runner's
    recorded refusal (a stop before the edit) when there is one."""
    found = _results_since(parent, co, since)
    edit, ids, stage = found.get(renewal.STEP), found.get("ids"), found.get("stage")
    reached, failed = 0, None
    if edit == "write_started":
        reached = 1
        failed = None if running else "renewal_save_no_signal"
    elif edit in (renewal.VERIFIED, renewal.ALREADY_CURRENT):
        reached = 3
        for value in (ids, stage):
            if value == "written_verified" or (value == "already_present"):
                reached += 1
            elif value not in (None, "write_started"):
                failed = value
                break
            else:
                if not running and value is None:
                    failed = "follow_up_not_recorded"
                break
    elif edit is not None:
        reached, failed = (2 if edit in renewal.WRITE_OUTCOMES else 0), edit
    elif refusal:
        failed = refusal
    elif not running:
        failed = "runner_ended_without_a_record"
    steps = []
    for index, label in enumerate(PROGRESS_STEPS):
        state = "done" if index < reached else ("failed" if failed else "active") if index == reached else "pending"
        steps.append((label, state))
    done = reached == len(PROGRESS_STEPS)
    current = ("Renewed: tenant edited and verified, IDs written, stage User Created" if done
               else PROGRESS_STEPS[reached] if reached < len(PROGRESS_STEPS) else "")
    return {"steps": steps, "percent": 100 if done else min(95, 5 + reached * 18), "current": current, "done": done,
            "failed": failed}


# --- the card (recorded evidence only) -----------------------------------------------------------------------------------

def edit_attempted(parent: Path, co: str) -> bool:
    """True once the renewal edit of this CO reached Confirm (any recorded write outcome): the button never shows again."""
    result = last(parent, co, renewal.STEP)
    return result is not None and (result in renewal.WRITE_OUTCOMES or result in ("write_started", "write_uncertain"))


def card_html(parent: Path, co: str) -> str:
    """The recorded state of the renewal: edit, IDs, stage. Empty when nothing was recorded yet."""
    edit, ids, stage = last(parent, co, renewal.STEP), last(parent, co, "ids"), last(parent, co, "stage")
    if edit is None:
        return ""
    parts = []
    if edit in renewal.TEXT:
        parts.append(renewal.TEXT[edit])
    elif edit in ("write_started", "write_uncertain"):
        parts.append("The Edit Account may or may not have been saved (no result was recorded): check the tenant's Scanning "
                     "interval and Expiration date in BackOffice. It is not repeated automatically.")
    else:
        parts.append("The renewal edit stopped (" + edit + ")." + (
            " Confirm was clicked: check the tenant in BackOffice; it is not repeated automatically."
            if edit in renewal.WRITE_OUTCOMES else " Nothing was saved in BackOffice."))
    for step, value, label in (("ids", ids, "Account ID and UUID"), ("stage", stage, "stage User Created")):
        if value == "written_verified":
            parts.append("Salesforce: " + label + " written and read back.")
        elif value in ("write_started", "write_uncertain"):
            parts.append("Salesforce: the " + label + " write may or may not have been applied: check the CO. Not repeated.")
        elif value == "write_rejected":
            parts.append("Salesforce refused the " + label + " write; not repeated.")
        elif value is not None:
            parts.append("Salesforce " + step + ": " + value + ".")
    return "".join("<p class='note'>" + escape(text) + "</p>" for text in parts)
