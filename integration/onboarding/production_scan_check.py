"""Read-only CE scan check of one production tenant (build 3, owner decision 2026-10-19). Pure helpers plus a masked file.

``scan_reply`` reduces the page's OWN table reply to the ONE row whose ``accountUuid`` is the verified one and keeps only
scan-related field names (``scan``, ``recon`` or ``leaked`` in the dotted name) with short scalar values: enums, numbers,
booleans, ISO times. Anything else (a domain, a name, an email, a UUID, a list) is ``WITHHELD``; identity fields never
leave. "Finished" is recognised only by a rule in ``CONFIRMED_FINISHED_RULES``, which stays EMPTY until the first live check
of CO-0798 is reviewed (Dev never checked CE scans): until then every readable result is ``unconfirmed`` (fail closed).
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUBDIR = "production-scan-checks"
WITHHELD = "<withheld>"
# Each rule is a tuple of (dotted field name, exact value) pairs that must ALL hold. Empty: nothing is "finished" yet.
CONFIRMED_FINISHED_RULES: tuple[tuple[tuple[str, Any], ...], ...] = ()
_NAME = re.compile(r"(?i)(scan|recon|leaked)")
_IDENTITY = frozenset({"accountUuid", "id", "accountName", "accountDomain", "userEmailDomains"})
_ENUM = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?")
_UUID_LIKE = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-")
_CO = re.compile(r"CO-[0-9]{4,10}")
_STATES = frozenset({"scan_completed", "unconfirmed", "row_not_found", "row_ambiguous", "production_session_expired",
                     "read_unavailable", "reply_unexpected", "scan_running", "no_ce_scan_yet", "unrecognized",
                     "executions_unavailable"})
# Confirmed live 2026-10-19 (owner, CO-0798 Details > Duration Per Scan): the CE scan is the LEAKED_CREDENTIALS_DISCOVERY
# execution; DONE (and DONE_WITH_ERRORS, as for Dev scans, owner 2026-10-07) is finished, PENDING is running.
CE_CAMPAIGN = "LEAKED_CREDENTIALS_DISCOVERY"
EXEC_DONE = frozenset({"DONE", "DONE_WITH_ERRORS"})
EXEC_RUNNING = frozenset({"PENDING"})
_EXEC_KEYS = ("campaign_type", "execution_type", "status", "start", "end", "duration_ms")
# Why the Duration Per Scan executions read failed: the step of ``read_scan_executions`` (a fixed word, never free text).
PROBLEM_STEPS = frozenset({"details_open", "tab_wait", "tab_click", "reply_wait", "reply_status", "id_mismatch", "parse_failed",
                           "other"})
ROW_STAGES = frozenset({"row", "menu", "item"})
COUNT_KEYS = ("rows", "matching_rows", "menu_buttons")
_MENU_ITEM = re.compile(r"Button-Grid_[A-Za-z0-9_]{1,40}")
SURFACE_RUNNING = frozenset({"RUNNING", "IN_PROGRESS", "PENDING", "STARTED"})


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if value >= 0 else WITHHELD
    if isinstance(value, str) and not _UUID_LIKE.search(value) and (_ENUM.fullmatch(value) or _ISO.fullmatch(value)):
        return value
    return WITHHELD


def _flatten(node: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(node, Mapping) or depth > 3:
        return out
    for key, value in node.items():
        if not isinstance(key, str):
            continue
        name = prefix + key
        if isinstance(value, Mapping):
            out.update(_flatten(value, name + ".", depth + 1))
        else:
            out[name] = value
    return out


def _state(fields: Mapping[str, Any]) -> str:
    for rule in CONFIRMED_FINISHED_RULES:
        if rule and all(fields.get(name) == expected for name, expected in rule):
            return "scan_completed"
    return "unconfirmed"


def scan_reply(status: Any, body: Any, expected_uuid: Any) -> dict[str, Any]:
    """``{"state", "found", "fields"}`` for the tenant whose accountUuid is ``expected_uuid`` (never returned)."""
    if type(status) is not int or status == 0:
        return {"state": "read_unavailable", "found": False, "fields": {}}
    if status in (401, 403):
        return {"state": "production_session_expired", "found": False, "fields": {}}
    paged = body.get("pagination_response") if isinstance(body, Mapping) else None
    rows = paged.get("table_data") if isinstance(paged, Mapping) else None
    if status != 200 or not isinstance(rows, list):
        return {"state": "reply_unexpected", "found": False, "fields": {}}
    expected = expected_uuid.strip().lower() if isinstance(expected_uuid, str) else None
    mine = [r for r in rows[:200] if isinstance(r, Mapping) and expected
            and str(_flatten(r).get("accountUuid", "")).strip().lower() == expected]
    if not mine:
        return {"state": "row_not_found", "found": False, "fields": {}}
    if len(mine) > 1:
        return {"state": "row_ambiguous", "found": False, "fields": {}}
    flat = _flatten(mine[0])
    fields = {name: _safe(value) for name, value in sorted(flat.items())
              if _NAME.search(name) and name not in _IDENTITY}
    return {"state": _state(fields), "found": True, "fields": fields}


def record(parent: Path, co: str, reply: Mapping[str, Any], now: datetime) -> Path:
    """Write one masked observation file (state, found, scan field names and safe values); raises on failure."""
    if not _CO.fullmatch(co) or reply.get("state") not in _STATES:
        raise ValueError("scan_observation_invalid")
    fields = {str(k): _safe(v) for k, v in dict(reply.get("fields") or {}).items() if _NAME.search(str(k))}
    stamp = now.astimezone(timezone.utc)
    data = {"co_reference": co, "recorded_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "state": reply["state"],
            "found": bool(reply.get("found")), "fields": fields}
    problem = reply.get("executions_problem")
    if isinstance(problem, str) and problem in PROBLEM_STEPS:
        data["executions_problem"] = problem
    stage = reply.get("executions_stage")
    if isinstance(stage, str) and stage in ROW_STAGES:
        data["executions_stage"] = stage
    counts = reply.get("executions_counts")
    if isinstance(counts, Mapping):
        data["executions_counts"] = {key: counts[key] for key in COUNT_KEYS
                                     if isinstance(counts.get(key), int) and not isinstance(counts.get(key), bool)
                                     and 0 <= counts[key] <= 100000}
    items = reply.get("executions_menu_items")
    if isinstance(items, list):
        data["executions_menu_items"] = [i for i in items[:12] if isinstance(i, str) and _MENU_ITEM.fullmatch(i)]
    execs = reply.get("executions")
    if isinstance(execs, list):
        data["executions"] = [{k: _safe(e.get(k)) for k in _EXEC_KEYS} for e in execs[:50] if isinstance(e, Mapping)]
    folder = Path(parent) / SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{co}-{stamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    n = 1
    while path.exists():
        path = folder / f"{co}-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{n}.json"
        n += 1
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
    return path


def latest(parent: Path, co: str) -> dict[str, Any] | None:
    """The newest observation of ``co`` (None when there is none or it cannot be read)."""
    folder = Path(parent) / SUBDIR
    try:
        names = sorted(p for p in folder.glob(co + "-*.json") if p.is_file())
    except OSError:
        return None
    best = None
    for path in names:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(item, dict) and item.get("co_reference") == co and item.get("state") in _STATES:
            if best is None or str(item.get("recorded_at")) >= str(best.get("recorded_at")):
                best = item
    return best


def _iso_from_ms(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 4_102_444_800_000:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def surface_scan_state(observation: Any) -> dict[str, Any] | None:
    """The SURFACE scan state of a tenant from its scan observation: ``{"state": completed | running | unknown, "last_scan",
    "checked_at"}``, or None when there is no readable observation of a found row. It uses only the tenant row's own
    ``lastScanStatusEnum`` (never the CE executions list, which can be unreadable while the row says COMPLETED)."""
    if not isinstance(observation, Mapping) or observation.get("found") is not True:
        return None
    fields = observation.get("fields")
    if not isinstance(fields, Mapping):
        return None
    status = fields.get("lastScanStatusEnum")
    state = ("completed" if status == "COMPLETED" else "running" if isinstance(status, str) and status in SURFACE_RUNNING
             else "unknown")
    stamp = observation.get("recorded_at")
    return {"state": state, "last_scan": _iso_from_ms(fields.get("lastReconScan")),
            "checked_at": stamp if isinstance(stamp, str) else None}


def executions_state(parsed: Any) -> str:
    """The CE scan state from the parsed Duration Per Scan executions (the Dev parser's shape): ``scan_completed`` when
    every LEAKED_CREDENTIALS_DISCOVERY execution is DONE, ``scan_running`` when one is PENDING, ``no_ce_scan_yet`` when
    there is none, ``unrecognized`` for any other status, ``executions_unavailable`` when nothing was parsed."""
    rows = parsed.get("executions") if isinstance(parsed, Mapping) else None
    if not isinstance(rows, list):
        return "executions_unavailable"
    ce = [r for r in rows if isinstance(r, Mapping) and r.get("campaign_type") == CE_CAMPAIGN]
    if not ce:
        return "no_ce_scan_yet"
    statuses = {r.get("status") for r in ce}
    if statuses & EXEC_RUNNING:
        return "scan_running"
    if statuses <= EXEC_DONE:
        return "scan_completed"
    return "unrecognized"
