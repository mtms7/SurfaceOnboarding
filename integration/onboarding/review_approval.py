"""Review summary hash and one-use approval record (docs/43 section 4).

An operator approves exactly what the review summary showed: the approval is bound to one CO, one route, the
Salesforce source revision and a SHA-256 of the canonical summary. Any change to the CO (so to the revision or the
summary) makes the approval invalid and a new summary must be approved. An approval expires (default 4 hours) and is
consumed once. This module only records and checks approvals: it never starts a run and never calls an external system.
Fail closed: a corrupt or unreadable file raises ApprovalUnavailable; nothing is ever treated as approved by default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
from typing import Any, Mapping

STATE_FILE_NAME = "onboarding_review_approvals.json"
APPROVAL_TTL = timedelta(hours=4)
RECORD_FIELDS = frozenset({"operator", "approved_at", "expires_at", "route", "source_revision", "summary_hash", "consumed_at"})
REFERENCE = re.compile(r"CO-[0-9]{4,10}")
HASH = re.compile(r"[0-9a-f]{64}")
OPERATOR = re.compile(r"[^\s<>'\"]{1,254}")
_lock = Lock()


class ApprovalUnavailable(RuntimeError):
    """The approval file cannot be read or is corrupt: nothing may be treated as approved."""


class ApprovalRefused(ValueError):
    """The approval request itself is invalid (bad reference, hash, revision, route or operator)."""


def summary_hash(summary: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical JSON of the summary (sorted keys, no whitespace, non-JSON values as text)."""
    canonical = json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timezone-aware time required")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ApprovalUnavailable("approval_file_unreadable") from error
    if not isinstance(raw, dict):
        raise ApprovalUnavailable("approval_file_invalid")
    for reference, record in raw.items():
        if (not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(record, dict)
                or not set(record) <= RECORD_FIELDS or not {"operator", "approved_at", "expires_at", "route",
                                                            "source_revision", "summary_hash"} <= set(record)):
            raise ApprovalUnavailable("approval_file_invalid")
    return raw


def _write(path: Path, records: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(records, indent=2, sort_keys=True) + "\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def issue(path: Path, reference: str, route: str, source_revision: str, digest: str, operator: str,
          now: datetime, ttl: timedelta = APPROVAL_TTL) -> dict[str, Any]:
    """Record the operator's approval of exactly this summary; replaces any earlier approval of the same CO."""
    if not REFERENCE.fullmatch(reference or ""):
        raise ApprovalRefused("invalid_reference")
    if not isinstance(route, str) or not route or len(route) > 64:
        raise ApprovalRefused("invalid_route")
    if not isinstance(source_revision, str) or not source_revision or len(source_revision) > 128:
        raise ApprovalRefused("invalid_revision")
    if not HASH.fullmatch(digest or ""):
        raise ApprovalRefused("invalid_summary_hash")
    if not OPERATOR.fullmatch(operator or ""):
        raise ApprovalRefused("invalid_operator")
    if ttl <= timedelta(0) or ttl > timedelta(hours=24):
        raise ApprovalRefused("invalid_lifetime")
    record = {"operator": operator, "approved_at": _stamp(now), "expires_at": _stamp(now + ttl), "route": route,
              "source_revision": source_revision, "summary_hash": digest}
    with _lock:
        records = load(path)
        records[reference] = record
        _write(path, records)
    return dict(record)


def problem(record: Mapping[str, Any] | None, route: str, source_revision: str, digest: str, now: datetime) -> str | None:
    """First reason this approval cannot be used for the summary now shown, or None when it is valid and unused."""
    if record is None:
        return "approval_missing"
    if record.get("consumed_at"):
        return "approval_consumed"
    expires = _parse(record.get("expires_at"))
    if expires is None or expires <= now.astimezone(timezone.utc):
        return "approval_expired"
    if record.get("route") != route:
        return "route_changed"
    if record.get("source_revision") != source_revision:
        return "revision_changed"
    if record.get("summary_hash") != digest:
        return "summary_changed"
    return None


def check(path: Path, reference: str, route: str, source_revision: str, digest: str, now: datetime) -> str | None:
    return problem(load(path).get(reference), route, source_revision, digest, now)


def consume(path: Path, reference: str, route: str, source_revision: str, digest: str, now: datetime) -> str | None:
    """Validate and mark the approval used (one approval, one run). Returns the problem code, or None on success."""
    with _lock:
        records = load(path)
        found = problem(records.get(reference), route, source_revision, digest, now)
        if found is not None:
            return found
        records[reference] = {**records[reference], "consumed_at": _stamp(now)}
        _write(path, records)
    return None
