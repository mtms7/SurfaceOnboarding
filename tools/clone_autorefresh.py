"""Dashboard background job: keep the production clone (Redash saved query 253) fresh without a manual command.

Owner request 2026-10-20: "Clone stale" kept coming back because the hourly Windows task failed whenever Redash was not
reachable (VPN off) and nothing retried afterwards. This job runs inside the desktop dashboard process:

* once at start (every restart collects), then
* whenever the stored clone is older than ``REFRESH_AFTER`` (45 min) or unusable,
* after a failed attempt it waits ``RETRY_AFTER`` (5 min) before the next one, so reconnecting the VPN is picked up;
* catch-up (owner go 2026-11-05): the cloned database loads once a day (~00:00-00:02 UTC). From ``CATCHUP_FROM`` (00:05 UTC)
  until the stored clone holds data from today's load, it collects every ``CATCHUP_EVERY`` (15 min) instead of 45, so the new
  day's tenants appear within about 15 min of Redash having them. The collector still re-runs Redash only when its cached
  result is older than 60 min; the extra passes are GETs of the latest result.

It calls the same read-only collector as ``tools/redash_inventory_collector.py --collect --query-id 253`` (the approved
use of one saved query; no browser, no MFA, no Claude, never ``accept_shrink``). It does not touch the production lock
constants, never runs on the VM, and keeps only a fixed reason code: no exception text, key, row or payload is stored,
logged or printed. It cannot clear a stale clone whose newest cloned change is older than 36 h (``data_stale``): that is
the cloned database itself, which a Redash re-run does not change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from threading import Event, Lock
from typing import Any, Callable

QUERY_ID = 253
REFRESH_AFTER = timedelta(minutes=45)  # the collector itself only re-runs Redash when its cached result is older than 60 min
RETRY_AFTER = timedelta(minutes=5)
CATCHUP_FROM = timedelta(minutes=5)  # after midnight UTC: the daily clone load (~00:00-00:02 UTC) should be in Redash's copy
CATCHUP_EVERY = timedelta(minutes=15)
LOOP_SECONDS = 60
ENV_SWITCH = "SURFACE_CLONE_AUTOREFRESH"  # "0" turns the job off
_REASON = re.compile(r"[a-z0-9_]{1,64}")
_FALLBACK_REASON = "redash_collector_unavailable"
_STILL_OLD = "redash_refresh_result_still_old"

HINTS = {
    "redash_unreachable": "Redash cannot be reached from this PC: connect the Pentera VPN",
    "redash_key_unavailable": "the Redash key for query 253 is missing: run the collector with --store-key --query-id 253",
    "redash_results_stale": "Redash's own result is older than 36 h",
    "redash_refresh_not_permitted": "Redash did not allow a refresh",
    _STILL_OLD: "Redash returned a result that is still old",
}

class Refresher:
    """The attempt state of one region's clone (EU: the module functions below; US: ``US``, owner request 2026-11-09)."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.state: dict[str, str] = {"state": "never", "at": "", "reason": ""}
        self.last_failure: datetime | None = None
        self.last_attempt: datetime | None = None

    def reset(self) -> None:
        self.last_failure = None
        self.last_attempt = None
        self.state.update({"state": "never", "at": "", "reason": ""})


EU = Refresher()
US = Refresher()
_state = EU.state  # the EU state dict (kept for readers of the former module global)


def reset() -> None:
    """Forget every attempt (tests, and a clean start); both regions."""
    EU.reset()
    US.reset()


def status() -> dict[str, str]:
    return dict(EU.state)


def enabled(environ: Any = None, vm: bool = False) -> bool:
    import os

    environ = os.environ if environ is None else environ
    return not vm and str(environ.get(ENV_SWITCH, "1")).strip() != "0"


def _safe_reason(value: object) -> str:
    return value if isinstance(value, str) and _REASON.fullmatch(value) else _FALLBACK_REASON


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def waiting_for_daily_load(now: datetime, data_age: int | None) -> bool:
    """True from 00:05 UTC until the stored clone's newest change is from today's load (pure). An unknown data age is
    not a catch-up reason (the 45-min rule and the stale gates already cover it)."""
    if not isinstance(data_age, int) or isinstance(data_age, bool) or data_age < 0:
        return False
    now = now.astimezone(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now - midnight >= CATCHUP_FROM and now - timedelta(seconds=data_age) < midnight


def tick(now: datetime, *, age_seconds: Callable[[], int | None], collect_once: Callable[[], dict[str, Any]],
         force: bool = False, data_age_seconds: Callable[[], int | None] | None = None,
         refresher: Refresher | None = None) -> str:
    """One pass. Returns ``busy``, ``skipped_backoff``, ``skipped_fresh``, ``refreshed`` or ``failed``; never raises.
    ``refresher`` is the region's state (default EU)."""
    box = refresher or EU
    _state = box.state  # noqa: F841 - same name as before inside this function
    if not box.lock.acquire(blocking=False):
        return "busy"
    try:
        if box.last_failure is not None and timedelta(0) <= now - box.last_failure < RETRY_AFTER:
            return "skipped_backoff"
        if not force:
            try:
                age = age_seconds()
            except Exception:  # noqa: BLE001 - an unreadable clone is a reason to collect
                age = None
            catchup = False
            if data_age_seconds is not None:
                try:
                    catchup = waiting_for_daily_load(now, data_age_seconds())
                except Exception:  # noqa: BLE001 - no catch-up; the 45-min rule still applies
                    catchup = False
            due_catchup = catchup and (box.last_attempt is None or not timedelta(0) <= now - box.last_attempt < CATCHUP_EVERY)
            if isinstance(age, int) and not isinstance(age, bool) and 0 <= age <= REFRESH_AFTER.total_seconds()                     and not due_catchup:
                return "skipped_fresh"
            if catchup and not due_catchup:
                return "skipped_fresh"  # catch-up passes are at most every 15 min, whatever Redash's own result age
        box.last_attempt = now
        _state.update({"state": "running", "at": _stamp(now), "reason": ""})
        try:
            report = collect_once()
            if isinstance(report, dict) and report.get("result") in ("inventory_collected", "inventory_unchanged") \
                    and not report.get("refresh_failed"):
                reason = ""
            elif isinstance(report, dict) and report.get("refresh_failed"):
                reason = _safe_reason(report.get("refresh_reason") or _STILL_OLD)
            else:
                reason = _safe_reason(report.get("result") if isinstance(report, dict) else None)
        except Exception as error:  # noqa: BLE001 - only the fixed reason code of a collector error is kept
            reason = _safe_reason(getattr(error, "reason", None))
        if reason:
            box.last_failure = now
            _state.update({"state": "failed", "at": _stamp(now), "reason": reason})
            return "failed"
        box.last_failure = None
        _state.update({"state": "ok", "at": _stamp(now), "reason": ""})
        return "refreshed"
    finally:
        box.lock.release()


def describe(refresher: Refresher | None = None) -> str:
    """One plain sentence for the clone strip; empty until the first attempt (and when the job is off)."""
    box = refresher or EU
    name = "US clone" if box is US else "Clone"
    state, at, reason = box.state["state"], box.state["at"], box.state["reason"]
    when = at[11:16] + " UTC" if len(at) >= 16 else ""
    if state == "running":
        return name + " auto-refresh is running now."
    if state == "ok":
        return name + " auto-refreshed at " + when + "."
    if state == "failed":
        hint = HINTS.get(reason)
        if box is US and reason == "redash_key_unavailable":
            hint = "the Redash key for the US query is missing: run the collector with --store-key --region us"
        return (name + " auto-refresh failed at " + when + " (" + reason + ")" + (": " + hint if hint else "")
                + ". It retries every 5 min.")
    return ""


def _region_status(region: str) -> dict[str, Any]:
    from integration.onboarding import production_views
    from integration.onboarding.leonardo_inventory import clone_root

    return production_views.clone_status(clone_root(), datetime.now(timezone.utc), region=region)


def default_age_seconds(region: str = "EU") -> int | None:
    """Age of the stored clone (when Redash last ran the query); None when it is missing or unusable."""
    age = _region_status(region).get("age_seconds")
    return age if isinstance(age, int) else None


def default_data_age_seconds(region: str = "EU") -> int | None:
    """Age of the newest change in the stored clone (the cloned database's daily load); None when unknown."""
    age = _region_status(region).get("data_age_seconds")
    return age if isinstance(age, int) else None


def default_collect_once() -> dict[str, Any]:
    """The read-only collector for query 253 (what ``--collect --query-id 253`` does), in this process."""
    from tools import redash_inventory_collector as collector

    key = collector.load_key(query_id=QUERY_ID)
    return collector.collect(collector.build_opener(), key, query_id=QUERY_ID)


def us_enabled() -> bool:
    """True once the US saved query is pinned (``redash_inventory.US_QUERY_ID``); before that no US job runs."""
    from integration.onboarding import redash_inventory as redash

    return redash.region_configured("US")


def default_collect_once_us() -> dict[str, Any]:
    """The read-only collector for the pinned US query (what ``--collect --region us`` does), in this process."""
    from tools import redash_inventory_collector as collector

    query_id = collector.resolve_query_id(None, region="US")  # fails closed when the US query is not pinned
    key = collector.load_key(query_id=query_id)
    return collector.collect(collector.build_opener(), key, query_id=query_id)


def run_loop(stop: Event | None = None, *, clock: Callable[[], datetime] | None = None,
             age_seconds: Callable[[], int | None] | None = None,
             collect_once: Callable[[], dict[str, Any]] | None = None, wait_seconds: float = LOOP_SECONDS,
             data_age_seconds: Callable[[], int | None] | None = None, region: str = "EU") -> None:
    """Daemon loop: a forced pass at start, then a pass every ``wait_seconds`` until ``stop`` is set. Never dies.
    ``region`` "US" uses the US refresher, the US clone's ages and the US collector."""
    stop = stop or Event()
    clock = clock or (lambda: datetime.now(timezone.utc))
    us = region == "US"
    refresher = US if us else EU
    real_ages = age_seconds is None
    age_seconds = age_seconds or (lambda: default_age_seconds(region))
    collect_once = collect_once or (default_collect_once_us if us else default_collect_once)
    if data_age_seconds is None and real_ages:
        data_age_seconds = lambda: default_data_age_seconds(region)  # noqa: E731 - the real clone: catch up on the daily load
    force = True
    while True:
        try:
            tick(clock(), age_seconds=age_seconds, collect_once=collect_once, force=force, data_age_seconds=data_age_seconds,
                 refresher=refresher)
        except Exception:  # noqa: BLE001 - the loop never dies
            pass
        force = False
        if stop.wait(wait_seconds):
            return
