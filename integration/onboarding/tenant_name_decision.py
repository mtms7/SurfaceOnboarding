"""The confirmed short tenant name (owner decision 2026-10-21, docs/60): one local record per CO, bound to the Salesforce account name.

A long account name (over 40 characters) must be confirmed by the owner before a production dry run or create. The confirmed name keeps
the first two words of the account name and has at most 40 characters. The record stores the confirmed name, a hash of the account name
it was made for (so a changed account name invalidates it), the operator and the time; never the account name itself. Fail closed: a
corrupt or unreadable file raises ``TenantNameUnavailable``; nothing is ever guessed. No network, no browser, no Salesforce call.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
from typing import Any, NamedTuple

from . import account_name_shortener

STATE_FILE_NAME = "onboarding_tenant_names.json"
LIMIT = account_name_shortener.LIMIT
KEEP = account_name_shortener.KEEP
REFERENCE = re.compile(r"CO-[0-9]{4,10}")
OPERATOR = re.compile(r"[^\s<>'\"]{1,254}")
RECORD_FIELDS = frozenset({"confirmed_name", "full_name_hash", "operator", "confirmed_at"})
_lock = Lock()


class TenantNameRefused(ValueError):
    """The requested name or request is invalid; the message is a stable code."""


class TenantNameUnavailable(RuntimeError):
    """The record file cannot be read or is corrupt: no name may be treated as confirmed."""


class Resolved(NamedTuple):
    name: str
    confirmed: bool
    needs_confirmation: bool
    unavailable: bool = False


def _clean(name: Any) -> str:
    if not isinstance(name, str) or re.search(r"[\x00-\x1f\x7f]", name) or not " ".join(name.split()):
        raise TenantNameRefused("tenant_name_invalid")
    return " ".join(name.split())


def needs_confirmation(full_name: str) -> bool:
    return len(" ".join(str(full_name).split())) > LIMIT


def validate(full_name: str, name: Any) -> str:
    """The cleaned name, or ``TenantNameRefused`` (``tenant_name_invalid`` / ``_first_words_changed`` / ``_too_long``)."""
    full, cleaned = _clean(full_name), _clean(name)
    if cleaned.split()[:KEEP] != full.split()[:KEEP] or (len(cleaned.split()) < KEEP and len(full.split()) >= KEEP):
        raise TenantNameRefused("tenant_name_first_words_changed")
    if len(cleaned) > LIMIT:
        raise TenantNameRefused("tenant_name_too_long")
    return cleaned


def _hash(full_name: str) -> str:
    return hashlib.sha256(_clean(full_name).encode("utf-8")).hexdigest()


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timezone-aware time required")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise TenantNameUnavailable("tenant_names_unreadable") from error
    try:
        raw = json.loads(text)
    except ValueError as error:
        raise TenantNameUnavailable("tenant_names_corrupt") from error
    if not isinstance(raw, dict):
        raise TenantNameUnavailable("tenant_names_corrupt")
    for reference, record in raw.items():
        if (not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(record, dict)
                or set(record) != RECORD_FIELDS or not all(isinstance(value, str) and value for value in record.values())):
            raise TenantNameUnavailable("tenant_names_corrupt")
    return raw


def confirmed_name(path: Path, co_reference: str, full_name: str) -> str | None:
    """The confirmed name for this CO and this account name, or None. Raises ``TenantNameUnavailable`` for an unreadable file."""
    record = _load(path).get(co_reference)
    if record is None or record["full_name_hash"] != _hash(full_name):
        return None
    try:
        return validate(full_name, record["confirmed_name"])
    except TenantNameRefused as error:
        raise TenantNameUnavailable("tenant_names_corrupt") from error


def confirm(path: Path, co_reference: str, full_name: str, name: Any, operator: str, now: datetime) -> str:
    """Record the owner's confirmed name for one CO. Returns the cleaned name; never overwrites a file it cannot read."""
    if not isinstance(co_reference, str) or not REFERENCE.fullmatch(co_reference):
        raise TenantNameRefused("invalid_co_reference")
    if not isinstance(operator, str) or not OPERATOR.fullmatch(operator):
        raise TenantNameRefused("invalid_operator")
    cleaned = validate(full_name, name)
    with _lock:
        records = _load(path)
        records[co_reference] = {"confirmed_name": cleaned, "full_name_hash": _hash(full_name), "operator": operator,
                                 "confirmed_at": _stamp(now)}
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(target.parent), prefix=".tenant-names-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(records, stream, sort_keys=True, indent=1)
            os.replace(temp, target)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
    return cleaned


def resolve(path: Path, co_reference: str, full_name: str) -> Resolved:
    """What the tenant name is for this CO: the confirmed name, or the full account name (with the confirmation flag)."""
    full = _clean(full_name)
    needs = needs_confirmation(full)
    if not needs:
        return Resolved(full, False, False)
    try:
        name = confirmed_name(path, co_reference, full)
    except TenantNameUnavailable:
        return Resolved(full, False, True, True)
    if name is None:
        return Resolved(full, False, True)
    return Resolved(name, True, False)
