"""One-use written approval for a production CE-only create (docs/42 section 3.4, docs/45 D1).

An approval is bound to one approval reference, one CO, the CE-only route, the scope "ce_only_single_co" and the
Salesforce source revision seen at approval time. It expires (default 7 days, hard maximum 31 days) and is consumed
by the create, so a retry needs a fresh approval. Only one unconsumed, unexpired approval may exist for a different CO
at a time. This module only records and checks approvals: it never starts a run, never calls an external system and
never touches PRODUCTION_RUNNER_SUPPORTED. Fail closed: a corrupt or unreadable file is reported as
`approval_unreadable` and nothing is ever treated as approved by default. Records hold no secrets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
from threading import Lock
from typing import Any, Mapping

from .models import EngineValue
from .session_readiness import MAX_APPROVAL_LIFETIME
from .web_activation import REFERENCE_PATTERN

STATE_FILE_NAME = "production_create_approvals.json"
ALLOWED_ROUTE = EngineValue.CASE_2.value
SCOPE = "ce_only_single_co"
DEFAULT_LIFETIME = timedelta(days=7)
REQUIRED_FIELDS = frozenset({"approval_ref", "co_reference", "route", "scope", "source_revision", "issued_by",
                             "issued_at", "expires_at"})
RECORD_FIELDS = REQUIRED_FIELDS | {"consumed_at"}
CO_REFERENCE = re.compile(r"CO-[0-9]{4,10}")
ISSUER = re.compile(r"[^\s<>'\"]{1,254}")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LOCK_SUFFIX = ".lock"
LOCK_STALE_AFTER_SECONDS = 60
_lock = Lock()


class ApprovalRefused(ValueError):
    """The issue request is invalid; `reason` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Unreadable(RuntimeError):
    pass


def _aware(moment: Any) -> bool:
    return isinstance(moment, datetime) and moment.tzinfo is not None and moment.utcoffset() is not None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _valid_ref(value: Any) -> bool:
    return isinstance(value, str) and REFERENCE_PATTERN.fullmatch(value) is not None


def _valid_co(value: Any) -> bool:
    return isinstance(value, str) and CO_REFERENCE.fullmatch(value) is not None


def _valid_revision(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128


def _valid_record(key: Any, record: Any) -> bool:
    return (isinstance(record, dict) and REQUIRED_FIELDS <= set(record) <= RECORD_FIELDS
            and _valid_ref(key) and record["approval_ref"] == key and _valid_co(record["co_reference"])
            and record["route"] == ALLOWED_ROUTE and record["scope"] == SCOPE
            and _valid_revision(record["source_revision"])
            and isinstance(record["issued_by"], str) and ISSUER.fullmatch(record["issued_by"]) is not None
            and _parse(record["issued_at"]) is not None and _parse(record["expires_at"]) is not None
            and ("consumed_at" not in record or _parse(record["consumed_at"]) is not None))


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise _Unreadable("approval_unreadable") from error
    if not isinstance(raw, dict) or not all(_valid_record(key, record) for key, record in raw.items()):
        raise _Unreadable("approval_unreadable")
    return raw


def _write(path: Path, records: Mapping[str, Any]) -> None:
    """Atomic, private (0600 where the platform supports it) replace of the state file."""
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


def issue(path: Path, approval_ref: str, co_reference: str, route: str, source_revision: str, issued_by: str,
          allowed_issuers: frozenset[str], now: datetime, lifetime: timedelta = DEFAULT_LIFETIME) -> dict[str, Any]:
    """Record a one-CO CE-only production approval. Raises ApprovalRefused(reason) on anything invalid."""
    if route != ALLOWED_ROUTE:
        raise ApprovalRefused("route_not_ce_only")
    if (not isinstance(allowed_issuers, frozenset) or not isinstance(issued_by, str)
            or issued_by not in allowed_issuers or not ISSUER.fullmatch(issued_by)):
        raise ApprovalRefused("issuer_not_allowed")
    if not _valid_ref(approval_ref):
        raise ApprovalRefused("invalid_approval_ref")
    if not _valid_co(co_reference):
        raise ApprovalRefused("invalid_co_reference")
    if not _valid_revision(source_revision):
        raise ApprovalRefused("invalid_revision")
    if not isinstance(lifetime, timedelta) or lifetime <= timedelta(0) or lifetime > MAX_APPROVAL_LIFETIME:
        raise ApprovalRefused("invalid_lifetime")
    if not _aware(now):
        raise ApprovalRefused("naive_datetime")
    moment = now.astimezone(timezone.utc).replace(microsecond=0)
    record = {"approval_ref": approval_ref, "co_reference": co_reference, "route": route, "scope": SCOPE,
              "source_revision": source_revision, "issued_by": issued_by, "issued_at": _stamp(moment),
              "expires_at": _stamp(moment + lifetime)}
    with _lock:
        try:
            records = _load(path)
        except _Unreadable:
            raise ApprovalRefused("approval_unreadable") from None
        if approval_ref in records:
            raise ApprovalRefused("approval_ref_reused")
        for other in records.values():
            expires = _parse(other["expires_at"])
            if (other["co_reference"] != co_reference and "consumed_at" not in other
                    and expires is not None and expires > moment):
                raise ApprovalRefused("other_approval_active")
        records[approval_ref] = record
        _write(path, records)
    return dict(record)


def _problem(record: Mapping[str, Any] | None, co_reference: Any, route: Any, source_revision: Any,
             now: Any) -> str | None:
    if record is None:
        return "approval_missing"
    if record.get("consumed_at"):
        return "approval_consumed"
    issued = _parse(record.get("issued_at"))
    expires = _parse(record.get("expires_at"))
    if not _aware(now) or issued is None or expires is None:
        return "approval_expired"
    moment = now.astimezone(timezone.utc)
    if not issued <= moment < expires:
        return "approval_expired"
    if record.get("co_reference") != co_reference:
        return "approval_co_mismatch"
    if record.get("route") != route or route != ALLOWED_ROUTE:
        return "approval_route_mismatch"
    if record.get("source_revision") != source_revision:
        return "approval_revision_drift"
    return None


def check(path: Path, approval_ref: str, co_reference: str, route: str, source_revision: str,
          now: datetime) -> str | None:
    """None when the approval is valid and unused for exactly this CO, route and revision; else a reason code."""
    try:
        records = _load(path)
    except _Unreadable:
        return "approval_unreadable"
    return _problem(records.get(approval_ref) if isinstance(approval_ref, str) else None, co_reference, route,
                    source_revision, now)


def _acquire_file_lock(path: Path) -> tuple[int | None, str | None]:
    """(fd, None) when the exclusive lock file was created, else (None, reason). Never deletes a lock it did not make."""
    lock_path = path.with_name(path.name + LOCK_SUFFIX)
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), None
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            return None, "approval_locked"
        return None, "approval_locked_stale" if age > LOCK_STALE_AFTER_SECONDS else "approval_locked"
    except FileNotFoundError:
        return None, "approval_missing"  # the state directory does not exist, so no approval does either
    except OSError:
        return None, "approval_locked"


def consume(path: Path, approval_ref: str, co_reference: str, route: str, source_revision: str,
            now: datetime) -> str | None:
    """Validate and mark the approval used (one approval, one create). None on success, else a reason code."""
    with _lock:
        fd, locked = _acquire_file_lock(path)
        if fd is None:
            return locked
        lock_path = path.with_name(path.name + LOCK_SUFFIX)
        try:
            try:
                records = _load(path)  # re-read under the lock: another process may have consumed meanwhile
            except _Unreadable:
                return "approval_unreadable"
            found = _problem(records.get(approval_ref) if isinstance(approval_ref, str) else None, co_reference,
                             route, source_revision, now)
            if found is not None:
                return found
            records[approval_ref] = {**records[approval_ref], "consumed_at": _stamp(now)}
            _write(path, records)
        finally:
            os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass
    return None


def record_for(path: Path, approval_ref: str) -> dict[str, Any] | None:
    """A copy of one approval record (read only), or None when the file is unreadable or has no such reference."""
    try:
        records = _load(path)
    except _Unreadable:
        return None
    found = records.get(approval_ref) if isinstance(approval_ref, str) else None
    return dict(found) if isinstance(found, dict) else None


def unlock_environ(record: Mapping[str, Any]) -> dict[str, str]:
    """The three approval values session_readiness.production_unlock_problem reads (never the SESSIONS flag)."""
    try:
        issued = _parse(record["issued_at"])
        expires = _parse(record["expires_at"])
        reference = record["approval_ref"]
    except (TypeError, KeyError):
        raise ApprovalRefused("invalid_record") from None
    if issued is None or expires is None or not _valid_ref(reference):
        raise ApprovalRefused("invalid_record")
    return {"SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": reference,
            "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_ISSUED": issued.isoformat(),
            "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES": expires.isoformat()}
