"""Redacted, append-only local record of a production create attempt (owner decision D2).

Every production create (or dry run / refusal / uncertain submit) leaves one small JSON file so the owner can judge
whether a wrong tenant is better renamed or fixed by editing parameters. Only an allowlist of fields is kept, every
string is trimmed and stripped of control characters, and anything that looks like a secret is replaced by
``"[redacted]"`` (counted in ``redactions``). Records are written atomically with private permissions and are never
overwritten or deleted: a name collision gets a counter suffix. Nothing here touches the network.

Layout: ``<state dir>/production-create-records/<CO>-<UTC YYYYMMDDTHHMMSSZ>[-N].json`` (the state dir comes from
``state_paths.state_dir()`` at the call site and is passed in).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECORDS_SUBDIR = "production-create-records"
REDACTED = "[redacted]"
MAX_STRING = 300
MAX_LIST_ITEMS = 50
MAX_DICT_ITEMS = 50

OUTCOMES = ("dry_run_passed", "refused", "created_unverified", "readback_verified", "uncertain_submit",
            "attempt_started", "owner_review_cleared")
EDITABLE_CANDIDATES = ("account_id", "account_uuid", "domains", "plan_summary", "tenant_name")

_SCALAR_FIELDS = ("route", "approval_ref", "refusal_reason", "tenant_name", "account_id", "account_uuid",
                  "plan_summary", "salesforce_revision", "search_control", "operator")
_LIST_FIELDS = ("domains", "response_shape", "readback_mismatches", "payload_mismatches")
ADVANCED_SECTION_VALUES = ("found", "not_found")
ADVANCED_CONTROL_STATES = ("on", "off", "absent")
_ALLOWED = frozenset({"co_reference", "outcome", "recorded_at", "redactions", "form_fields_checked",
                      "advanced_section", "advanced_controls", "advanced_mismatches",
                      *_SCALAR_FIELDS, *_LIST_FIELDS})
_CO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
_STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
_KEYWORD_RE = re.compile(r"bearer |password|cookie|authorization|mfa|token", re.IGNORECASE)
_JWT_RE = re.compile(r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_+/=-]{10,}")
_BLOB_RE = re.compile(r"[A-Za-z0-9+/=_-]{40,}")
# Machine names (not free text): reason codes and field/response-field names are matched against a strict shape
# instead of the secret keywords, so "approval_token_missing" and "access_token" survive. A field name that also
# looks like a JWT or a long opaque blob is still redacted.
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{2,80}")
_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_PAYLOAD_NAME_RE = re.compile(r"(?:unexpected:)?[A-Za-z0-9_.-]{1,80}")  # a create-body field name, ``unexpected:`` prefix allowed


class ProductionCreateRecordError(Exception):
    """A record could not be built, written, or trusted (fail closed)."""


def _looks_secret(text: str) -> bool:
    # 32-hex account_uuid and 24/32-hex account_id are shorter than the 40-char blob rule, so they never trip it.
    return bool(_KEYWORD_RE.search(text) or _JWT_RE.search(text) or _BLOB_RE.search(text))


def _clean(value: Any, counter: list[int], allow_int: bool = True) -> str | None:
    """A trimmed, control-free string, ``[redacted]`` if secret-like, or None when ``value`` is not a scalar."""
    if isinstance(value, bool) or not isinstance(value, (str, int) if allow_int else str):
        return None
    text = _CONTROL_RE.sub(" ", str(value)).strip()
    # Check the full text first so truncation cannot hide a secret straddling the cut.
    if _looks_secret(text):
        counter[0] += 1
        return REDACTED
    return text[:MAX_STRING].strip()


def _clean_name(value: Any, counter: list[int]) -> str | None:
    """A field/response-field name: strict shape only (no keyword rule). None when not a string."""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if _NAME_RE.fullmatch(text) and not (_JWT_RE.search(text) or _BLOB_RE.search(text)):
        return text
    counter[0] += 1
    return REDACTED


def _clean_payload_name(value: Any, counter: list[int]) -> str | None:
    """A create-body field name from the payload comparison (strict shape; optional ``unexpected:`` prefix)."""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if _PAYLOAD_NAME_RE.fullmatch(text) and not (_JWT_RE.search(text) or _BLOB_RE.search(text)):
        return text
    counter[0] += 1
    return REDACTED


def _clean_reason(value: Any, counter: list[int]) -> str | None:
    """A refusal reason code: ``^[a-z][a-z0-9_]{2,80}$`` or ``[redacted]``. None when not a string."""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if _REASON_RE.fullmatch(text):
        return text
    counter[0] += 1
    return REDACTED


def _clean_free(value: Any, counter: list[int]) -> str | None:
    return _clean(value, counter, False)


def _sanitize(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    counter = [0]
    out: dict[str, Any] = {}
    for name in _SCALAR_FIELDS:
        if name in data:
            cleaned = (_clean_reason(data[name], counter) if name == "refusal_reason"
                       else _clean(data[name], counter))
            if cleaned is not None:
                out[name] = cleaned
    for name in _LIST_FIELDS:
        value = data.get(name)
        if isinstance(value, (list, tuple)):
            clean_item = (_clean_payload_name if name == "payload_mismatches"
                          else _clean_name if name in ("response_shape", "readback_mismatches") else _clean_free)
            items = [clean_item(v, counter) for v in list(value)[:MAX_LIST_ITEMS]]
            out[name] = [i for i in items if i is not None]
    checked = data.get("form_fields_checked")
    if isinstance(checked, dict):
        fields: dict[str, str] = {}
        for key, verdict in list(checked.items())[:MAX_DICT_ITEMS]:
            cleaned_key = _clean_name(key, counter)
            if cleaned_key:
                fields[cleaned_key] = verdict if verdict in ("pass", "fail") else "fail"
        out["form_fields_checked"] = fields
    # Production dry run: whether the Advanced section was found, each advanced control's name with on/off/absent
    # (never a value), and the names whose state differs from the Dev target or that are absent.
    if data.get("advanced_section") in ADVANCED_SECTION_VALUES:
        out["advanced_section"] = data["advanced_section"]
    controls = data.get("advanced_controls")
    if isinstance(controls, dict):
        states: dict[str, str] = {}
        for key, state in list(controls.items())[:MAX_DICT_ITEMS]:
            cleaned_key = _clean_name(key, counter)
            if cleaned_key and cleaned_key != REDACTED and state in ADVANCED_CONTROL_STATES:
                states[cleaned_key] = state
        out["advanced_controls"] = states
    mismatches = data.get("advanced_mismatches")
    if isinstance(mismatches, (list, tuple)):
        names = [_clean_name(v, counter) for v in list(mismatches)[:MAX_LIST_ITEMS]]
        out["advanced_mismatches"] = [n for n in names if n and n != REDACTED]
    return out, counter[0]


def _records_dir(state_dir: str | os.PathLike[str]) -> Path:
    return Path(state_dir) / RECORDS_SUBDIR


def _utc(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ProductionCreateRecordError("now_must_be_timezone_aware")
    return now.astimezone(timezone.utc)


def _write_new(directory: Path, base: str, payload: bytes) -> Path:
    """Write ``payload`` to a new ``base[-N].json`` without ever replacing an existing file."""
    temp = directory / f".{uuid.uuid4().hex}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        counter = 1
        while counter < 10000:
            final = directory / (f"{base}.json" if counter == 1 else f"{base}-{counter}.json")
            try:
                os.link(temp, final)  # fails if ``final`` exists: atomic and never overwrites
                return final
            except FileExistsError:
                counter += 1
            except OSError:
                if final.exists():
                    counter += 1
                    continue
                raise
        raise ProductionCreateRecordError("record_name_space_exhausted")
    finally:
        try:
            temp.unlink()  # our own temp file only
        except OSError:
            pass


def record(state_dir: str | os.PathLike[str], data: dict[str, Any], now: datetime) -> Path:
    """Persist one redacted record and return its path. Raises ``ProductionCreateRecordError`` on bad input."""
    moment = _utc(now)
    if not isinstance(data, dict):
        raise ProductionCreateRecordError("record_data_must_be_object")
    co = data.get("co_reference")
    if not isinstance(co, str) or not _CO_RE.fullmatch(co.strip()) or _looks_secret(co):
        raise ProductionCreateRecordError("invalid_co_reference")
    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise ProductionCreateRecordError("invalid_outcome")
    body, redactions = _sanitize(data)
    body.update(co_reference=co.strip(), outcome=outcome, redactions=redactions,
                recorded_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
    payload = (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    directory = _records_dir(state_dir)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return _write_new(directory, f"{co.strip()}-{moment.strftime('%Y%m%dT%H%M%SZ')}", payload)
    except OSError as exc:
        raise ProductionCreateRecordError("record_write_failed") from exc


def _validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - _ALLOWED:
        raise ProductionCreateRecordError("record_unexpected_shape")
    co, outcome, stamp, redactions = (raw.get(k) for k in ("co_reference", "outcome", "recorded_at", "redactions"))
    if not (isinstance(co, str) and _CO_RE.fullmatch(co) and outcome in OUTCOMES and isinstance(stamp, str)
            and _STAMP_RE.fullmatch(stamp) and isinstance(redactions, int) and not isinstance(redactions, bool)
            and redactions >= 0):
        raise ProductionCreateRecordError("record_invalid_fields")
    for name in _SCALAR_FIELDS:
        if name in raw and not isinstance(raw[name], str):
            raise ProductionCreateRecordError("record_invalid_fields")
    for name in _LIST_FIELDS:
        if name in raw and not (isinstance(raw[name], list) and all(isinstance(v, str) for v in raw[name])):
            raise ProductionCreateRecordError("record_invalid_fields")
    checked = raw.get("form_fields_checked")
    if "form_fields_checked" in raw and not (
            isinstance(checked, dict) and all(isinstance(k, str) and v in ("pass", "fail") for k, v in checked.items())):
        raise ProductionCreateRecordError("record_invalid_fields")
    # Nothing on disk may differ from what the sanitizer would have written (control chars, secrets, over-length).
    clean, _ = _sanitize(raw)
    if any(raw.get(k) != v for k, v in clean.items()) or any(
            k not in clean and k not in ("co_reference", "outcome", "recorded_at", "redactions") for k in raw):
        raise ProductionCreateRecordError("record_failed_redaction_check")
    return raw


def load(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read and validate a record; any corruption or unexpected content raises (fail closed)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProductionCreateRecordError("record_unreadable") from exc
    return _validate(raw)


def list_records(state_dir: str | os.PathLike[str], co_reference: str | None = None) -> list[Path]:
    """Record paths oldest first (by recorded time), optionally only one CO. A corrupt record raises."""
    directory = _records_dir(state_dir)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(p for p in directory.iterdir() if p.suffix == ".json" and not p.name.startswith(".")):
        content = load(path)
        if co_reference is None or content["co_reference"] == co_reference:
            found.append((content["recorded_at"], path.name, path))
    return [path for _, _, path in sorted(found)]


def editable_parameters(record_data: dict[str, Any]) -> list[str]:
    """Sorted names of parameters present (non-empty, not redacted) that the owner could fix by edit."""
    names = []
    for name in EDITABLE_CANDIDATES:
        value = record_data.get(name)
        if isinstance(value, list):
            value = [v for v in value if v and v != REDACTED]
        if value and value != REDACTED:
            names.append(name)
    return sorted(names)
