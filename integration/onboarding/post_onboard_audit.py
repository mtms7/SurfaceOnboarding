"""Post-onboarding audit (owner request + go 2026-11-06): what is still open in BackOffice after a dashboard create.

Pure apart from reading local files. Two sources feed the SAME checks:

* ``live``: the tenant's own BackOffice row (and its Duration Per Scan list), read read-only in the kept, signed-in
  production window on the PC (``--production-scan-check --kept-window-only``). Each read appends ONE masked line to
  ``AUDIT_LOG`` (booleans, enum words, numbers and times only; never a name, domain, email, UUID or token).
* ``clone``: the nightly Redash production clone (a day behind, but readable on the VM without the PC).

For every check the newer source with a known value wins. Which COs: every verified dashboard create (CE-only New, Case 3
New) from the last ``LOOKBACK`` (3 days), plus any create from the last ``FIRST_LOOKBACK`` (30 days) that still has an open
or unknown check (owner 2026-11-06: "first run 30 days, in general the last 3 days").

Checks (owner 2026-11-06; Case 3 "finished" = Surface COMPLETED and CE DONE):

* CE-only: CE scan done; operator assigned.
* Case 3: Surface scan completed (row COMPLETED, a recon after the create, no recon still running); CE scan done; operator
  assigned; Maximum scan Duration 90; primary user (owner request 2026-11-10: done when our own record says so OR the
  clone's ``v2.primary_user_present`` is True; open when the clone says False and there is no done record; unknown when
  neither knows). CE-only has no primary-user step and never gets this check.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import production_create_record as records

AUDIT_LOG = "post-onboard-audits.jsonl"
FIRST_LOOKBACK = timedelta(days=30)
LOOKBACK = timedelta(days=3)
LIVE_EVERY = timedelta(minutes=90)
MAX_SCAN_HOURS = 90
CE_ROUTE = "case_2_new_ce_only"
CASE3_ROUTE = "case_3_combined_baseline"
ROUTES = {CE_ROUTE: "ce_only", CASE3_ROUTE: "case3"}
VERIFIED = "readback_verified"
DONE, OPEN, UNKNOWN = "done", "open", "unknown"
SURFACE_RUNNING = frozenset({"RUNNING", "IN_PROGRESS", "PENDING", "STARTED"})
RECON_CAMPAIGN = "RECON_WITH_ASSET_APPROVALS"
CE_CAMPAIGN = "LEAKED_CREDENTIALS_DISCOVERY"
EXEC_DONE = frozenset({"DONE", "DONE_WITH_ERRORS"})
EXEC_RUNNING = frozenset({"PENDING"})
CHECKS = {
    "ce_only": ("ce_scan", "operator"),
    "case3": ("surface_scan", "ce_scan", "operator", "max_scan", "primary_user"),
}
LABELS = {
    "surface_scan": "Surface scan finished",
    "ce_scan": "CE scan finished",
    "operator": "Operator Account assigned",
    "max_scan": "Maximum scan Duration 90 hours",
    "primary_user": "Primary user (Admin) in the tenant",
}
OPEN_TEXT = {
    "surface_scan": "Surface scan not finished yet (or not started): check the tenant's scans in BackOffice.",
    "ce_scan": "CE scan not finished yet.",
    "operator": "No Operator Account on the tenant: BackOffice > Edit Account > Operator.",
    "max_scan": "Maximum scan Duration is not 90: BackOffice > Edit Account.",
    "primary_user": "No primary user in the tenant: use Create primary user on this page (after the scan).",
}
_CO = re.compile(r"CO-[0-9]{3,6}")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ENUM = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PRIMARY_DONE = frozenset({"user_created_verified", "user_already_present", "owner_confirmed_by_hand"})


def _parse(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
        return None
    return datetime.strptime(stamp, _FORMAT).replace(tzinfo=timezone.utc)


def _int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _enum(value: Any) -> str | None:
    return value if isinstance(value, str) and _ENUM.fullmatch(value) else None


# --- which COs ---------------------------------------------------------------------------------------------------------------

def verified_creates(parent: Path) -> dict[str, dict[str, Any]]:
    """``{co: {"route": "ce_only"|"case3", "created_at": datetime}}`` from the newest verified create record of each CO."""
    found: dict[str, dict[str, Any]] = {}
    try:
        paths = records.list_records(parent)
    except Exception:
        return {}
    for path in paths:
        try:
            item = records.load(path)
        except Exception:
            continue
        co, route, when = item.get("co_reference"), ROUTES.get(item.get("route")), _parse(item.get("recorded_at"))
        if item.get("outcome") != VERIFIED or not isinstance(co, str) or not _CO.fullmatch(co) or route is None or when is None:
            continue
        if co not in found or when > found[co]["created_at"]:
            found[co] = {"route": route, "created_at": when}
    return found


def _completed_by_stage(parent: Path) -> set[str]:
    """COs whose newest verified stage line (CE-only or Case 3 log) is Onboarding Completed."""
    done: set[str] = set()
    for name in ("production-stage-writes.jsonl", "case3-followup-writes.jsonl"):
        newest: dict[str, Any] = {}
        try:
            lines = (Path(parent) / records.RECORDS_SUBDIR / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("co_reference"), str) and item.get("to_stage"):
                newest[item["co_reference"]] = item
        done |= {co for co, item in newest.items()
                 if item.get("result") == "written_verified" and item.get("to_stage") == "Onboarding Completed"}
    return done


def candidates(parent: Path, now: datetime, is_open: Callable[[str, str], bool]) -> list[str]:
    """The COs to audit now, newest create first: created within LOOKBACK, or within FIRST_LOOKBACK while ``is_open(co, route)``."""
    creates = verified_creates(parent)
    finished = _completed_by_stage(parent)
    chosen = []
    for co, item in creates.items():
        age = now - item["created_at"]
        if co in finished or age < timedelta(0) or age > FIRST_LOOKBACK:
            continue
        if age <= LOOKBACK or is_open(co, item["route"]):
            chosen.append((item["created_at"], co))
    return [co for _, co in sorted(chosen, reverse=True)]


# --- facts from each source --------------------------------------------------------------------------------------------------

def live_row_facts(row: Any) -> dict[str, Any]:
    """The masked facts of ONE raw BackOffice tenant row (the page's own table reply): nothing identifying."""
    if not isinstance(row, Mapping):
        return {}
    operators = row.get("operatorAccounts")
    hours = row.get("campaignsTimeoutInHours")
    primary = row.get("primaryUser")
    return {
        "surface_status": _enum(row.get("lastScanStatusEnum")),
        "last_recon_ms": _int(row.get("lastReconScan")),
        "created_ms": _int(row.get("_created")),
        "operator_assigned": bool(operators) if isinstance(operators, list) else None,
        "max_scan_hours": hours if type(hours) in (int, float) and not isinstance(hours, bool) else None,
        "primary_user_present": isinstance(primary, Mapping) if "primaryUser" in row else None,
        "enabled": row.get("enabled") if isinstance(row.get("enabled"), bool) else None,
    }


def _exec_states(executions: Any) -> tuple[str | None, str | None]:
    """(recon, ce) from the Duration Per Scan list: "done" | "running" | "none" | None (unreadable or unrecognised)."""
    if not isinstance(executions, list):
        return None, None
    out = []
    for campaign in (RECON_CAMPAIGN, CE_CAMPAIGN):
        statuses = {e.get("status") for e in executions if isinstance(e, Mapping) and e.get("campaign_type") == campaign}
        if not statuses:
            out.append("none")
        elif statuses & EXEC_RUNNING:
            out.append("running")
        elif statuses <= EXEC_DONE:
            out.append("done")
        else:
            out.append(None)
    return out[0], out[1]


def _surface(status: str | None, last_ms: int | None, created_ms: int | None, recon: str | None = None) -> str:
    if status in SURFACE_RUNNING or recon == "running":
        return OPEN
    if status == "COMPLETED":
        if last_ms is not None and created_ms is not None and last_ms <= created_ms:
            return OPEN  # the completed scan is older than the tenant: not this onboarding's scan
        return DONE if recon in (None, "done") else UNKNOWN
    if status is None and last_ms is None and recon in (None, "none"):
        return OPEN  # no scan has started
    return UNKNOWN  # INCOMPLETE, FAILED or an unknown word: a person looks


def _bool_check(value: Any) -> str:
    return DONE if value is True else OPEN if value is False else UNKNOWN


def _hours_check(value: Any) -> str:
    if value is None:
        return UNKNOWN
    return DONE if value == MAX_SCAN_HOURS else OPEN


def checks_from_live(line: Mapping[str, Any]) -> dict[str, str]:
    """Check states from one audit log line (``facts`` + ``recon`` / ``ce`` words)."""
    facts = line.get("facts") if isinstance(line.get("facts"), Mapping) else {}
    recon, ce = line.get("recon"), line.get("ce")
    return {
        "surface_scan": _surface(facts.get("surface_status"), facts.get("last_recon_ms"), facts.get("created_ms"), recon),
        "ce_scan": {"done": DONE, "running": OPEN, "none": OPEN}.get(ce, UNKNOWN),
        "operator": _bool_check(facts.get("operator_assigned")),
        "max_scan": _hours_check(facts.get("max_scan_hours")),
    }


def clone_primary_user(tenant: Any) -> bool | None:
    """The clone's ``v2.primary_user_present`` (query 253 / the US query); None when absent. The v1 ``primary_user``
    block is not used: the Redash v1 columns carry no primaryUser, so its ``present`` is always False there."""
    v2 = tenant.get("v2") if isinstance(tenant, Mapping) and isinstance(tenant.get("v2"), Mapping) else {}
    value = v2.get("primary_user_present")
    return value if isinstance(value, bool) else None


def checks_from_clone(tenant: Any) -> dict[str, str]:
    """Check states from one minimised clone tenant row (``leonardo_inventory.minimize_row`` shape plus ``v2``)."""
    if not isinstance(tenant, Mapping):
        return {}
    scan = tenant.get("scan") if isinstance(tenant.get("scan"), Mapping) else {}
    created = tenant.get("created") if type(tenant.get("created")) is int else None
    v2 = tenant.get("v2") if isinstance(tenant.get("v2"), Mapping) else {}
    return {
        "surface_scan": _surface(_enum(scan.get("status")), _int(scan.get("last_recon_scan_ms")), created),
        "ce_scan": {"DONE": DONE, "PENDING": OPEN}.get(v2.get("ce_status"), UNKNOWN),
        "operator": _bool_check(_clone_operator(tenant, v2)),
        "max_scan": _hours_check(tenant.get("campaigns_timeout_hours")),
    }


def _clone_operator(tenant: Mapping[str, Any], v2: Mapping[str, Any]) -> bool | None:
    """Operator assigned from the clone: the minimised flag (from Redash operatorCount), else a v2 ``operator_count`` if a later
    profile stores one. 2026-11-10: operatorCount is empty in Redash for ~72% of EU tenants (incl. most CE-only creates), so
    the check stays unknown for them: that is missing source data, not "no operator"."""
    flag = tenant.get("operator_assigned")
    if isinstance(flag, bool):
        return flag
    count = v2.get("operator_count")
    return count > 0 if type(count) is int and count >= 0 else None


# --- the masked live log -----------------------------------------------------------------------------------------------------

def log_line(co: str, now: datetime, state: str, facts: Mapping[str, Any], executions: Any) -> dict[str, Any]:
    """One masked audit line; raises ValueError on anything outside the allow-list."""
    if not isinstance(co, str) or not _CO.fullmatch(co) or not isinstance(state, str) or not re.fullmatch(r"[a-z_]{1,40}", state):
        raise ValueError("audit_line_invalid")
    facts = facts if isinstance(facts, Mapping) else {}
    allowed = ("surface_status", "last_recon_ms", "created_ms", "operator_assigned", "max_scan_hours",
               "primary_user_present", "enabled")
    clean = {key: facts.get(key) for key in allowed}
    if clean["surface_status"] is not None and not _ENUM.fullmatch(str(clean["surface_status"])):
        raise ValueError("audit_line_invalid")
    for key in ("last_recon_ms", "created_ms"):
        if clean[key] is not None and _int(clean[key]) is None:
            raise ValueError("audit_line_invalid")
    for key in ("operator_assigned", "primary_user_present", "enabled"):
        if clean[key] is not None and not isinstance(clean[key], bool):
            raise ValueError("audit_line_invalid")
    hours = clean["max_scan_hours"]
    if hours is not None and (type(hours) not in (int, float) or isinstance(hours, bool)):
        raise ValueError("audit_line_invalid")
    recon, ce = _exec_states(executions)
    return {"co_reference": co, "recorded_at": now.astimezone(timezone.utc).strftime(_FORMAT), "state": state,
            "facts": clean, "recon": recon, "ce": ce}


def append(parent: Path, line: Mapping[str, Any]) -> None:
    folder = Path(parent) / records.RECORDS_SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder / AUDIT_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(line), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def latest_live(parent: Path) -> dict[str, dict[str, Any]]:
    """The newest audit log line per CO (unreadable lines are skipped; a missing log is {})."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / AUDIT_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    newest: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        co = item.get("co_reference") if isinstance(item, dict) else None
        if isinstance(co, str) and _CO.fullmatch(co) and _parse(item.get("recorded_at")) is not None:
            if co not in newest or item["recorded_at"] >= newest[co]["recorded_at"]:
                newest[co] = item
    return newest


# --- the view ----------------------------------------------------------------------------------------------------------------

def view(route: str, live: Mapping[str, Any] | None, clone_tenant: Any, clone_at: datetime | None,
         primary_user: tuple[str | None, datetime | None] = (None, None)) -> dict[str, Any]:
    """``{"route", "checks": [{"key", "label", "state", "source", "at"}], "open", "unknown", "finished", "note"}``.

    Per check the newer source with a known value wins; a known value always beats an unknown one. ``finished`` (the
    scan move rule) is True only when every scan check of the route is done."""
    keys = CHECKS.get(route, ())
    live_at = _parse(live.get("recorded_at")) if isinstance(live, Mapping) else None
    live_checks = checks_from_live(live) if live_at is not None else {}
    clone_checks = checks_from_clone(clone_tenant) if clone_at is not None else {}
    rows = []
    for key in keys:
        if key == "primary_user":  # our own recorded step, else the clone's primaryUserPresent (owner request 2026-11-10)
            result, at = primary_user
            present = clone_primary_user(clone_tenant) if clone_at is not None else None
            if result in PRIMARY_DONE:
                state, source, when = DONE, "record", at
            elif present is not None:
                state, source, when = (DONE if present else OPEN), "clone", clone_at
            else:
                state, source, when = UNKNOWN, ("record" if result else None), (at if result else None)
            rows.append({"key": key, "label": LABELS[key], "state": state, "source": source,
                         "at": when.strftime(_FORMAT) if when else None})
            continue
        options = [(at, checks.get(key), source) for at, checks, source in
                   ((live_at, live_checks, "live"), (clone_at, clone_checks, "clone")) if at is not None and checks.get(key)]
        known = [o for o in options if o[1] != UNKNOWN]
        pick = max(known or options, key=lambda o: o[0]) if options else None
        rows.append({"key": key, "label": LABELS[key], "state": pick[1] if pick else UNKNOWN,
                     "source": pick[2] if pick else None, "at": pick[0].strftime(_FORMAT) if pick else None})
    states = {r["key"]: r["state"] for r in rows}
    scan_keys = [k for k in keys if k in ("surface_scan", "ce_scan")]
    return {"route": route, "checks": rows,
            "open": sum(1 for r in rows if r["state"] == OPEN), "unknown": sum(1 for r in rows if r["state"] == UNKNOWN),
            "finished": bool(scan_keys) and all(states.get(k) == DONE for k in scan_keys),
            "note": None}


def all_done(item: Mapping[str, Any]) -> bool:
    return item.get("open") == 0 and item.get("unknown") == 0


def live_due(last_attempt: datetime | None, now: datetime, window_since: datetime | None) -> bool:
    """A live read is due when never tried, LIVE_EVERY has passed, or the window was signed in after the last try."""
    if last_attempt is None:
        return True
    if now - last_attempt >= LIVE_EVERY:
        return True
    return window_since is not None and window_since > last_attempt


def window_signed_in_since(log_text: str) -> datetime | None:
    """When the current production window started (newest ``launched`` line of the window log), else None."""
    newest = None
    for line in (log_text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "launched":
            when = _parse(parts[0])
            if when is not None and (newest is None or when > newest):
                newest = when
    return newest


def summary_text(item: Mapping[str, Any]) -> str:
    """Short queue chip text."""
    if all_done(item):
        return "BackOffice checks done"
    parts = []
    if item.get("open"):
        parts.append(f"{item['open']} open in BackOffice")
    if item.get("unknown"):
        parts.append(f"{item['unknown']} not checked")
    return " · ".join(parts)


def card_html(item: Mapping[str, Any]) -> str:
    """The CO page card (escaped; no script)."""
    from html import escape

    icon = {DONE: "&#10003;", OPEN: "&#9679;", UNKNOWN: "?"}
    rows = []
    for check in item.get("checks") or ():
        source = {"live": "BackOffice", "clone": "clone", "record": "dashboard record"}.get(check.get("source"), "no data")
        when = (check.get("at") or "")[:16].replace("T", " ")
        extra = "" if check["state"] != OPEN else " <span class='muted'>" + escape(OPEN_TEXT[check["key"]]) + "</span>"
        rows.append(f"<li>{icon.get(check['state'], '?')} {escape(check['label'])}: <strong>{escape(check['state'])}</strong>"
                    f" <span class='muted'>({escape(source)}{' ' + escape(when) + 'Z' if when else ''})</span>{extra}</li>")
    note = f"<p class='muted'>{escape(item['note'])}</p>" if item.get("note") else ""
    chip = ("<span class='chip chip-ok'>All done</span>" if all_done(item)
            else "<span class='chip chip-warn'>" + escape(summary_text(item)) + "</span>")
    return ("<section class='card' aria-labelledby='co-audit-title'><div class='card-head'>"
            "<h2 id='co-audit-title' class='pill'>After onboarding: BackOffice audit</h2>" + chip + "</div><ul>"
            + "".join(rows) + "</ul>" + note
            + "<p class='muted'>Read-only. Live BackOffice reads run by themselves in the kept, signed-in automation window on the "
              "PC; the clone is the nightly copy (also read on the VM). The newer known value is shown.</p></section>")


def views_for(parent: Path, cos: Iterable[str], clone_rows_by_uuid: Callable[[str], Any] | None,
              clone_at: datetime | None) -> dict[str, dict[str, Any]]:
    """The audit view of each CO (clone row found by the verified create's Account UUID)."""
    creates = verified_creates(parent)
    live = latest_live(parent)
    out = {}
    for co in cos:
        item = creates.get(co)
        if item is None:
            continue
        tenant = None
        if clone_rows_by_uuid is not None:
            uuid = _verified_uuid(parent, co)
            tenant = clone_rows_by_uuid(uuid) if uuid else None
        out[co] = view(item["route"], live.get(co), tenant, clone_at if tenant is not None else None,
                       primary_user_result(parent, co) if item["route"] == "case3" else (None, None))
    return out


def _verified_uuid(parent: Path, co: str) -> str | None:
    try:
        paths = records.list_records(parent, co)
    except Exception:
        return None
    best = None
    for path in paths:
        try:
            item = records.load(path)
        except Exception:
            continue
        if item.get("outcome") == VERIFIED and isinstance(item.get("account_uuid"), str):
            if best is None or str(item.get("recorded_at")) >= str(best.get("recorded_at")):
                best = item
    return best["account_uuid"].strip().lower() if best else None


def primary_user_result(parent: Path, co: str) -> tuple[str | None, datetime | None]:
    """(newest primary-user result, its time) from the Case 3 follow-up log; (None, None) when there is none."""
    try:
        lines = (Path(parent) / records.RECORDS_SUBDIR / "case3-followup-writes.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None
    best: tuple[str | None, datetime | None] = (None, None)
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("co_reference") == co and item.get("step") == "primary_user"                 and isinstance(item.get("result"), str):
            best = (item["result"], _parse(item.get("recorded_at")))
    return best
