"""Validate and normalise one Redash saved-query result into Leonardo-shaped tenant rows.

Source (owner approval 2026-10-05): saved query 251, "Surface Onboarding |
Tenant inventory (read-only)", on the Redash data source "Prod (Cloned) -
Mgmt". The query itself projects only allow-listed fields (no user names,
emails, phones, or operator ids). This module opens no connection; the
transport lives in ``tools/redash_inventory_collector.py``.

Everything fails closed with a stable, value-free ``InventoryError`` reason:
an unexpected result shape, a missing required column, an unreadable date,
a row without an identity. The rows then go through the same
``snapshot_payload`` allow-list, integrity hash and drift check as the
Leonardo export, so a snapshot from either source has one format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from .leonardo_inventory import EXPECTED_PATHS, REDASH_MAX_TOTAL, InventoryError

QUERY_ID = 251
MAX_OPERATOR_COUNT = 1000

# Redash column -> row path (Leonardo's own row shape, so minimize_row applies unchanged).
_RENAMED = {"_id": "id", "operatorCount": "operatorAccounts"}
# Required for this source: identity, the domains the duplicate check compares, and the scan status.
REQUIRED_COLUMNS = ("_id", "accountName", "accountUuid", "accountDomain", "alternateDomains", "lastScanStatusEnum")
_SAFE_COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}")
TIMESTAMP_RANGE_MS = (-10 ** 12, 10 ** 13)  # ~1938 to ~2286; anything outside is garbage, not a date
DATE_COLUMNS = frozenset({"_created", "_modified", "lastReconScan",
                          "accountLicense.startDate", "accountLicense.expirationDate"})
INT_COLUMNS = frozenset({"lastReconScanDurationMilliseconds", "operatorCount", "accountLicense.assetsNumber",
                         "accountLicense.domainsNumber", "accountLicense.subDomainsNumber",
                         "accountLicense.leakedCredentialsScannedDomainsNumber"})
PASSTHROUGH_COLUMNS = frozenset({
    "accountUuid", "accountName", "accountDomain", "accountType", "accountSubtype", "enabled", "isDeleted",
    "scanningInterval", "lastScanStatusEnum", "accountLicense.licenseType", "accountLicense.enabled",
    "accountLicense.scanningFrequency", "accountLicense.leakedCredentialsAllowed"})
LIST_COLUMNS = frozenset({"alternateDomains"})
# Not in every tenant document (absent in the clone's results so far): never reported as drift or missing.
UNREPORTED_COLUMNS = frozenset({"accountSubtype"})
# Known and deliberately not stored (the Leonardo export's allow-list has no such field).
IGNORED_COLUMNS = frozenset({"leakedCredentialsSettings.spyCloudSettings",
                             "leakedCredentialsSettings.spyCloudSettings.enabled"})
# Owner decisions 2026-10-05 / 2026-10-08 (SpyCloud ON is the default, informational): only the ``enabled``
# boolean is kept from the SpyCloud settings (see ``leonardo_inventory.spycloud_enabled``).
# Query 251 returns the object column (or, flattened, its ``.enabled`` column); both are read for that one boolean and nothing else is stored.
SPYCLOUD_OBJECT_COLUMN = "leakedCredentialsSettings.spyCloudSettings"
SPYCLOUD_COLUMN = SPYCLOUD_OBJECT_COLUMN + ".enabled"
KNOWN_COLUMNS = (frozenset(REQUIRED_COLUMNS) | DATE_COLUMNS | INT_COLUMNS | PASSTHROUGH_COLUMNS | LIST_COLUMNS
                 | IGNORED_COLUMNS)
OPTIONAL_COLUMNS = KNOWN_COLUMNS - frozenset(REQUIRED_COLUMNS)
_LICENSE_PREFIX = "accountLicense."


def _row_path(column: str) -> str:
    return _RENAMED.get(column, column)


def _stored_paths() -> frozenset[str]:
    paths: set[str] = set()
    paths.update({"leakedCredentialsSettings", "leakedCredentialsSettings.spyCloudSettings",
                  SPYCLOUD_COLUMN})
    for column in KNOWN_COLUMNS - IGNORED_COLUMNS:
        path = _row_path(column)
        paths.add(path)
        if path.startswith(_LICENSE_PREFIX):
            paths.add("accountLicense")
    return frozenset(paths)


# The drift check for this source only compares what the query projects.
EXPECTED_REDASH_PATHS: dict[str, frozenset[str]] = {
    path: types for path, types in EXPECTED_PATHS.items()
    if path in _stored_paths() and path not in UNREPORTED_COLUMNS}


@dataclass(frozen=True, slots=True)
class NormalizedResult:
    rows: tuple[dict[str, Any], ...]
    retrieved_at: datetime
    latest_modified: datetime | None
    unexpected_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]


def parse_timestamp_ms(value: Any) -> int | None:
    """Epoch milliseconds (UTC) from an ISO string, a number, or None; anything else is refused."""
    if value is None:
        return None
    if type(value) is bool:
        raise InventoryError("redash_date_unparseable")
    if type(value) in (int, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise InventoryError("redash_date_unparseable")
        number = int(value)
        stamp = number if abs(number) >= 10 ** 11 else number * 1000  # seconds vs milliseconds
        if not TIMESTAMP_RANGE_MS[0] <= stamp <= TIMESTAMP_RANGE_MS[1]:
            raise InventoryError("redash_date_unparseable")
        return stamp
    if type(value) is str:
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            stamp = int(parsed.timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            raise InventoryError("redash_date_unparseable") from None
        if not TIMESTAMP_RANGE_MS[0] <= stamp <= TIMESTAMP_RANGE_MS[1]:
            raise InventoryError("redash_date_unparseable")
        return stamp
    raise InventoryError("redash_date_unparseable")


def _whole_number(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is bool:
        raise InventoryError("redash_schema_unavailable")
    if type(value) is float and value.is_integer():
        return int(value)
    if type(value) is int:
        return value
    raise InventoryError("redash_schema_unavailable")


def _domain_list(value: Any) -> list[Any] | None:
    if value is None or type(value) is list:
        return value
    if type(value) is str:
        try:
            parsed = json.loads(value)
        except ValueError:
            raise InventoryError("redash_schema_unavailable") from None
        if type(parsed) is list:
            return parsed
    raise InventoryError("redash_schema_unavailable")


def _flat(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Redash names nested fields with dots; also accept a real nested ``accountLicense`` object."""
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "accountLicense" and isinstance(value, dict):
            flat.update({_LICENSE_PREFIX + name: item for name, item in value.items()})
        elif type(key) is str:
            flat[key] = value
    return flat


def _spycloud_flag(column: str, value: Any) -> bool | None:
    """The ``enabled`` boolean from the SpyCloud object (dict or JSON text) or its flat column, else None."""
    if column == SPYCLOUD_OBJECT_COLUMN:
        if type(value) is str:
            try:
                value = json.loads(value)
            except ValueError:
                return None
        value = value.get("enabled") if isinstance(value, dict) else None
    return value if type(value) is bool else None


def _build_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"accountUuid": None, "accountLicense": None}
    license_: dict[str, Any] = {}
    for column, value in _flat(raw).items():
        if column in (SPYCLOUD_OBJECT_COLUMN, SPYCLOUD_COLUMN):
            enabled = _spycloud_flag(column, value)
            if enabled is not None:  # anything but a real boolean stays "unknown", never guessed
                row["leakedCredentialsSettings"] = {"spyCloudSettings": {"enabled": enabled}}
            continue
        if column not in KNOWN_COLUMNS or column in IGNORED_COLUMNS:
            continue
        if column in DATE_COLUMNS:
            value = parse_timestamp_ms(value)
        elif column in INT_COLUMNS:
            value = _whole_number(value)
        elif column in LIST_COLUMNS:
            value = _domain_list(value)
        if column == "operatorCount":  # only "is an operator assigned" is kept, never who
            if value is not None and not 0 <= value <= MAX_OPERATOR_COUNT:
                raise InventoryError("redash_schema_unavailable")
            value = None if value is None else (["operator"] if value else [])
        elif column == "_id":
            value = value if type(value) is str and value else None
        if column.startswith(_LICENSE_PREFIX):
            license_[column[len(_LICENSE_PREFIX):]] = value
        else:
            row[_row_path(column)] = value
    if any(item is not None for item in license_.values()):
        row["accountLicense"] = license_
    return row


def normalize(result: Any) -> NormalizedResult:
    """Turn a Redash ``{"query_result": {"data": {...}, "retrieved_at": ...}}`` reply into tenant rows."""
    query_result = result.get("query_result") if isinstance(result, dict) else None
    data = query_result.get("data") if isinstance(query_result, dict) else None
    columns = data.get("columns") if isinstance(data, dict) else None
    table = data.get("rows") if isinstance(data, dict) else None
    if type(columns) is not list or type(table) is not list or not isinstance(query_result.get("retrieved_at"), str):
        raise InventoryError("redash_schema_unavailable")
    if len(table) > REDASH_MAX_TOTAL:
        raise InventoryError("inventory_too_large")  # before any row is processed
    names = [column.get("name") for column in columns if isinstance(column, dict)]
    if len(names) != len(columns) or not all(type(name) is str for name in names):
        raise InventoryError("redash_schema_unavailable")
    if any(required not in names for required in REQUIRED_COLUMNS):
        raise InventoryError("redash_schema_unavailable")
    retrieved_ms = parse_timestamp_ms(query_result["retrieved_at"])
    if retrieved_ms is None:
        raise InventoryError("redash_schema_unavailable")
    rows = []
    for raw in table:
        if not isinstance(raw, dict):
            raise InventoryError("redash_schema_unavailable")
        rows.append(_build_row(raw))
    stamps = [row["_modified"] for row in rows if type(row.get("_modified")) is int]
    latest = max(stamps) if stamps else None
    try:
        retrieved_at = datetime.fromtimestamp(retrieved_ms / 1000, tz=timezone.utc)
        latest_modified = datetime.fromtimestamp(latest / 1000, tz=timezone.utc) if latest is not None else None
    except (OverflowError, OSError, ValueError):
        raise InventoryError("redash_date_unparseable") from None
    unexpected = sorted(name if _SAFE_COLUMN.fullmatch(name) else "<column>"
                        for name in names if name not in KNOWN_COLUMNS)
    return NormalizedResult(
        tuple(rows), retrieved_at, latest_modified,
        tuple(unexpected)[:20],
        tuple(sorted(name for name in OPTIONAL_COLUMNS - IGNORED_COLUMNS - UNREPORTED_COLUMNS
                     if name not in names)))


def source_meta(normalized: NormalizedResult, collected_at: datetime, *, refreshed: bool) -> dict[str, Any]:
    """Provenance stored in the snapshot (no row data): which query, how old the data is, what drifted."""
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "kind": "redash", "query_id": QUERY_ID, "data_source": "Prod (Cloned) - Mgmt",
        "retrieved_at": normalized.retrieved_at.strftime(stamp),
        "collected_at": collected_at.astimezone(timezone.utc).strftime(stamp),
        "data_latest_modified": normalized.latest_modified.strftime(stamp) if normalized.latest_modified else None,
        "refreshed_this_run": bool(refreshed),
        "unexpected_columns": list(normalized.unexpected_columns),
        "missing_columns": list(normalized.missing_columns),
    }
