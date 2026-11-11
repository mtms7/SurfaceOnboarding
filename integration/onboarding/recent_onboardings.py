"""Recent onboardings panel and its daily history (owner request 2026-11-10).

After sign-in, the production Onboarding landing page shows the last ``RECENT_LIMIT`` verified dashboard creates (CE-only
New and Case 3 New), newest first, whatever their age: the post-onboarding audit checks of each (``post_onboard_audit``),
looked up in the EU clone and then the US clone, with a short "why" per open or unknown check.

Pure apart from reading local files and appending ``DAILY_LOG``. No live read of any kind is made here.

* Regions: the CO's verified Account UUID is looked up in each configured region's clone (EU, then US). A region whose
  clone is missing, unreadable, of the wrong region or stale (the same rule as ``production_views.clone_status``) gives no
  clone facts at all, so nothing turns "done" from it: the checks stay unknown unless the live log knows.
* Why texts are built only from enum words, dates, numbers and booleans: never a name, domain, email or UUID.
* ``DAILY_LOG``: one masked, allow-listed line per CO per UTC day, written the first time the panel's audit is computed
  that day (the PC's background loop after a clone load, or the page). The panel marks rows whose states changed since
  the newest earlier line.
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from . import post_onboard_audit as audit
from . import production_create_record as records

RECENT_LIMIT = 10
DAILY_LOG = "post-onboard-daily.jsonl"
REGION_ORDER = ("EU", "US")
CLONE_LIMIT_SECONDS = 6 * 3600  # production_views.CLONE_LIMIT_SECONDS
DATA_LIMIT_SECONDS = 36 * 3600  # production_views.DATA_LIMIT_SECONDS
ROUTE_CHIP = {"ce_only": "CE-only", "case3": "Case 3"}
SOURCES = frozenset({"live", "clone", "record"})
STATES = frozenset({audit.DONE, audit.OPEN, audit.UNKNOWN})
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_RESULT = re.compile(r"[a-z_]{1,40}")
_WRITE_LOCK = threading.Lock()
_INDEX_CACHE: dict[str, tuple[Any, dict[str, Any]]] = {}  # region -> (rows_sha256, {uuid: tenant row})

OPEN_WORD = {"surface_scan": "Surface scan not finished", "ce_scan": "CE scan not finished", "operator": "No operator",
             "max_scan": "Max scan not 90 h", "primary_user": "Primary user missing"}
UNKNOWN_WORD = {"surface_scan": "Surface scan not checked", "ce_scan": "CE scan not checked",
                "operator": "Operator not checked", "max_scan": "Max scan not checked",
                "primary_user": "Primary user not checked"}


# --- the clones of both regions ------------------------------------------------------------------------------------------

def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value[:19] + "Z", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clone_data_at(payload: Any) -> datetime | None:
    """When the clone's data is from: ``source.data_latest_modified``, else the capture time."""
    if not isinstance(payload, Mapping):
        return None
    source = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
    return _stamp(source.get("data_latest_modified")) or _stamp(payload.get("captured_at"))


def clone_fresh(payload: Any, now: datetime) -> bool:
    """``production_views.clone_status`` "fresh" on an already loaded payload: query age <= 6 h and data age <= 36 h."""
    if not isinstance(payload, Mapping):
        return False
    age = payload.get("age_seconds")
    source = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
    newest = _stamp(source.get("data_latest_modified"))
    if type(age) is not int or age > CLONE_LIMIT_SECONDS or newest is None:
        return False
    return max(0, int((now - newest).total_seconds())) <= DATA_LIMIT_SECONDS


def region_clones(load: Callable[[str], Any], now: datetime,
                  regions: Iterable[str] = REGION_ORDER) -> list[dict[str, Any]]:
    """``[{"region", "index", "at", "fresh", "reason"}]`` for each configured region, EU first. ``load(env_name)`` returns
    the verified snapshot (``leonardo_inventory.load_latest``) or raises; a failure is that region's "unavailable"."""
    from . import redash_inventory as redash

    out = []
    for region in regions:
        if not redash.region_configured(region):
            continue
        entry: dict[str, Any] = {"region": region, "index": None, "at": None, "fresh": False, "reason": "unavailable"}
        try:
            payload = load(redash.REGION_ENVIRONMENTS[region])
        except Exception:  # noqa: BLE001 - a missing or unusable clone is "unavailable", never an error
            out.append(entry)
            continue
        if not isinstance(payload, Mapping) or redash.snapshot_region(payload) != region:
            out.append({**entry, "reason": "wrong_region"})
            continue
        sha = payload.get("rows_sha256")
        cached = _INDEX_CACHE.get(region)
        if sha is not None and cached is not None and cached[0] == sha:
            index = cached[1]
        else:
            index = {str(row.get("account_uuid") or "").strip().lower(): row for row in payload.get("tenants") or ()
                     if isinstance(row, Mapping) and row.get("account_uuid")}
            if sha is not None:
                _INDEX_CACHE[region] = (sha, index)
        fresh = clone_fresh(payload, now)
        out.append({"region": region, "index": index, "at": clone_data_at(payload), "fresh": fresh,
                    "reason": "" if fresh else "stale"})
    return out


def find_tenant(uuid: str | None, clones: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, Any]:
    """(the region entry that holds the UUID, the tenant row); (None, None) when no readable region has it."""
    if not uuid:
        return None, None
    for entry in clones:
        index = entry.get("index")
        if isinstance(index, Mapping) and uuid in index:
            return dict(entry), index[uuid]
    return None, None


# --- the why of each open / unknown check ----------------------------------------------------------------------------------

def _day(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if type(value) is int and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _surface_facts(source: str | None, live: Mapping[str, Any] | None, tenant: Any) -> tuple[Any, Any, Any, Any]:
    if source == "live" and isinstance(live, Mapping):
        facts = live.get("facts") if isinstance(live.get("facts"), Mapping) else {}
        return facts.get("surface_status"), facts.get("last_recon_ms"), facts.get("created_ms"), live.get("recon")
    if source == "clone" and isinstance(tenant, Mapping):
        scan = tenant.get("scan") if isinstance(tenant.get("scan"), Mapping) else {}
        created = tenant.get("created") if type(tenant.get("created")) is int else None
        return audit._enum(scan.get("status")), audit._int(scan.get("last_recon_scan_ms")), created, None  # noqa: SLF001
    return None, None, None, None


def _no_data_why(region: Mapping[str, Any] | None, clones: list[Mapping[str, Any]], uuid: str | None) -> str:
    if region is not None and not region.get("fresh"):
        when = _day(region.get("at"))
        return f"{region['region']} clone is stale" + (f" (data {when})" if when else "") + ": not trusted"
    if not uuid:
        return "no verified Account UUID recorded"
    if not clones:
        return "no clone readable here"
    missing = [c["region"] for c in clones if c.get("index") is None]
    return "tenant not in the clone yet" + (f" ({'/'.join(missing)} clone unavailable)" if missing else "")


def describe(check: Mapping[str, Any], live: Mapping[str, Any] | None, tenant: Any, clone_at: datetime | None,
             primary: tuple[str | None, datetime | None], no_data: str) -> tuple[str, str]:
    """(short word, why) of one open or unknown check; ("", "") for a done one. Enum words, dates and booleans only."""
    key, state, source = check.get("key"), check.get("state"), check.get("source")
    if state == audit.DONE:
        return "", ""
    word = (OPEN_WORD if state == audit.OPEN else UNKNOWN_WORD).get(str(key), str(key))
    clone_day = _day(clone_at)
    if key == "surface_scan":
        status, last_ms, created_ms, recon = _surface_facts(source, live, tenant)
        if status in audit.SURFACE_RUNNING or recon == "running":
            return "Surface scan running", "running"
        if source is None:
            return word, no_data
        if status == "COMPLETED" and last_ms is not None and created_ms is not None and last_ms <= created_ms:
            return word, "completed scan is older than the tenant"
        if status == "COMPLETED":
            return word, "completed, but the scan list shows no finished recon run"
        if status is None and last_ms is None:
            return word, "no scan has started since the tenant was created"
        when = _day(last_ms)
        return word, f"last scan {status or 'status unknown'}" + (f" on {when}" if when else "")
    if key == "ce_scan":
        if source is None:
            if isinstance(tenant, Mapping) and clone_at is not None:
                return word, "no CE scan yet" + (f" (clone {clone_day})" if clone_day else "")
            return word, no_data
        if source == "live":
            ce = live.get("ce") if isinstance(live, Mapping) else None
            return ("CE scan pending", "CE scan pending") if ce == "running" else (
                word, "no CE scan yet" if ce == "none" else "CE scan list not readable")
        v2 = tenant.get("v2") if isinstance(tenant, Mapping) and isinstance(tenant.get("v2"), Mapping) else {}
        status = audit._enum(v2.get("ce_status"))  # noqa: SLF001
        if status == "PENDING":
            return "CE scan pending", "CE scan pending" + (f" (clone {clone_day})" if clone_day else "")
        return word, (f"last CE scan {status}" if status else "no CE scan yet") + (f" (clone {clone_day})" if clone_day else "")
    if key == "operator":
        if source is None:
            return word, no_data
        return word, "no Operator Account on the tenant" if state == audit.OPEN else "Redash has no operator data for this tenant"
    if key == "max_scan":
        if source is None:
            return word, no_data
        hours = None
        if source == "live" and isinstance(live, Mapping) and isinstance(live.get("facts"), Mapping):
            hours = live["facts"].get("max_scan_hours")
        elif source == "clone" and isinstance(tenant, Mapping):
            hours = tenant.get("campaigns_timeout_hours")
        if type(hours) in (int, float) and not isinstance(hours, bool):
            return word, f"Maximum scan Duration is {hours:g} h"
        return word, "Maximum scan Duration not readable"
    if key == "primary_user":  # our own record only (post_onboard_audit.PRIMARY_USER_NOTE)
        result = primary[0]
        if isinstance(result, str) and _RESULT.fullmatch(result):
            return word, "last attempt: " + result.replace("_", " ") + " (use Create primary user on the CO page)"
        return word, "the customer's Admin user has not been created yet (Create primary user on the CO page)"
    return word, ""


# --- one CO ---------------------------------------------------------------------------------------------------------------

def co_status(parent: Path, co: str, route: str, live: Mapping[str, Any] | None,
              clones: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The audit view of one CO from both regions, plus ``region``, ``clone_day`` and per check ``word`` / ``why``."""
    uuid = audit._verified_uuid(parent, co)  # noqa: SLF001
    region, tenant = find_tenant(uuid, clones)
    trusted = tenant if region is not None and region.get("fresh") else None
    clone_at = region.get("at") if trusted is not None else None
    primary = audit.primary_user_result(parent, co) if route == "case3" else (None, None)
    item = audit.view(route, live, trusted, clone_at, primary)
    no_data = _no_data_why(region, clones, uuid)
    for check in item["checks"]:
        check["word"], check["why"] = describe(check, live, trusted, clone_at, primary, no_data)
    item["region"] = region["region"] if region is not None else None
    item["clone_day"] = _day(clone_at) if clone_at is not None else None
    return item


def recent(parent: Path, clones: list[Mapping[str, Any]], limit: int = RECENT_LIMIT) -> list[dict[str, Any]]:
    """The last ``limit`` verified dashboard creates, newest first: ``[{"co", "route", "created_at", **co_status}]``."""
    creates = audit.verified_creates(parent)
    newest = sorted(creates.items(), key=lambda pair: (pair[1]["created_at"], pair[0]), reverse=True)[:limit]
    live = audit.latest_live(parent)
    out = []
    for co, item in newest:
        status = co_status(parent, co, item["route"], live.get(co), clones)
        out.append({"co": co, "route": item["route"], "created_at": item["created_at"], **status})
    return out


# --- the daily history ------------------------------------------------------------------------------------------------------

def daily_line(co: str, day: str, route: str, region: str | None, clone_day: str | None,
               checks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """One masked daily line; raises ValueError on anything outside the allow-list."""
    if not isinstance(co, str) or not audit._CO.fullmatch(co) or not isinstance(day, str) or not _DAY.fullmatch(day):  # noqa: SLF001
        raise ValueError("daily_line_invalid")
    if route not in audit.CHECKS or region not in (None, *REGION_ORDER):
        raise ValueError("daily_line_invalid")
    if clone_day is not None and (not isinstance(clone_day, str) or not _DAY.fullmatch(clone_day)):
        raise ValueError("daily_line_invalid")
    clean: dict[str, dict[str, Any]] = {}
    for check in checks:
        key, state, source = check.get("key"), check.get("state"), check.get("source")
        if key not in audit.CHECKS[route] or state not in STATES or (source is not None and source not in SOURCES):
            raise ValueError("daily_line_invalid")
        clean[key] = {"state": state, "source": source}
    if set(clean) != set(audit.CHECKS[route]):
        raise ValueError("daily_line_invalid")
    return {"co_reference": co, "day": day, "route": route, "region": region, "clone_day": clone_day, "checks": clean}


def daily_lines(parent: Path) -> list[dict[str, Any]]:
    """Every readable, allow-listed daily line (others are skipped; a missing log is [])."""
    try:
        text = (Path(parent) / records.RECORDS_SUBDIR / DAILY_LOG).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        try:
            item = json.loads(raw)
            checks = [{"key": k, **v} for k, v in (item.get("checks") or {}).items() if isinstance(v, dict)]
            out.append(daily_line(item.get("co_reference"), item.get("day"), item.get("route"), item.get("region"),
                                  item.get("clone_day"), checks))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def record_daily(parent: Path, items: Iterable[Mapping[str, Any]], now: datetime) -> int:
    """Append today's line for every item that has none yet today (UTC); returns how many were written."""
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    with _WRITE_LOCK:
        logged = {(line["co_reference"], line["day"]) for line in daily_lines(parent)}
        new = []
        for item in items:
            co = item.get("co")
            if (co, day) in logged:
                continue
            new.append(daily_line(co, day, item.get("route"), item.get("region"), item.get("clone_day"),
                                  item.get("checks") or ()))
            logged.add((co, day))
        if not new:
            return 0
        folder = Path(parent) / records.RECORDS_SUBDIR
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(folder / DAILY_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(line, sort_keys=True) + "\n" for line in new))
            handle.flush()
            os.fsync(handle.fileno())
        return len(new)


def changed_since(lines: Iterable[Mapping[str, Any]], co: str, today: str,
                  checks: Iterable[Mapping[str, Any]]) -> str | None:
    """The day of the newest earlier line when its states differ from ``checks`` now; else None."""
    previous = None
    for line in lines:
        if line.get("co_reference") == co and str(line.get("day")) < today:
            if previous is None or line["day"] >= previous["day"]:
                previous = line
    if previous is None:
        return None
    now_states = {c.get("key"): c.get("state") for c in checks}
    then_states = {k: (v or {}).get("state") for k, v in (previous.get("checks") or {}).items()}
    return previous["day"] if now_states != then_states else None


# --- the panel --------------------------------------------------------------------------------------------------------------

PANEL_CSS = ("<style>.recent ul{list-style:none;margin:0;padding:0}.recent li{display:grid;grid-template-columns:"
             "minmax(150px,1.2fr) minmax(200px,3fr);gap:4px 14px;padding:7px 0;border-top:1px solid var(--line)}"
             ".recent li:first-child{border-top:0}.recent .meta{display:block;font-size:12px;color:var(--muted)}"
             ".recent .items .chip{margin:0 4px 2px 0}.recent .why{display:block;font-size:12px;color:var(--muted)}"
             ".recent{padding:10px 16px}.recent summary{cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;gap:8px;"
             "list-style:none}.recent summary::-webkit-details-marker{display:none}.recent .rtitle{font-weight:600}"
             ".recent .rhint{margin-left:auto;font-size:12px;color:var(--muted)}.recent details[open] .rhint{visibility:hidden}"
             ".recent details[open] summary{margin-bottom:8px}"
             "@media (max-width:640px){.recent li{grid-template-columns:1fr}}</style>")


def _short_day(when: Any) -> str:
    if isinstance(when, datetime):
        when = when.astimezone(timezone.utc).date()
    return f"{when:%b} {when.day}" if isinstance(when, date) else ""


def panel_html(items: list[Mapping[str, Any]] | None, now: datetime, names: Mapping[str, Any] | None = None,
               history: Iterable[Mapping[str, Any]] = ()) -> str:
    """The landing-page card (escaped; no script). ``items`` None = the status could not be built.

    Owner request 2026-11-10: closed by default (a native ``<details>``) with a one-line summary, so the Onboarding page
    stays clean; one click shows the rows."""
    def head(summary: str) -> str:
        return ("<section class='card recent' aria-labelledby='recent-onb-title'>" + PANEL_CSS + "<details>"
                "<summary><span id='recent-onb-title' class='rtitle'>Recent onboardings</span>" + summary
                + "<span class='rhint'>show details</span></summary>")

    if items is None:
        return head("<span class='chip chip-neutral'>Status unavailable</span>") + "</details></section>"
    if not items:
        return head("<span class='muted'>no dashboard creates yet</span>") + "</details></section>"
    states = [[c.get("state") for c in item.get("checks") or ()] for item in items]
    done = sum(1 for s in states if all(x == audit.DONE for x in s))
    need = sum(1 for s in states if audit.OPEN in s)
    unchecked = len(items) - done - need
    summary = ("<span class='muted'>last " + str(len(items)) + "</span>"
               + ("<span class='chip chip-ok'>" + str(done) + " all done</span>" if done else "")
               + ("<span class='chip chip-warn'>" + str(need) + " need attention</span>" if need else "")
               + ("<span class='chip chip-neutral'>" + str(unchecked) + " not fully checked</span>" if unchecked else ""))
    head = head(summary)
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    lines = list(history)
    rows = []
    for item in items:
        co = str(item.get("co") or "")
        name = (names or {}).get(co)
        changed = changed_since(lines, co, today, item.get("checks") or ())
        meta = " · ".join(part for part in (ROUTE_CHIP.get(str(item.get("route")), ""), item.get("region") or "region —",
                                            "created " + _short_day(item.get("created_at"))) if part)
        left = ("<div><a class='co' href='/co/" + escape(co, quote=True) + "?env=prod'>" + escape(co) + "</a>"
                + (" <span class='muted'>" + escape(str(name)) + "</span>" if isinstance(name, str) and name else "")
                + "<span class='meta'>" + escape(meta) + "</span></div>")
        pending = [c for c in item.get("checks") or () if c.get("state") != audit.DONE]
        if not pending:
            status = "<span class='chip chip-ok'>All done</span>"
        else:
            status = "".join("<span class='chip " + ("chip-warn" if c.get("state") == audit.OPEN else "chip-neutral") + "'>"
                             + escape(str(c.get("word") or c.get("key"))) + "</span>" for c in pending)
            whys = "; ".join(str(c.get("word")) + ": " + str(c.get("why")) for c in pending if c.get("why"))
            status += "<span class='why'>" + escape(whys) + "</span>" if whys else ""
        if changed:
            status += ("<span class='why'><span class='chip chip-info'>changed since "
                       + escape("yesterday" if changed == _yesterday(today) else changed) + "</span></span>")
        rows.append("<li>" + left + "<div class='items'>" + status + "</div></li>")
    return (head + "<ul>" + "".join(rows) + "</ul><p class='muted'>Read-only: the nightly clone (EU, then US) and this "
            "dashboard's own records and BackOffice reads. Checked once a day by itself; the history is kept locally.</p>"
            "</details></section>")


def _yesterday(today: str) -> str:
    return date.fromordinal(date.fromisoformat(today).toordinal() - 1).isoformat()
