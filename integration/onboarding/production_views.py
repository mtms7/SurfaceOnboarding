"""Read-only view helpers for the production-first dashboard (docs/46, docs/47).

Everything here only READS the paths it is given and returns plain dicts for the page to render. Nothing here writes,
issues, consumes or records an approval or a create record, starts a run, or touches the network. Every reader fails
closed: a missing, corrupt or unexpected file is reported as ``unreadable`` (or ``unavailable`` where the caller says a
missing file means "not published to this server"), never as "none" or "valid". Output carries only allow-listed
fields: no source revisions, no approval file internals, no secrets and no raw payloads.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from . import leonardo_inventory as inventory
from . import review_approval
from phase1_validator.onboarding_comment_dates import extract_dealhub_dates
from .production_approval import (
    ALLOWED_ROUTE, CO_REFERENCE, ISSUER, RECORD_FIELDS, REQUIRED_FIELDS, SCOPE, valid_revision)
from .production_create_record import (
    RECORDS_SUBDIR, ProductionCreateRecordError, editable_parameters, list_records, load)

CLONE_LIMIT_SECONDS = int(inventory.PRODUCTION_GATE_MAX_AGE.total_seconds())
DISPLAY_MAX_AGE = timedelta(days=3650)  # any snapshot is loaded for display; staleness is shown, not hidden
MAX_TEXT = 160
MAX_LIST = 20
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

LOCK_REASON_TEXT = {
    "production_sessions_flag_missing": "Production sessions are not enabled.",
    "production_approval_missing": "No valid production approval is configured.",
    "production_approval_expired": "The configured production approval is expired.",
    "production_runner_not_supported": "The production runner is not supported in this build.",
}


def lock_reason_text(code: str | None) -> str:
    """Plain words for a ``session_readiness.production_unlock_problem`` code (unknown codes stay generic)."""
    return LOCK_REASON_TEXT.get(code or "", "Production create is not unlocked.")


def _text(value: Any) -> str:
    """A bounded, control-character-free string; never raises."""
    text = _CONTROL.sub(" ", value if isinstance(value, str) else "").strip()
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.strptime(value, _STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# --- Create approvals (7 days, one use): production_create_approvals.json -------------------------------------------

def _approval_item(record: dict[str, Any], now: datetime) -> dict[str, Any]:
    issued, expires = _parse(record["issued_at"]), _parse(record["expires_at"])
    if record.get("consumed_at"):
        state = "consumed"
    elif record.get("superseded_at"):
        state = "superseded"
    elif issued is None or expires is None or not issued <= now.astimezone(timezone.utc) < expires:
        state = "expired"
    else:
        state = "valid"
    return {"approval_ref": _text(record["approval_ref"]), "co_reference": _text(record["co_reference"]),
            "route": _text(record["route"]), "scope": _text(record["scope"]), "issued_by": _text(record["issued_by"]),
            "issued_at": record["issued_at"], "expires_at": record["expires_at"],
            "consumed_at": _text(record.get("consumed_at") or ""), "state": state}


def _valid_approval(key: Any, record: Any) -> bool:
    return (isinstance(key, str) and isinstance(record, dict) and REQUIRED_FIELDS <= set(record) <= RECORD_FIELDS
            and record["approval_ref"] == key and isinstance(record["co_reference"], str)
            and CO_REFERENCE.fullmatch(record["co_reference"]) is not None
            and record["route"] == ALLOWED_ROUTE and record["scope"] == SCOPE
            and isinstance(record["issued_by"], str) and ISSUER.fullmatch(record["issued_by"]) is not None
            and _parse(record["issued_at"]) is not None and _parse(record["expires_at"]) is not None
            and valid_revision(record["source_revision"])
            and ("consumed_at" not in record or _parse(record["consumed_at"]) is not None)
            and ("superseded_at" not in record or _parse(record["superseded_at"]) is not None))


def list_approvals(path: Path, now: datetime, *, missing_is_unavailable: bool = False) -> dict[str, Any]:
    """All create approvals, newest first, for display.

    ``{"state": "ok" | "unavailable" | "unreadable", "reason": str, "items": [...]}``. A missing file is "ok" with no
    items on the desktop; with ``missing_is_unavailable`` (a server that never receives the file) it is "unavailable".
    Items never carry the Salesforce source revision.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_is_unavailable:
            return {"state": "unavailable", "reason": "approvals_not_published", "items": []}
        return {"state": "ok", "reason": "", "items": []}
    except (OSError, ValueError):
        return {"state": "unreadable", "reason": "approval_unreadable", "items": []}
    if not isinstance(raw, dict) or not all(_valid_approval(key, record) for key, record in raw.items()):
        return {"state": "unreadable", "reason": "approval_unreadable", "items": []}
    items = [_approval_item(record, now) for record in raw.values()]
    items.sort(key=lambda item: (item["issued_at"], item["approval_ref"]), reverse=True)
    return {"state": "ok", "reason": "", "items": items}


def pick_approval(listing: dict[str, Any], co_reference: str) -> dict[str, Any]:
    """The create approval that applies to one CO out of a ``list_approvals`` result: a valid one if any, else the newest.

    ``{"state": "none" | "valid" | "expired" | "consumed" | "unreadable" | "unavailable", "reason": str, "item": dict | None}``.
    """
    if listing["state"] != "ok":
        return {"state": listing["state"], "reason": listing["reason"], "item": None}
    mine = [item for item in listing["items"] if item["co_reference"] == co_reference]
    if not mine:
        return {"state": "none", "reason": "", "item": None}
    chosen = next((item for item in mine if item["state"] == "valid"), mine[0])
    return {"state": chosen["state"], "reason": "", "item": chosen}


def approval_for_co(path: Path, co_reference: str, now: datetime, *, missing_is_unavailable: bool = False) -> dict[str, Any]:
    """``pick_approval`` over a fresh read of the approvals file."""
    return pick_approval(list_approvals(path, now, missing_is_unavailable=missing_is_unavailable), co_reference)


# --- Summary approvals (4 hours, one use): onboarding_review_approvals.json -----------------------------------------

def list_summary_approvals(path: Path, now: datetime, *, missing_is_unavailable: bool = False) -> dict[str, Any]:
    """The 4-hour review-summary approvals for display (a different record from the 7-day create approval).

    Same result shape as ``list_approvals``; items carry the CO, operator, approval and expiry times and a state, never
    the summary hash or the source revision. A corrupt file is "unreadable": nothing is treated as approved.
    """
    if missing_is_unavailable and not Path(path).exists():
        return {"state": "unavailable", "reason": "approvals_not_published", "items": []}
    try:
        records = review_approval.load(Path(path))
    except (review_approval.ApprovalUnavailable, OSError, ValueError):
        return {"state": "unreadable", "reason": "approval_unreadable", "items": []}
    items = []
    for reference, record in records.items():
        approved, expires = _parse(record.get("approved_at")), _parse(record.get("expires_at"))
        if record.get("consumed_at"):
            state = "consumed"
        elif approved is None or expires is None or not approved <= now.astimezone(timezone.utc) < expires:
            state = "expired"
        else:
            state = "valid"
        items.append({"co_reference": _text(reference), "operator": _text(record.get("operator")),
                      "route": _text(record.get("route")), "approved_at": _text(record.get("approved_at")),
                      "expires_at": _text(record.get("expires_at")), "state": state})
    items.sort(key=lambda item: (item["approved_at"], item["co_reference"]), reverse=True)
    return {"state": "ok", "reason": "", "items": items}


# --- D2 create records: production-create-records/ ----------------------------------------------------------------

def _record_view(record: dict[str, Any]) -> dict[str, Any]:
    checks = record.get("form_fields_checked") if isinstance(record.get("form_fields_checked"), dict) else {}
    domains = [_text(item) for item in (record.get("domains") or [])[:MAX_LIST]]
    view: dict[str, Any] = {
        "co_reference": _text(record["co_reference"]), "outcome": record["outcome"], "recorded_at": record["recorded_at"],
        "redactions": int(record.get("redactions") or 0),
        "domains": domains,
        "form_checks": {"pass": sum(1 for v in checks.values() if v == "pass"),
                        "fail": sum(1 for v in checks.values() if v == "fail")},
        "editable": editable_parameters(record)}
    for name in ("route", "approval_ref", "refusal_reason", "tenant_name", "account_id", "account_uuid", "plan_summary"):
        view[name] = _text(record.get(name) or "")
    return view


def create_records_by_co(parent_dir: Path, *, missing_is_unavailable: bool = False) -> dict[str, Any]:
    """The newest create record per CO.

    ``parent_dir`` is the folder that contains ``production-create-records``. Result:
    ``{"state": "ok" | "unavailable" | "unreadable", "reason": str, "by_co": {co: view}}``. One corrupt record makes the
    whole answer ``unreadable`` (nothing partial is shown). A missing folder is "ok" and empty unless the caller says
    it is "unavailable" (a server that never receives the folder).
    """
    folder = Path(parent_dir) / RECORDS_SUBDIR
    try:
        present = folder.is_dir()
    except OSError:
        return {"state": "unreadable", "reason": "record_unreadable", "by_co": {}}
    if not present:
        if missing_is_unavailable:
            return {"state": "unavailable", "reason": "records_not_published", "by_co": {}}
        return {"state": "ok", "reason": "", "by_co": {}}
    try:
        by_co: dict[str, dict[str, Any]] = {}
        for path in list_records(parent_dir):  # oldest first, so a later record replaces an earlier one
            view = _record_view(load(path))
            by_co[view["co_reference"]] = view
    except (ProductionCreateRecordError, OSError, ValueError, TypeError, KeyError):
        return {"state": "unreadable", "reason": "record_unreadable", "by_co": {}}
    return {"state": "ok", "reason": "", "by_co": by_co}


def latest_create_record(parent_dir: Path, co_reference: str, *, missing_is_unavailable: bool = False) -> dict[str, Any]:
    """``{"state": "none" | "found" | "unreadable" | "unavailable", "reason": str, "record": view | None}`` for one CO."""
    found = create_records_by_co(parent_dir, missing_is_unavailable=missing_is_unavailable)
    if found["state"] != "ok":
        return {"state": found["state"], "reason": found["reason"], "record": None}
    record = found["by_co"].get(co_reference)
    return {"state": "found" if record else "none", "reason": "", "record": record}


# --- Create state (landing "Create" column and the CO page steps) ---------------------------------------------------

STEPS = ("Dry run", "Owner go", "Submit", "Readback")
OUTCOME_CHIPS = {
    "readback_verified": ("chip-ok", "Verified", 4), "created_unverified": ("chip-warn", "Created, not verified", 3),
    "uncertain_submit": ("chip-bad", "Uncertain: do not retry", 3), "dry_run_passed": ("chip-info", "Dry run passed", 1),
    "refused": ("chip-warn", "Refused", 0), "attempt_started": ("chip-warn", "Attempt started", 2)}
CREATED_OUTCOMES = frozenset({"readback_verified", "created_unverified", "uncertain_submit"})


def create_state(records: dict[str, Any], co_reference: str, *, locked: bool) -> dict[str, Any]:
    """The Create chip for one CO: ``{"code", "css", "label", "steps_done"}``. Unreadable or unavailable never reads as "none"."""
    state = records.get("state")
    if state == "unavailable":
        return {"code": "unavailable", "css": "chip-neutral", "label": "Unavailable on this server", "steps_done": 0}
    if state != "ok":
        return {"code": "unreadable", "css": "chip-warn", "label": "Records unreadable", "steps_done": 0}
    record = (records.get("by_co") or {}).get(co_reference)
    if record is not None:
        css, label, done = OUTCOME_CHIPS.get(record["outcome"], ("chip-warn", "Unknown outcome", 0))
        return {"code": record["outcome"], "css": css, "label": label, "steps_done": done}
    if locked:
        return {"code": "locked", "css": "chip-warn", "label": "Locked", "steps_done": 0}
    return {"code": "dry_run_ready", "css": "chip-neutral", "label": "Dry run ready", "steps_done": 0}


# --- Production checks chip -----------------------------------------------------------------------------------------

NOT_CHECKED_TEXT = {
    "production_inputs_not_prepared": "Production inputs are only prepared for CE-only COs; the clone itself is fine.",
    "co_source_unreadable": "The CO's name or domains could not be read; the clone itself is fine."}


REVIEW_LABEL_WARNINGS = frozenset({"identity_mismatch", "tenant_deleted", "tenant_disabled"})
UUID_NOTE = "UUID check runs on the VM dashboard."


def production_check(match: dict[str, Any] | None) -> dict[str, Any]:
    """One chip for a per-CO production match: Clear, Duplicate, Live paid x N, Clone stale or Clone unavailable.

    ``{"code", "css", "label", "blocked", "live_paid", "reason"}``. Anything unexpected is "unavailable" and blocked:
    a missing or unreadable match is never a clearance.
    """
    unusable = {"code": "unavailable", "css": "chip-warn", "label": "Clone unavailable", "blocked": True, "live_paid": 0,
                "reason": ""}
    if not isinstance(match, dict):
        return unusable
    status, reason = match.get("status"), _text(match.get("reason") or "")
    candidates = [item for item in (match.get("candidates") or []) if isinstance(item, dict)]
    live = sum(1 for item in candidates if item.get("live_paid") is True)
    base = {"blocked": True, "live_paid": live, "reason": reason}
    if status == "clone_unavailable":
        if reason == "inventory_snapshot_stale":
            return {**base, "code": "clone_stale", "css": "chip-warn", "label": "Clone stale"}
        return {**base, "code": "unavailable", "css": "chip-warn", "label": "Clone unavailable"}
    if match.get("uuid_checked") is False and status in ("exists_matches", "exists_differs", "exists_other", "ambiguous"):
        base["reason"] = (reason + " " + UUID_NOTE).strip()
    if status == "not_checked":
        return {**base, "code": "not_checked", "css": "chip-neutral", "label": "Not checked",
                "reason": NOT_CHECKED_TEXT.get(reason, reason)}
    if status == "onboarded":
        warnings = (match.get("review") or {}).get("warnings") if isinstance(match.get("review"), dict) else None
        label = "Onboarded: review" if set(warnings or []) & REVIEW_LABEL_WARNINGS else "Onboarded"
        return {**base, "code": "onboarded", "css": "chip-warn" if warnings else "chip-ok", "label": label,
                "blocked": False, "warnings": list(warnings or [])}
    if status == "uuid_not_in_clone":
        return {**base, "code": "uuid_not_in_clone", "css": "chip-warn", "label": "UUID set, not in clone"}
    if status == "not_in_production":
        return {**base, "code": "clear", "css": "chip-ok", "label": "Clear", "blocked": False}
    if status in ("exists_matches", "exists_differs", "exists_other", "ambiguous"):
        if live > 1:
            return {**base, "code": "live_paid", "css": "chip-bad", "label": "Live paid x " + str(live)}
        if status == "ambiguous":  # e.g. two tenants carry the CO's UUID, but fewer than two are live and paid
            return {**base, "code": "ambiguous", "css": "chip-bad", "label": "Ambiguous"}
        if status == "exists_other":
            return {**base, "code": "other_tenants", "css": "chip-bad", "label": "Other tenants only"}
        return {**base, "code": "duplicate", "css": "chip-bad", "label": "Duplicate"}
    return unusable


def _name_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def duplicate_validation(match: dict[str, Any] | None, tenant_names: list[str], domains: list[str]) -> dict[str, Any]:
    """Duplicate by name, duplicate by domain and the live paid count of exact-name tenants, from the match candidates.

    ``{"available": bool, "by_name": [...], "by_domain": [...], "other": int, "live_paid_exact": int,
    "tenants_checked": int}``. Unavailable (no usable clone) is reported as ``available: False``, never as zero matches.
    """
    if not isinstance(match, dict) or match.get("status") == "clone_unavailable" or match.get("status") not in (
            "not_in_production", "exists_matches", "exists_differs", "exists_other", "ambiguous"):
        return {"available": False, "by_name": [], "by_domain": [], "other": 0, "live_paid_exact": 0, "tenants_checked": 0}
    names = {_name_key(item) for item in tenant_names if item}
    wanted_domains = {str(item).casefold() for item in domains if item}
    by_name, by_domain, other, live_exact = [], [], 0, 0
    for item in (match.get("candidates") or []):
        if not isinstance(item, dict):
            continue
        row = {"name": _text(item.get("name")), "domain": _text(item.get("domain")), "live_paid": item.get("live_paid") is True,
               "deleted": item.get("deleted") is True, "license_type": _text(item.get("license_type"))}
        same_name = _name_key(item.get("name")) in names
        same_domain = str(item.get("domain") or "").casefold() in wanted_domains
        if same_name:
            by_name.append(row)
            live_exact += 1 if row["live_paid"] else 0
        if same_domain:
            by_domain.append(row)
        if not same_name and not same_domain:
            other += 1
    try:
        checked = int(match.get("tenants_checked") or 0)
    except (TypeError, ValueError):
        checked = 0
    return {"available": True, "by_name": by_name, "by_domain": by_domain, "other": other, "live_paid_exact": live_exact,
            "tenants_checked": checked}


def timing_window(start_iso: str | None, today: date, days_before: int) -> dict[str, Any]:
    """The Start-minus-N-days rule: ``{"state": "open" | "closed" | "unknown", "opens": iso | "", "start": iso | ""}``.

    The CSM-request exception has no recorded source, so it is never assumed.
    """
    try:
        start = date.fromisoformat(str(start_iso or "")[:10])
    except ValueError:
        return {"state": "unknown", "opens": "", "start": ""}
    opens = start - timedelta(days=days_before)
    return {"state": "open" if today >= opens else "closed", "opens": opens.isoformat(), "start": start.isoformat()}


# --- Clone status (the strip on every production page) --------------------------------------------------------------

def clone_status(root: Path, now: datetime) -> dict[str, Any]:
    """Age and trust of the production clone (Redash) for display.

    ``{"state": "fresh" | "stale" | "unusable", "captured_at", "age_seconds", "limit_seconds": 21600, "reason", "label"}``.
    Exactly 6 h old is still fresh (the gate uses ``age <= 6 h``); older shows its age but is not trusted. Any problem
    with the snapshot is "unusable" with the loader's reason code.
    """
    base = {"state": "unusable", "captured_at": "", "age_seconds": None, "limit_seconds": CLONE_LIMIT_SECONDS,
            "reason": "", "label": ""}
    try:
        payload = inventory.load_latest(Path(root), "prod-clone", max_age=DISPLAY_MAX_AGE, now=now)
    except inventory.InventoryError as error:
        return {**base, "reason": _text(error.reason)}
    except (OSError, ValueError, TypeError):
        return {**base, "reason": "inventory_snapshot_unreadable"}
    age = int(payload.get("age_seconds") or 0)
    return {**base, "state": "fresh" if age <= CLONE_LIMIT_SECONDS else "stale",
            "captured_at": _text(payload.get("captured_at")), "age_seconds": age, "label": _text(payload.get("environment_label"))}


def age_words(seconds: int | None) -> str:
    """``2 h 10 min`` or ``35 min``; an unknown age is ``unknown``."""
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        return "unknown"
    hours, minutes = divmod(max(0, seconds) // 60, 60)
    return (str(hours) + " h " + str(minutes) + " min") if hours else (str(minutes) + " min")


# --- Onboard card checklist (the production row of the card at the top of the CO page) -------------------------------

UNAVAILABLE_ON_SERVER = "unavailable on this server"
CHECK_CHIPS = {"locked": ("chip-warn", "Locked"), "ready": ("chip-ok", "Ready"), "blocked": ("chip-bad", "Blocked"),
               "unavailable": ("chip-neutral", "Unavailable")}
CHECK_ORDER = (("unlock", "Production runner unlocked"), ("session", "Production session"), ("clone", "Clone fresh under 6 h"),
               ("duplicate", "No duplicate in production"), ("data", "Salesforce data valid"), ("timing", "Timing window"),
               ("approval", "One-CO create approval"), ("dry_run", "Dry run done"), ("owner_go", "Owner go"))


def _item(key: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "label": dict(CHECK_ORDER)[key], "status": status, "detail": _text(detail)}


def onboard_checklist(*, vm: bool, locked: bool, lock_reason: str | None, session_ready: bool | None, clone: dict[str, Any] | None,
                      match: dict[str, Any] | None, domain_count: int | None, uuid_state: str, ce_route: bool,
                      start_iso: str | None, today: date, days_before: int, approval: dict[str, Any] | None,
                      create: dict[str, Any] | None) -> list[dict[str, str]]:
    """The nine production onboarding checks as ``{"key", "label", "status", "detail"}``, in display order.

    ``status`` is ``locked``, ``ready``, ``blocked`` or ``unavailable``. Pure: every input is already read by the caller
    (``clone`` from ``clone_status``, ``approval`` from ``approval_for_co``, ``create`` from ``create_state``). Anything that
    cannot be evaluated is ``unavailable``, never ``ready``; ``uuid_state`` is ``empty``, ``set`` or ``unread``.
    """
    items = []
    # 1 runner unlock
    if locked:
        items.append(_item("unlock", "locked", lock_reason_text(lock_reason)))
    else:
        items.append(_item("unlock", "ready", "Runner unlocked by configuration."))
    # 2 production session: nothing in this build records one, so it is never green without an explicit ready flag
    if vm:
        items.append(_item("session", "unavailable", "Sessions: " + UNAVAILABLE_ON_SERVER + "."))
    elif locked:
        items.append(_item("session", "locked", "Production sessions stay locked. See the Sessions page."))
    elif session_ready is True:
        items.append(_item("session", "ready", "Production session ready."))
    else:
        items.append(_item("session", "unavailable", "No production session status is recorded. See the Sessions page."))
    # 3 clone
    state = clone.get("state") if isinstance(clone, dict) else None
    if state == "fresh":
        items.append(_item("clone", "ready", "Clone " + age_words(clone.get("age_seconds")) + " old. Limit 6 h."))
    elif state == "stale":
        items.append(_item("clone", "blocked", "Clone " + age_words(clone.get("age_seconds")) + " old. Older than 6 h: not trusted."))
    else:
        reason = _text((clone or {}).get("reason")) or "no snapshot"
        items.append(_item("clone", "unavailable", "Clone unusable: " + reason))
    # 4 duplicate validation
    check = production_check(match)
    if check["code"] == "clear":
        items.append(_item("duplicate", "ready", "No match in the production clone (" + str(int((match or {}).get("tenants_checked") or 0))
                           + " tenants checked)."))
    elif check["code"] == "onboarded":
        items.append(_item("duplicate", "blocked", "Already onboarded: tenant found in production (UUID matches the CO). "
                           + (check["reason"] or "")))
    elif check["code"] == "uuid_not_in_clone":
        items.append(_item("duplicate", "blocked", "UUID set but tenant not found in clone (the clone may be older than the create)."))
    elif check["code"] in ("duplicate", "live_paid", "other_tenants", "ambiguous"):
        items.append(_item("duplicate", "blocked", check["label"] + " in the production clone."
                           + (" " + UUID_NOTE if (match or {}).get("uuid_checked") is False else "")))
    else:
        items.append(_item("duplicate", "unavailable", check["label"] + ". A missing match is never a clearance."))
    # 5 Salesforce data
    if uuid_state == "empty" and isinstance(match, dict) and match.get("co_uuid_set") is True:
        items.append(_item("data", "blocked", "UUID sources disagree: the snapshot holds an Account UUID, the read shows none."))
    elif uuid_state not in ("empty", "set") or (ce_route and not isinstance(domain_count, int)):
        items.append(_item("data", "unavailable", "The CO could not be read for this check."))
    elif uuid_state == "set":
        items.append(_item("data", "blocked", "Salesforce already holds an Account UUID for this CO."))
    elif ce_route and domain_count != 1:
        items.append(_item("data", "blocked", "Exactly one email domain is required for CE-only (found " + str(domain_count) + ")."))
    else:
        items.append(_item("data", "ready", ("One email domain, " if ce_route else "") + "no prior Account UUID."))
    # 6 timing window
    window = timing_window(start_iso, today, days_before)
    if window["state"] == "open":
        items.append(_item("timing", "ready", "Start " + window["start"] + " minus " + str(days_before) + " days has passed."))
    elif window["state"] == "closed":
        items.append(_item("timing", "blocked", "Window opens " + window["opens"] + "."))
    else:
        items.append(_item("timing", "unavailable", "Start date not available."))
    # 7 one-CO create approval
    a_state = approval.get("state") if isinstance(approval, dict) else None
    if a_state == "valid":
        items.append(_item("approval", "ready", "Valid to " + _text(str((approval.get("item") or {}).get("expires_at") or ""))[:10] + "."))
    elif a_state == "unavailable":
        items.append(_item("approval", "unavailable", "Create approvals: " + UNAVAILABLE_ON_SERVER + "."))
    elif a_state in ("expired", "consumed", "none", "unreadable"):
        words = {"expired": "Expired: a new create approval is needed.", "consumed": "Used once: a new create approval is needed.",
                 "none": "No create approval recorded.", "unreadable": "The create approval record cannot be read."}
        items.append(_item("approval", "blocked", words[a_state]))
    else:
        items.append(_item("approval", "unavailable", "Create approval state not available."))
    # 8 dry run, 9 owner go: from the create record steps
    code = create.get("code") if isinstance(create, dict) else None
    if code == "unavailable":
        for key, text in (("dry_run", "Create records"), ("owner_go", "Create records")):
            items.append(_item(key, "unavailable", text + ": " + UNAVAILABLE_ON_SERVER + "."))
    elif code == "unreadable" or not isinstance(create, dict):
        for key in ("dry_run", "owner_go"):
            items.append(_item(key, "unavailable", "Create records unreadable or not available."))
    else:
        done = int(create.get("steps_done") or 0)
        pending = "locked" if locked else "blocked"
        items.append(_item("dry_run", "ready" if done >= 1 else pending, "Dry run recorded." if done >= 1 else "No dry run recorded."))
        items.append(_item("owner_go", "ready" if done >= 2 else pending, "Owner go recorded." if done >= 2 else "No owner go recorded."))
    return items


# --- Production onboarding sheet (assisted manual create: guidance only, never a control) --------------------------------
# Docs/49 section 1 plus the Dev runner's CE-only mapping. Pure: the caller reads Salesforce (or the published snapshot)
# and passes the values in; nothing here reads a file, the network or a clock. A value that cannot be derived is shown
# as "unavailable on this server" for that row only, never guessed and never green.

SHEET_REMINDER = ("A missing match is never a clearance: search the exact name and the primary domain in BackOffice yourself; "
                  "the domain must not be a demo domain.")
SHEET_CLOSING = "Production create stays locked in the dashboard: this sheet is guidance only."
SHEET_BACKOFFICE = "https://app.pentera.io"
SHEET_REFUSALS = {
    "one_email_domain_required": "Exactly one email domain is required for CE-only, so there is no single primary domain to enter.",
    "email_domain_invalid": "The one email domain is not a valid domain name, so it must not be entered.",
    "account_uuid_set": "Salesforce already holds an Account UUID for this CO: a tenant exists, do not create another.",
    "account_uuid_unread": "The Account UUID of this CO could not be read, so an empty field is not confirmed.",
    "account_name_unavailable": "The account name could not be read, so the tenant name cannot be derived.",
    "approval_not_approved": "Salesforce does not show this CO as Approved.",
    "duplicate_in_production": "The production clone already holds a tenant that matches this name or domain.",
    "live_paid_in_production": "The production clone shows more than one live paid tenant for this CO.",
    "already_onboarded": "Already onboarded: tenant found in production (the UUID matches the CO). Do not create another.",
    "uuid_not_in_clone": "UUID set but tenant not found in clone (the clone may be older than the create). Do not create another.",
    "match_not_checked": "The production match was not computed for this CO, so a missing match is not a clearance.",
    "uuid_sources_disagree": "UUID sources disagree: the published snapshot holds an Account UUID but the Salesforce read shows it empty.",
    "dealhub_range_missing": "Onboarding Comments hold no usable DealHub date range, so the expiration date cannot be derived.",
    "dealhub_range_ambiguous": "Onboarding Comments hold more than one DealHub date range, so the expiration date is ambiguous.",
    "dealhub_range_invalid": "The DealHub date range in Onboarding Comments is invalid (the end is not after the start).",
    "dealhub_range_ended": "The license would already be over: the expiration date is not after today.",
}
SHEET_STALE_WARNING = "Salesforce data may be out of date: re-check the CO in Salesforce before you start."
SHEET_SOFT_WARNING = "The clone check or the timing window is not clear for this CO: check BackOffice yourself."
SHEET_CHECK_KEYS = {"Route": "route", "Salesforce approval": "approval", "Email domain": "domain", "Account UUID": "uuid",
                    "Clone and production match": "clone", "Timing window": "timing"}
SHEET_SALESFORCE_CHIPS = ("Salesforce approval", "Email domain", "Account UUID")


def _row(label: str, value: str, note: str = "", status: str = "enter") -> dict[str, str]:
    """One sheet row; ``status`` is ``enter`` (type or select the value), ``leave`` (empty, None or off) or ``unavailable``."""
    return {"label": label, "value": _text(value), "note": _text(note), "status": status}


def _unavailable(label: str, note: str) -> dict[str, str]:
    return _row(label, UNAVAILABLE_ON_SERVER, note, "unavailable")


def _uuid_disagrees(co: dict[str, Any], match: dict[str, Any] | None) -> bool:
    """The match saw an Account UUID on the CO (published snapshot) but the CO read shows the field empty."""
    return co.get("uuid_state") == "empty" and isinstance(match, dict) and match.get("co_uuid_set") is True


def _sheet_eligibility(co: dict[str, Any], *, today: date, days_before: int, clone: dict[str, Any] | None,
                       match: dict[str, Any] | None, domain_ok: bool, as_of: str | None = None) -> list[dict[str, str]]:
    """The "before you start" chips: ``{"label", "status": ok | blocked | unavailable, "detail"}``."""
    def chip(label: str, status: str, detail: str) -> dict[str, str]:
        return {"key": SHEET_CHECK_KEYS[label], "label": label, "status": status, "detail": _text(detail)}
    items = [chip("Route", "ok", "CE-only, New Product Onboarding")]
    approval = co.get("approval_status")
    items.append(chip("Salesforce approval", "ok" if approval == "Approved" else "blocked" if approval else "unavailable",
                      "Approved" if approval == "Approved" else ("Status is " + _text(approval)) if approval else "Not read"))
    items.append(chip("Email domain", "ok" if domain_ok else "blocked",
                      "Exactly one valid domain" if domain_ok else "Exactly one valid domain is required"))
    uuid_state = co.get("uuid_state")
    if _uuid_disagrees(co, match):
        items.append(chip("Account UUID", "blocked", "UUID sources disagree (the snapshot holds one, the read shows none)"))
    elif uuid_state == "set" and production_check(match)["code"] == "onboarded":
        items.append(chip("Account UUID", "ok", "Set (matches tenant)"))
    else:
        items.append(chip("Account UUID", "ok" if uuid_state == "empty" else "blocked" if uuid_state == "set" else "unavailable",
                          {"empty": "Empty", "set": "Already set"}.get(uuid_state, "Not read")))
    clone_state = clone.get("state") if isinstance(clone, dict) else None
    check = production_check(match)
    age = age_words(clone.get("age_seconds")) if isinstance(clone, dict) else "unknown"
    label = "Clone and production match"
    if clone_state == "fresh" and check["code"] == "clear":
        items.append(chip(label, "ok", "Not in production; clone " + age + " old, limit 6 h"))
    elif clone_state == "stale":
        items.append(chip(label, "blocked", "Clone " + age + " old: older than 6 h, not trusted"))
    elif check["code"] == "onboarded":
        items.append(chip(label, "blocked", "Already onboarded: tenant found in production (UUID matches the CO)"))
    elif check["code"] == "uuid_not_in_clone":
        items.append(chip(label, "blocked", "UUID set but tenant not found in clone (the clone may be older than the create)"))
    elif check["code"] in ("duplicate", "live_paid", "other_tenants", "ambiguous"):
        items.append(chip(label, "blocked", check["label"] + " in the production clone"
                          + (". " + UUID_NOTE if (match or {}).get("uuid_checked") is False else "")))
    else:
        items.append(chip(label, "unavailable", check["label"] + ": " + UNAVAILABLE_ON_SERVER))
    window = timing_window(co.get("start_iso"), today, days_before)
    items.append(chip("Timing window", {"open": "ok", "closed": "blocked"}.get(window["state"], "unavailable"),
                      {"open": "Open since " + window["opens"], "closed": "Opens " + window["opens"]}.get(
                          window["state"], "Start date not available")))
    if as_of:  # a snapshot: every Salesforce-derived chip says when it was read
        for item in items:
            if item["label"] in SHEET_SALESFORCE_CHIPS:
                item["detail"] = _text(item["detail"] + " (as of " + as_of + ")")
    return items


def onboarding_sheet(co: dict[str, Any], *, rules: dict[str, Any], today: date, days_before: int, vm: bool,
                     clone: dict[str, Any] | None = None, match: dict[str, Any] | None = None,
                     as_of: str | None = None, stale: bool = False) -> dict[str, Any]:
    """The read-only "Production onboarding sheet" of one CO.

    ``co`` carries what the caller read: ``ce_route`` (bool), ``approval_status``, ``account_name``, ``email_domains`` (the
    raw Email_Domains__c text or None), ``uuid_state`` (``empty`` / ``set`` / ``unread``), ``country`` (text or None),
    ``comments`` (Onboarding_Comments__c or None), ``start_iso``. ``rules`` pins the Dev runner mapping: ``names_of`` (account
    name -> (tenant name, alias), raising ValueError), ``domain_of`` (raw text -> the one valid domain or None), and the
    constants ``first_name``, ``last_name``, ``email_local``, ``email_domain``, ``license_type``, ``quantities``, and
    ``license_dates`` (the Dev runner's ``ce_license_dates``: DealHub start, end -> contract start, expiry).
    ``as_of`` is the snapshot time (VM only): the Salesforce-derived chips say "as of" it, and ``stale`` adds a warning.

    Result: ``{"state": "not_applicable" | "refused" | "ready", "reason": code, "eligibility": [...], "rows": [...],
    "after": [...], "reminder", "closing", "tenant_name", "as_of", "stale_warning", "warning"}``. A refused sheet ("Do not
    onboard this CO") has no rows to type: a hard blocker holds (Salesforce approval, Account UUID, email domain, a
    duplicate or live paid tenant in the clone, or an unusable DealHub range). A soft condition (stale or unavailable
    clone, timing window not open) keeps the rows and sets ``warning``.
    """
    base: dict[str, Any] = {"state": "ready", "reason": "", "eligibility": [], "rows": [], "after": [],
                            "reminder": SHEET_REMINDER, "closing": SHEET_CLOSING, "tenant_name": "", "as_of": as_of or "",
                            "stale_warning": SHEET_STALE_WARNING if stale else "", "warning": ""}
    if co.get("ce_route") is not True:
        return {**base, "state": "not_applicable"}
    raw = co.get("email_domains")
    tokens = [item for item in re.split(r"[,;\s]+", raw.strip()) if item] if isinstance(raw, str) else []
    try:
        domain = rules["domain_of"](raw) if len(tokens) == 1 else None
    except (ValueError, TypeError):
        domain = None
    base["eligibility"] = _sheet_eligibility(co, today=today, days_before=days_before, clone=clone, match=match,
                                             domain_ok=domain is not None, as_of=as_of)
    name = co.get("account_name")
    tenant = alias = None
    if isinstance(name, str) and " ".join(name.split()):
        try:
            tenant, alias = rules["names_of"](name)
        except (ValueError, TypeError):
            tenant = alias = None
    check_code = production_check(match)["code"]
    onboarded = check_code == "onboarded" and co.get("uuid_state") == "set"
    if check_code == "onboarded" and isinstance(match, dict):
        review = match.get("review") if isinstance(match.get("review"), dict) else {}
        base["onboarded"] = {"line": _text(review.get("line") or "Onboarded - tenant found in production."),
                             "warnings": [str(code) for code in review.get("warnings") or ()]}
    reason = ("already_onboarded" if onboarded
              else "account_uuid_set" if co.get("uuid_state") == "set"
              else "account_uuid_unread" if co.get("uuid_state") != "empty"
              else "uuid_sources_disagree" if _uuid_disagrees(co, match)
              else "approval_not_approved" if co.get("approval_status") != "Approved"
              else "already_onboarded" if check_code == "onboarded"
              else "uuid_not_in_clone" if check_code == "uuid_not_in_clone"
              else "match_not_checked" if check_code == "not_checked"
              else "live_paid_in_production" if check_code == "live_paid"
              else "duplicate_in_production" if check_code in ("duplicate", "other_tenants", "ambiguous")
              else "one_email_domain_required" if len(tokens) != 1
              else "email_domain_invalid" if domain is None
              else "account_name_unavailable" if tenant is None else "")
    dates = extract_dealhub_dates(co.get("comments"))
    expiry = None
    if not reason:
        try:
            if dates.get("result") != "dealhub_dates_extracted":
                raise ValueError(str(dates.get("error_category") or ""))
            range_start = date.fromisoformat(str(dates.get("dealhub_start_date")))
            range_end = date.fromisoformat(str(dates.get("dealhub_end_date")))
            _contract_start, expiry = rules["license_dates"](range_start, range_end)
            if expiry <= today:
                reason = "dealhub_range_ended"
        except (ValueError, TypeError):
            category = str(dates.get("error_category") or "")
            reason = ("dealhub_range_ambiguous" if "ambiguous" in category
                      else "dealhub_range_invalid" if "invalid" in category else "dealhub_range_missing")
    if reason:
        return {**base, "state": "refused", "reason": reason}
    base["tenant_name"] = _text(tenant)
    q = rules["quantities"]

    def off(label: str, note: str = "") -> dict[str, str]:
        return _row(label, "Off", note, "leave")

    def leave(label: str, value: str) -> dict[str, str]:
        return _row(label, value, "", "leave")

    country = co.get("country")
    if isinstance(country, str) and " ".join(country.split()):
        country_row = _row("Country", " ".join(country.split()), "As shown on the Salesforce Account: pick the same country.")
    else:
        country_row = _unavailable("Country", "The published snapshot does not carry the country." if vm
                                   else "The country could not be read from Salesforce: take it from the Account.")
    expiry_row = _row("Expiration date", expiry.isoformat(),
                      "One year minus a day from the DealHub range start " + str(dates.get("dealhub_start_date"))
                      + ", never later than the range end " + str(dates.get("dealhub_end_date")) + " (Onboarding Comments).")
    base["rows"] = [
        _row("Company name", tenant, "Account name + ' - CE Only'."),
        _row("Account Type", "Customer"),
        _row("Company primary domain", domain, "The single Salesforce email domain. It must not be a demo domain."),
        leave("Alternate Domains", "Leave empty"), leave("SubDomains", "Leave empty"),
        _row("User email domains", str(rules["email_domain"]), "Internal domain, not the customer's."),
        leave("Networks", "Leave empty"), country_row,
        _row("Primary user", "New User", "Pick New User, not an existing user."),
        _row("First name", str(rules["first_name"])), _row("Last name", str(rules["last_name"])),
        _row("Organization Email", str(rules["email_local"]) + "+" + str(alias) + "@" + str(rules["email_domain"]),
             "Internal address, never a customer address."),
        _row("MFA required", "On", "Keep the form default ON.", "leave"),
        leave("Operator Account", "None"), leave("Scanning interval", "None"), off("Scan now"),
        off("Notifications"), off("Multiple users"), off("API access"), off("Phishing"),
        _row("Leaked Credentials", "On", "Turn ON."),
        _row("Leaked Credentials scanning interval", "Weekly"),
        _row("Leaked Credentials scanned domains", domain, "The same primary domain."),
        _row("Include Provisioning", "On"), _row("Include subdomains", "On"),
        off("Advanced switches", "Leave every Advanced switch off."),
        _row("License Type", str(rules["license_type"])),
        _row("Number of assets", str(q["assets"])), _row("Number of domains", str(q["domains"])),
        _row("Number of subdomains", str(q["subdomains"])),
        _row("Start date", today.isoformat(), "The day you enter it: if that is not " + today.isoformat() + ", use that day."),
        expiry_row,
        _row("SpyCloud", "On", "It is set in the create payload: verify it is ON after the create."),
    ]
    if any(item["status"] != "ok" for item in base["eligibility"] if item["label"] in ("Clone and production match", "Timing window")):
        base["warning"] = SHEET_SOFT_WARNING
    base["after"] = [
        "Click Confirm in BackOffice yourself.",
        "Search the new tenant by its exact name: " + _text(tenant) + ".",
        "Open Edit once to confirm the Advanced switches are all off except as above. Never change anything there.",
        "Copy the Account UUID from the tenant (32 hex characters).",
        "Write it into Salesforce Customer_Onboarding__c Account_UUID__c for this CO (or ask Claude to run the guarded "
        "one-field write), then move the Onboarding Stage yourself.",
    ]
    return base


# --- Production CO page guide (docs/50): one verdict, a short strip, one flat table, numbered after-steps ------------------
# Pure view model over ``onboarding_sheet``: nothing is read or written here. The gates are shown once (the strip) and the
# verdict names only the single most important blocker. A missing match is never a clearance.

GUIDE_KEYS = (("route", "Route"), ("approval", "Approved in Salesforce"), ("domain", "One email domain"),
              ("uuid", "Account UUID empty"), ("clone", "Duplicate check"))
GUIDE_LEAVE_SENTENCE = ("Also set exactly: Alternate Domains, SubDomains and Networks empty; Operator Account None; Scanning interval "
                        "None; MFA required on; Scan now, Notifications, Multiple users, API access and Phishing off; every "
                        "Advanced switch off. Touch nothing else.")
GUIDE_CLOSING = "Production create is locked in the dashboard: this page is guidance only."
GUIDE_CHECK_FIRST = "check BackOffice yourself for a duplicate before creating"


def _guide_after(tenant: str) -> list[str]:
    return ["Click Confirm in BackOffice yourself.",
            "Search the new tenant by its exact name: " + _text(tenant) + ".",
            "Open Edit once to confirm the Advanced switches are off and SpyCloud is on. Change nothing there.",
            "Copy the Account UUID from the tenant (32 hex characters).",
            "Write it into Salesforce Account_UUID__c for this CO, then move the Onboarding Stage yourself."]


def production_co_guide(sheet: dict[str, Any], *, clone: dict[str, Any] | None,
                        recorded_duplicates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The simplified production CO page model from an ``onboarding_sheet`` result.

    ``{"state", "verdict": "ready" | "blocked", "headline", "checks", "rows", "leave", "after", "closing", "as_of",
    "recorded_duplicates"}``. ``rows`` keep only fields to type or select (the "leave" rows collapse into ``leave``). Fail
    closed: Ready needs a clone item and a timing item that are both ok, no unavailable row and no recorded production
    duplicate (``recorded_duplicates``: the tenants of a recorded duplicate stop, or None).
    """
    state = sheet.get("state")
    items = {item.get("key"): item for item in sheet.get("eligibility") or [] if isinstance(item, dict)}
    onboarded = sheet.get("onboarded") if isinstance(sheet.get("onboarded"), dict) else None
    checks = [{"label": "Account UUID" if onboarded and key == "uuid" else label, "status": items[key]["status"],
               "detail": items[key]["detail"]} for key, label in GUIDE_KEYS if key in items]
    out: dict[str, Any] = {"state": state, "verdict": "blocked", "headline": "", "checks": checks, "rows": [], "leave": "",
                           "after": [], "closing": GUIDE_CLOSING, "as_of": sheet.get("as_of") or "",
                           "recorded_duplicates": recorded_duplicates}
    if state == "not_applicable":
        out["headline"] = "Not applicable: this CO is not a CE-only New Product Onboarding."
        return out
    if state == "refused" and sheet.get("reason") == "already_onboarded" and onboarded:
        # Neutral, not an error: the create stays blocked (no rows, no after-steps), but the CO is simply done.
        body = onboarded["line"].split(" - ", 1)[-1].rstrip(".")
        if onboarded["warnings"]:
            out["verdict"], out["headline"] = "onboarded_review", "Onboarded: review - tenant found in production; " + body + "."
        else:
            out["verdict"], out["headline"] = "onboarded", "Onboarded: tenant found in production, " + body.replace("tenant enabled, ", "enabled, ")
        return out
    if state == "refused":
        out["headline"] = "Blocked: " + SHEET_REFUSALS.get(sheet.get("reason"), "The values cannot be derived.")
        return out
    if state != "ready":
        out["headline"] = "Blocked: the guide is " + UNAVAILABLE_ON_SERVER + "."
        return out
    out["rows"] = [item for item in sheet["rows"] if item["status"] != "leave"]
    out["leave"] = GUIDE_LEAVE_SENTENCE
    out["after"] = _guide_after(sheet.get("tenant_name") or "")
    clone_item, timing = items.get("clone"), items.get("timing")
    unavailable = [item["label"] for item in out["rows"] if item["status"] == "unavailable"]
    if recorded_duplicates is not None:
        out["headline"] = "Blocked: a duplicate in production was recorded for this CO: " + GUIDE_CHECK_FIRST + "."
    elif clone_item is None or clone_item["status"] != "ok":
        age = age_words(clone.get("age_seconds")) if isinstance(clone, dict) else "unknown"
        if clone_item is None:
            out["headline"] = "Blocked: the clone check is missing: " + GUIDE_CHECK_FIRST + "."
        elif isinstance(clone, dict) and clone.get("state") == "stale":
            out["headline"] = "Blocked: the production clone is " + age + " old (limit 6 h): " + GUIDE_CHECK_FIRST + "."
        else:
            out["headline"] = "Blocked: " + _text(clone_item["detail"]).rstrip(".") + ": " + GUIDE_CHECK_FIRST + "."
    elif sheet.get("stale_warning"):
        out["headline"] = "Blocked: the Salesforce snapshot is older than 6 h: re-check the CO in Salesforce before you start."
    elif unavailable:
        out["headline"] = "Blocked: " + ", ".join(unavailable) + " unavailable: take it from the Salesforce Account before you start."
    elif timing is None:
        out["headline"] = "Blocked: the timing window is missing."
    elif timing["status"] != "ok":
        out["headline"] = "Blocked: " + _text(timing["detail"]).rstrip(".") + "."
    else:
        out["verdict"], out["headline"] = "ready", "Ready to onboard"
    return out
