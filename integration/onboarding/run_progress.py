"""Operator-facing progress of one attended onboarding run (read-only, honest).

The runner records a few milestone events as it goes (``RunLog.event`` -> a small local progress file). This module
maps those events to an ordered list of human steps and one overall percentage. It never simulates progress:
a step is done only when its own event, or a later step's event, was really recorded. Unknown events are ignored.
Until a final result exists the percentage never exceeds 95; a recorded success is 100 and a recorded failure
marks the step that was running as failed. Only step labels, the percentage and the result code leave this module
(no payloads, names, domains or event details).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MAX_RUNNING_PERCENT = 95
START_PERCENT = 5
RENEWAL_ROUTES = frozenset({"renewal_run", "case_4_renew_surface_new_ce", "case_5_renew_ce_new_surface",
                              "case_6_renew_both"})

# (label, ((event step, accepted outcomes or None for any), ...)) in the order the runner emits them.
_CREATE_STEPS: tuple[tuple[str, tuple[tuple[str, frozenset[str] | None], ...]], ...] = (
    ("Reading the CO from Salesforce", (("source_read", frozenset({"ok"})), ("license_dates", None))),
    ("Checking production for duplicates", (("production_gate", None),)),
    ("Checking the Dev tenant inventory", (("inventory_precheck", None),)),
    ("Connecting to Leonardo Dev", (("browser_attach", frozenset({"ok"})),)),
    ("Checking Leonardo Dev for duplicates", (("duplicate_check", None),)),
    ("Opening the Add Account form", (("add_account_open", None),)),
    ("Filling and validating the form", (("confirm_enable", frozenset({"enabled"})),)),
    ("Creating the tenant (Confirm)", (("confirm_click", None),)),
    ("Checking the tenant exists", (("form_close_wait", None), ("post_create_search", None))),
    ("Reading the tenant back", (("readback", None),)),
)
_RENEWAL_STEPS: tuple[tuple[str, tuple[tuple[str, frozenset[str] | None], ...]], ...] = (
    ("Reading the renewal CO from Salesforce", (("source", frozenset({"ok"})),)),
    ("Preparing the Dev mirror tenant", (("mirror", None),)),
    ("Planning the renewal (dry run)", (("renewal_dry_run", None),)),
    ("Applying the renewal in Leonardo Dev", (("renewal_apply", None),)),
)

MILESTONE_STEPS = frozenset(step for table in (_CREATE_STEPS, _RENEWAL_STEPS)
                            for _label, matchers in table for step, _outcomes in matchers)

PENDING, ACTIVE, DONE, FAILED = "pending", "active", "done", "failed"


@dataclass(frozen=True)
class ProgressStep:
    label: str
    state: str


@dataclass(frozen=True)
class RunProgress:
    steps: tuple[ProgressStep, ...]
    percent: int
    current: str  # label of the active (or failed) step, "Starting…" before the first event, "Done" when finished
    status: str  # running | done | failed


def _steps_for(route: str | None):
    return _RENEWAL_STEPS if route in RENEWAL_ROUTES else _CREATE_STEPS


def _seen(events: Iterable[Any]) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    for event in events if isinstance(events, (list, tuple)) else ():
        if isinstance(event, dict) and isinstance(event.get("step"), str):
            outcome = event.get("outcome")
            seen.append((event["step"], outcome if isinstance(outcome, str) else ""))
    return seen


def build_progress(route: str | None, events: Any, result: str | None = None, success: bool = False) -> RunProgress:
    """Map one run's events (and its final ``result`` once recorded) to steps and a percentage."""
    table = _steps_for(route)
    seen = _seen(events)
    reached = [any(step == name and (outcomes is None or outcome in outcomes)
                   for step, outcome in seen for name, outcomes in matchers)
               for _label, matchers in table]
    # A step is done when it, or any later step, was recorded (a step the route skips never blocks the bar).
    last = max((index for index, hit in enumerate(reached) if hit), default=-1)
    total = len(table)
    done = last + 1
    if result:
        if success:
            return RunProgress(tuple(ProgressStep(label, DONE) for label, _m in table), 100, "Done", "done")
        failed_at = min(done, total - 1)
        steps = tuple(ProgressStep(label, DONE if i < done else FAILED if i == failed_at else PENDING)
                      for i, (label, _m) in enumerate(table))
        percent = min(MAX_RUNNING_PERCENT, START_PERCENT + (100 - START_PERCENT) * done // total)
        return RunProgress(steps, percent, table[failed_at][0], "failed")
    steps = tuple(ProgressStep(label, DONE if i < done else ACTIVE if i == done else PENDING)
                  for i, (label, _m) in enumerate(table))
    percent = min(MAX_RUNNING_PERCENT, START_PERCENT + (MAX_RUNNING_PERCENT - START_PERCENT) * done // total)
    current = table[done][0] if done < total else table[-1][0]
    return RunProgress(steps, percent, current if seen and last >= 0 else "Starting…", "running")


# --- the small local progress file (milestone events only; no details) -----------------------------------------

MAX_REFERENCES = 20
MAX_EVENTS_PER_REFERENCE = 60


def _load(path: Path) -> dict[str, list[dict[str, str]]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runs = data.get("runs") if isinstance(data, dict) else None
    return runs if isinstance(runs, dict) else {}


def record_milestone(path: Path, reference: str, entry: dict[str, str]) -> None:
    """Best effort: append one milestone (``t``, ``step``, ``outcome`` only) for ``reference``; never raises."""
    try:
        if entry.get("step") not in MILESTONE_STEPS:
            return
        runs = _load(path)
        events = runs.get(reference, [])
        events.append({key: str(entry.get(key, ""))[:64] for key in ("t", "step", "outcome")})
        runs[reference] = events[-MAX_EVENTS_PER_REFERENCE:]
        runs = dict(list(runs.items())[-MAX_REFERENCES:])
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"note": "Milestone steps of attended runs for the dashboard progress bar. No values.",
                       "runs": runs}, stream)
        os.replace(temp, path)
    except Exception:
        pass


def read_milestones(path: Path, reference: str, since: str = "") -> list[dict[str, str]]:
    """The recorded milestones of ``reference`` at or after ``since`` (an ISO timestamp); [] when unreadable."""
    events = _load(path).get(reference, [])
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict) and str(event.get("t", "")) >= since]
