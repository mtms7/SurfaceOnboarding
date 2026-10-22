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
# Owner decision 2026-10-20: CO-0800 (Surface & Credential Exposure New = Case 3) may be approved too. One CO, 7 days, revision
# bound, consumed once, exactly as CE-only; the CREATE gates stay CE-only (they compare against ALLOWED_ROUTE).
CASE3_ROUTE = EngineValue.CASE_3.value
CASE3_SCOPE = "case3_single_co"
ROUTE_SCOPES = {ALLOWED_ROUTE: SCOPE, CASE3_ROUTE: CASE3_SCOPE}


def scope_for(route: Any) -> str | None:
    """The scope an approval of ``route`` must carry, or None for a route that cannot be approved."""
    return ROUTE_SCOPES.get(route) if isinstance(route, str) else None
DEFAULT_LIFETIME = timedelta(days=7)
REQUIRED_FIELDS = frozenset({"approval_ref", "co_reference", "route", "scope", "source_revision", "issued_by",
                             "issued_at", "expires_at"})
# Path B step (b), owner go 2026-10-22: a Case 3 record also carries the owner-confirmed tenant name it was approved for.
RECORD_FIELDS = REQUIRED_FIELDS | {"consumed_at", "superseded_at", "tenant_name"}
TENANT_NAME_MAX = 80
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


def valid_tenant_name(value: Any) -> bool:
    """A clean single-line tenant name: no control characters, no padding or doubled spaces, at most 80 characters."""
    return (isinstance(value, str) and 0 < len(value) <= TENANT_NAME_MAX and value == " ".join(value.split())
            and not re.search(r"[\x00-\x1f\x7f]", value))


def valid_revision(value: Any) -> bool:
    """The one revision validator (shared by the issuer, the record check and the dashboard view)."""
    return isinstance(value, str) and 0 < len(value) <= 128


_valid_revision = valid_revision


def _valid_record(key: Any, record: Any) -> bool:
    return (isinstance(record, dict) and REQUIRED_FIELDS <= set(record) <= RECORD_FIELDS
            and _valid_ref(key) and record["approval_ref"] == key and _valid_co(record["co_reference"])
            and scope_for(record["route"]) is not None and record["scope"] == scope_for(record["route"])
            and _valid_revision(record["source_revision"])
            and ("tenant_name" not in record
                 or (record["route"] == CASE3_ROUTE and valid_tenant_name(record["tenant_name"])))
            and isinstance(record["issued_by"], str) and ISSUER.fullmatch(record["issued_by"]) is not None
            and _parse(record["issued_at"]) is not None and _parse(record["expires_at"]) is not None
            and ("consumed_at" not in record or _parse(record["consumed_at"]) is not None)
            and ("superseded_at" not in record or _parse(record["superseded_at"]) is not None))


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
          allowed_issuers: frozenset[str], now: datetime, lifetime: timedelta = DEFAULT_LIFETIME,
          tenant_name: str | None = None) -> dict[str, Any]:
    """Record a one-CO production approval (CE-only or Case 3). Raises ApprovalRefused(reason) on anything invalid.

    A Case 3 approval needs the confirmed ``tenant_name`` it is bound to; a CE-only approval takes none."""
    if scope_for(route) is None:
        raise ApprovalRefused("route_not_ce_only")
    if route == CASE3_ROUTE:
        if not valid_tenant_name(tenant_name):
            raise ApprovalRefused("invalid_tenant_name")
    elif tenant_name is not None:
        raise ApprovalRefused("tenant_name_not_allowed")
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
    record = {"approval_ref": approval_ref, "co_reference": co_reference, "route": route, "scope": scope_for(route),
              "source_revision": source_revision, "issued_by": issued_by, "issued_at": _stamp(moment),
              "expires_at": _stamp(moment + lifetime)}
    if route == CASE3_ROUTE:
        record["tenant_name"] = tenant_name
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ApprovalRefused("approval_unreadable") from None
        fd, locked = _acquire_file_lock(path)  # the same cross-process lock consume takes
        if fd is None:
            raise ApprovalRefused(locked or "approval_locked")
        lock_path = path.with_name(path.name + LOCK_SUFFIX)
        try:
            try:
                records = _load(path)  # re-read under the lock: never write back a stale copy (lost consumed_at)
            except _Unreadable:
                raise ApprovalRefused("approval_unreadable") from None
            if approval_ref in records:
                raise ApprovalRefused("approval_ref_reused")
            superseded = []
            for key, other in records.items():
                expires = _parse(other["expires_at"])
                if "consumed_at" in other or "superseded_at" in other or expires is None or expires <= moment:
                    continue  # consumed, superseded or expired: not live
                if other["co_reference"] != co_reference:
                    raise ApprovalRefused("other_approval_active")
                if other["source_revision"] == source_revision:
                    raise ApprovalRefused("approval_already_active_for_co")
                superseded.append(key)  # bound to an older revision: it can no longer pass the gate
            for key in superseded:
                records[key] = {**records[key], "superseded_at": _stamp(moment)}
            records[approval_ref] = record
            _write(path, records)
        finally:
            os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass
    return dict(record)


def _name_problem(record: Mapping[str, Any], tenant_name: Any) -> str | None:
    """The record's bound tenant name against the name the create would type (Case 3 records only)."""
    bound = record.get("tenant_name")
    if not valid_tenant_name(bound):
        return "approval_tenant_name_missing"
    return None if valid_tenant_name(tenant_name) and tenant_name == bound else "approval_tenant_name_drift"


def _problem(record: Mapping[str, Any] | None, co_reference: Any, route: Any, source_revision: Any,
             now: Any, tenant_name: Any = None, require_name: bool = False) -> str | None:
    if record is None:
        return "approval_missing"
    if record.get("consumed_at"):
        return "approval_consumed"
    if record.get("superseded_at"):
        return "approval_superseded"
    issued = _parse(record.get("issued_at"))
    expires = _parse(record.get("expires_at"))
    if not _aware(now) or issued is None or expires is None:
        return "approval_expired"
    moment = now.astimezone(timezone.utc)
    if not issued <= moment < expires:
        return "approval_expired"
    if record.get("co_reference") != co_reference:
        return "approval_co_mismatch"
    if record.get("route") != route or scope_for(route) is None or record.get("scope") != scope_for(route):
        return "approval_route_mismatch"
    if record.get("source_revision") != source_revision:
        return "approval_revision_drift"
    if route == CASE3_ROUTE and (require_name or tenant_name is not None):
        if tenant_name is None:
            return "approval_tenant_name_missing"  # the caller named no tenant: a Case 3 approval is never consumed blind
        return _name_problem(record, tenant_name)
    return None


def tenant_name_problem(path: Path, approval_ref: str, co_reference: str, route: str, tenant_name: Any) -> str | None:
    """None when the Case 3 approval is bound to exactly ``tenant_name``; else a reason code (fails closed). Read only."""
    try:
        records = _load(path)
    except _Unreadable:
        return "approval_unreadable"
    record = records.get(approval_ref) if isinstance(approval_ref, str) else None
    if record is None:
        return "approval_missing"
    if record.get("co_reference") != co_reference:
        return "approval_co_mismatch"
    if route != CASE3_ROUTE or record.get("route") != CASE3_ROUTE:
        return "approval_route_mismatch"
    return _name_problem(record, tenant_name)


def check(path: Path, approval_ref: str, co_reference: str, route: str, source_revision: str,
          now: datetime, *, tenant_name: str | None = None) -> str | None:
    """None when the approval is valid and unused for exactly this CO, route and revision; else a reason code.

    The bound tenant name of a Case 3 approval is compared only when ``tenant_name`` is given."""
    try:
        records = _load(path)
    except _Unreadable:
        return "approval_unreadable"
    return _problem(records.get(approval_ref) if isinstance(approval_ref, str) else None, co_reference, route,
                    source_revision, now, tenant_name)


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
            now: datetime, *, tenant_name: str | None = None) -> str | None:
    """Validate and mark the approval used (one approval, one create). None on success, else a reason code.

    A Case 3 approval is consumed only for exactly its bound ``tenant_name`` (none given: refused)."""
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
                             route, source_revision, now, tenant_name, require_name=True)
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


def revocable_problem(record: Mapping[str, Any] | None, now: datetime) -> str | None:
    """None when ``record`` is a live approval (not consumed, superseded or expired at ``now``); else the reason code."""
    if record is None:
        return "approval_missing"
    if record.get("consumed_at"):
        return "approval_consumed"
    if record.get("superseded_at"):
        return "approval_superseded"
    expires = _parse(record.get("expires_at"))
    if expires is None or expires <= now.astimezone(timezone.utc):
        return "approval_expired"
    return None


def revoke(path: Path, approval_ref: str, now: datetime) -> dict[str, Any]:
    """Retire ONE live approval (owner request 2026-10-22): sets ``superseded_at`` and changes nothing else.

    Same cross-process lock and atomic write as ``issue`` / ``consume``. Raises ``ApprovalRefused(reason)`` for an unreadable file,
    an unknown, consumed, superseded or expired approval, a naive time or a held lock; nothing is written then.
    """
    if not _valid_ref(approval_ref):
        raise ApprovalRefused("invalid_approval_ref")
    if not _aware(now):
        raise ApprovalRefused("naive_datetime")
    moment = now.astimezone(timezone.utc).replace(microsecond=0)
    with _lock:
        fd, locked = _acquire_file_lock(path)
        if fd is None:
            raise ApprovalRefused(locked or "approval_locked")
        lock_path = path.with_name(path.name + LOCK_SUFFIX)
        try:
            try:
                records = _load(path)  # re-read under the lock
            except _Unreadable:
                raise ApprovalRefused("approval_unreadable") from None
            problem = revocable_problem(records.get(approval_ref), moment)
            if problem is not None:
                raise ApprovalRefused(problem)
            records[approval_ref] = {**records[approval_ref], "superseded_at": _stamp(moment)}
            _write(path, records)
            return dict(records[approval_ref])
        finally:
            os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass
