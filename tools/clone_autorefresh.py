"""Dashboard background job: keep the production clone (Redash saved query 253) fresh without a manual command.

Owner request 2026-10-20: "Clone stale" kept coming back because the hourly Windows task failed whenever Redash was not
reachable (VPN off) and nothing retried afterwards. This job runs inside the desktop dashboard process:

* once at start (every restart collects), then
* whenever the stored clone is older than ``REFRESH_AFTER`` (45 min) or unusable,
* after a failed attempt it waits ``RETRY_AFTER`` (5 min) before the next one, so reconnecting the VPN is picked up.

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

_lock = Lock()
_state: dict[str, str] = {"state": "never", "at": "", "reason": ""}
_last_failure: datetime | None = None


def reset() -> None:
    """Forget every attempt (tests, and a clean start)."""
    global _last_failure
    _last_failure = None
    _state.update({"state": "never", "at": "", "reason": ""})


def status() -> dict[str, str]:
    return dict(_state)


def enabled(environ: Any = None, vm: bool = False) -> bool:
    import os

    environ = os.environ if environ is None else environ
    return not vm and str(environ.get(ENV_SWITCH, "1")).strip() != "0"


def _safe_reason(value: object) -> str:
    return value if isinstance(value, str) and _REASON.fullmatch(value) else _FALLBACK_REASON


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tick(now: datetime, *, age_seconds: Callable[[], int | None], collect_once: Callable[[], dict[str, Any]],
         force: bool = False) -> str:
    """One pass. Returns ``busy``, ``skipped_backoff``, ``skipped_fresh``, ``refreshed`` or ``failed``; never raises."""
    global _last_failure
    if not _lock.acquire(blocking=False):
        return "busy"
    try:
        if _last_failure is not None and timedelta(0) <= now - _last_failure < RETRY_AFTER:
            return "skipped_backoff"
        if not force:
            try:
                age = age_seconds()
            except Exception:  # noqa: BLE001 - an unreadable clone is a reason to collect
                age = None
            if isinstance(age, int) and not isinstance(age, bool) and 0 <= age <= REFRESH_AFTER.total_seconds():
                return "skipped_fresh"
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
            _last_failure = now
            _state.update({"state": "failed", "at": _stamp(now), "reason": reason})
            return "failed"
        _last_failure = None
        _state.update({"state": "ok", "at": _stamp(now), "reason": ""})
        return "refreshed"
    finally:
        _lock.release()


def describe() -> str:
    """One plain sentence for the clone strip; empty until the first attempt (and when the job is off)."""
    state, at, reason = _state["state"], _state["at"], _state["reason"]
    when = at[11:16] + " UTC" if len(at) >= 16 else ""
    if state == "running":
        return "Clone auto-refresh is running now."
    if state == "ok":
        return "Clone auto-refreshed at " + when + "."
    if state == "failed":
        hint = HINTS.get(reason)
        return ("Clone auto-refresh failed at " + when + " (" + reason + ")" + (": " + hint if hint else "")
                + ". It retries every 5 min.")
    return ""


def default_age_seconds() -> int | None:
    """Age of the stored clone (when Redash last ran the query); None when it is missing or unusable."""
    from integration.onboarding import production_views
    from integration.onboarding.leonardo_inventory import clone_root

    info = production_views.clone_status(clone_root(), datetime.now(timezone.utc))
    age = info.get("age_seconds")
    return age if isinstance(age, int) else None


def default_collect_once() -> dict[str, Any]:
    """The read-only collector for query 253 (what ``--collect --query-id 253`` does), in this process."""
    from tools import redash_inventory_collector as collector

    key = collector.load_key(query_id=QUERY_ID)
    return collector.collect(collector.build_opener(), key, query_id=QUERY_ID)


def run_loop(stop: Event | None = None, *, clock: Callable[[], datetime] | None = None,
             age_seconds: Callable[[], int | None] | None = None,
             collect_once: Callable[[], dict[str, Any]] | None = None, wait_seconds: float = LOOP_SECONDS) -> None:
    """Daemon loop: a forced pass at start, then a pass every ``wait_seconds`` until ``stop`` is set. Never dies."""
    stop = stop or Event()
    clock = clock or (lambda: datetime.now(timezone.utc))
    age_seconds = age_seconds or default_age_seconds
    collect_once = collect_once or default_collect_once
    force = True
    while True:
        try:
            tick(clock(), age_seconds=age_seconds, collect_once=collect_once, force=force)
        except Exception:  # noqa: BLE001 - the loop never dies
            pass
        force = False
        if stop.wait(wait_seconds):
            return
