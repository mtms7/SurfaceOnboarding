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

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from .leonardo_inventory import EXPECTED_PATHS, MAX_ALTERNATE_DOMAINS, REDASH_MAX_TOTAL, InventoryError

QUERY_ID = 251  # the live source and default; see PROFILES for the other selectable query
V2_QUERY_ID = 253  # "Tenant inventory v2 (read-only)": flat, wider projection; used only when selected
SUPPORTED_QUERY_IDS = (QUERY_ID, V2_QUERY_ID)
ROW_SCHEMA_V1, ROW_SCHEMA_V2 = "v1", "v2"  # snapshot payload marker ``row_schema``; absent means v1
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


# ----- query 253 (v2): extra columns, strict schema per query ------------------------------------------
# Column -> kind. Kinds: str, bool, int, interval (str|int), list (list of short strings), enum (UPPER_SNAKE), date (epoch ms). Values are stored in
# ``tenant["v2"]`` (flat snake_case keys, None omitted) next to the unchanged v1 row; no personal data.
V2_COLUMNS: dict[str, str] = {
    "accountCountryCode": "str", "userEmailDomains": "list", "enumerationScanningInterval": "interval",
    "leakedCredentialsScanningInterval": "interval", "operatorCount": "int", "subDomainsCount": "int",
    "additionalNetworksCount": "int", "pendingValidationAssetsCount": "int", "leakedCredentialsScannedDomains": "list",
    "staticOutboundIpEnabled": "bool", "fullNucleiScanEnabled": "bool", "aiEnabled": "bool",
    "webAiAttackerEnabled": "bool", "vodBetaEnabled": "bool", "webDictionaryBruteForceEnabled": "bool",
    "webDorkingEnabled": "bool", "subDomainsReconEnabled": "bool", "authenticatedTestingEnabled": "bool",
    "automatedDiscoveryEnabled": "bool", "domainsMultiAttackStackEnabled": "bool",
    "domainsMultiAttackStackAssetsPerStack": "int", "subDomainsMultiAttackStackEnabled": "bool",
    "subDomainsMultiAttackStackAssetsPerStack": "int", "primaryUserMfaRequired": "bool",
    "primaryUserPresent": "bool", "spyCloudEnabled": "bool",
    # docs/56: the newest Credential Exposure (LEAKED_CREDENTIALS_DISCOVERY) execution of the account, from the
    # campaignExecutions lookup in query 253: status (enum), start and end (dates, epoch ms), and the execution count.
    "lastLeakedCredentialsExecutionStatus": "enum", "lastLeakedCredentialsExecutionStart": "date",
    "lastLeakedCredentialsExecutionEnd": "date", "leakedCredentialsExecutionsCount": "int"}
# accountLicense columns query 253 adds; they feed the v1 row's license (so ``minimize_row`` keeps them) and nothing else.
V2_LICENSE_COLUMNS: dict[str, str] = {
    "accountLicense.reconLevel": "interval", "accountLicense.notificationsAllowed": "bool",
    "accountLicense.multipleUsersAllowed": "bool", "accountLicense.apiAccessAllowed": "bool",
    "accountLicense.phishingEnabled": "bool", "accountLicense.provisioningEnabled": "bool",
    "accountLicense.allowedModules": "list", "accountLicense.elevatedFeaturesEnabled": "bool"}
# Shape limits (review 2026-10-17): PII-shaped free text must never enter the clone, and lists cannot grow the file.
_MAX_LIST_ITEMS = 50
_MAX_TEXT = 128
_HOSTNAME = re.compile(r"[A-Za-z0-9*_.-]{1,253}")  # no '@', no spaces, at most 253 characters
_COUNTRY_CODE = re.compile(r"[A-Z]{2}")
_ENUM = re.compile(r"[A-Za-z0-9_ -]{1,32}")
_STATUS_ENUM = re.compile(r"[A-Z][A-Z_]{0,31}")  # an execution status such as DONE, DONE_WITH_ERRORS, PENDING
# Owner decision 2026-10-19 (option A): a list cell of these two query-253 columns that IS a list but fails the hostname
# bounds (over 50 items, an entry with an "@" or an odd character) becomes unknown (omitted) and is counted; the rest of
# the row and the clone still load. Any other problem, in these or any other column, still refuses the whole result.
WITHHOLDABLE_LIST_COLUMNS = frozenset({"userEmailDomains", "leakedCredentialsScannedDomains"})
MAX_ROW_LIST_CHARS = 1500  # all list items of one row together: bounds the worst-case row so 6,000 rows stay under 32 MB
# Alternate domains have their own bounds (live 2026-10-18: 125 tenants over 50 items, max 1,622 items / ~28,500
# characters in one row, ~567,000 characters in all): per row, and for the whole result so the file stays under 32 MB.
ALT_DOMAINS_MAX_ITEMS = MAX_ALTERNATE_DOMAINS  # the same bound minimize_row keeps, so nothing is cut later
ALT_DOMAINS_MAX_ROW_CHARS = 100_000
ALT_DOMAINS_MAX_TOTAL_CHARS = 1_100_000  # live ~567,000 (was 1,500,000 before the docs/56 columns took 0.7 MB of the budget)
ALT_DOMAINS_MAX_TOTAL_ITEMS = 70_000  # live ~35,900 (was 90,000); each item also costs ~13 bytes of JSON in the snapshot


# The four docs/56 columns keep short stored keys: every byte counts at 6,000 rows under the 32 MB clone limit.
_V2_SHORT_KEYS = {"lastLeakedCredentialsExecutionStatus": "ce_status", "lastLeakedCredentialsExecutionStart": "ce_start",
                  "lastLeakedCredentialsExecutionEnd": "ce_end", "leakedCredentialsExecutionsCount": "ce_count"}


def v2_key(column: str) -> str:
    """Flat snake_case key for a v2 column (``staticOutboundIpEnabled`` -> ``static_outbound_ip_enabled``)."""
    return _V2_SHORT_KEYS.get(column) or re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", column).lower()


V2_KINDS: dict[str, str] = {v2_key(column): kind for column, kind in V2_COLUMNS.items()}
KNOWN_COLUMNS_V2 = KNOWN_COLUMNS | frozenset(V2_COLUMNS) | frozenset(V2_LICENSE_COLUMNS)


@dataclass(frozen=True, slots=True)
class QueryProfile:
    query_id: int
    known: frozenset[str]
    row_schema: str


PROFILES: dict[int, QueryProfile] = {
    QUERY_ID: QueryProfile(QUERY_ID, KNOWN_COLUMNS, ROW_SCHEMA_V1),
    V2_QUERY_ID: QueryProfile(V2_QUERY_ID, KNOWN_COLUMNS_V2, ROW_SCHEMA_V2),
}


def profile_for(query_id: int) -> QueryProfile:
    profile = PROFILES.get(query_id) if type(query_id) is int else None
    if profile is None:
        raise InventoryError("redash_query_id_unsupported")
    return profile


def _v2_value(kind: str, value: Any) -> Any:
    """One typed, null-safe v2 value; a wrong type fails closed (never guessed)."""
    if value is None:
        return None
    if kind == "bool":
        if type(value) is bool:
            return value
    elif kind == "int":
        number = _whole_number(value)
        if number is not None and 0 <= number < 10 ** 12:
            return number
    elif kind == "str":
        if type(value) is str and _COUNTRY_CODE.fullmatch(value):
            return value
    elif kind == "interval":
        if type(value) is str and _ENUM.fullmatch(value):
            return value
        if type(value) is int and 0 <= value < 10 ** 12:
            return value
        if type(value) is float and value == value and abs(value) < 1e12 and value.is_integer() and value >= 0:
            return int(value)
    elif kind == "enum":
        if type(value) is str and _STATUS_ENUM.fullmatch(value):
            return value
    elif kind == "date":
        return parse_timestamp_ms(value)  # refuses a bool or garbage; stored as epoch milliseconds
    elif kind == "list":
        items = _domain_list(value)
        if items is not None:
            return _hostnames(items)
    raise InventoryError("redash_schema_unavailable")


def _hostnames(items: Any) -> list[str]:
    """A bounded list of hostname-shaped strings; anything else refuses (fail closed, no value in the reason)."""
    if type(items) is not list or len(items) > _MAX_LIST_ITEMS             or not all(type(item) is str and _HOSTNAME.fullmatch(item) for item in items):
        raise InventoryError("redash_schema_unavailable")
    return items


def _alternate_domains(items: list[Any]) -> list[str]:
    """Hostname-shaped alternate domains under their own bounds; a comma-joined item is split into its parts first
    (each part must still be a hostname, and at least one must remain). Anything else refuses, no value in the reason."""
    hosts: list[str] = []
    for item in items:
        if type(item) is str and "," in item:
            parts = [part.strip() for part in item.split(",") if part.strip()]
            if not parts:
                raise InventoryError("redash_schema_unavailable")
            hosts.extend(parts)
        else:
            hosts.append(item)
    if (len(hosts) > ALT_DOMAINS_MAX_ITEMS
            or not all(type(host) is str and _HOSTNAME.fullmatch(host) for host in hosts)
            or sum(len(host) for host in hosts) > ALT_DOMAINS_MAX_ROW_CHARS):
        raise InventoryError("redash_schema_unavailable")
    return hosts


def validate_row_schema(payload: Any) -> str:
    """The snapshot's row schema ('v1' when unmarked). v2 tenants must carry only known, correctly typed v2 keys; a
    v1 snapshot must carry none. Raises ``InventoryError('inventory_row_schema_invalid')`` with no value in it."""
    if not isinstance(payload, dict):
        raise InventoryError("inventory_row_schema_invalid")
    schema = payload.get("row_schema", ROW_SCHEMA_V1)
    tenants = payload.get("tenants")
    if schema not in (ROW_SCHEMA_V1, ROW_SCHEMA_V2) or type(tenants) is not list:
        raise InventoryError("inventory_row_schema_invalid")
    source = payload.get("source")
    query_id = source.get("query_id") if isinstance(source, dict) else None
    if query_id is not None:  # the recorded source query and the row schema must agree (253 <-> v2, 251 <-> v1)
        if query_id not in PROFILES or PROFILES[query_id].row_schema != schema:
            raise InventoryError("inventory_row_schema_invalid")
    elif schema == ROW_SCHEMA_V2:
        raise InventoryError("inventory_row_schema_invalid")
    for tenant in tenants:
        if not isinstance(tenant, dict):
            raise InventoryError("inventory_row_schema_invalid")
        if schema == ROW_SCHEMA_V1:
            if "v2" in tenant:
                raise InventoryError("inventory_row_schema_invalid")
            continue
        extra = tenant.get("v2")
        if type(extra) is not dict:
            raise InventoryError("inventory_row_schema_invalid")
        for key, value in extra.items():
            kind = V2_KINDS.get(key)
            try:
                clean = _v2_value(kind, value) if kind is not None and value is not None else None
            except InventoryError:
                clean = None
            if clean is None or type(clean) is not type(value) or clean != value:
                raise InventoryError("inventory_row_schema_invalid")
    return schema


# The drift check for this source only compares what the query projects.
EXPECTED_REDASH_PATHS: dict[str, frozenset[str]] = {
    path: types for path, types in EXPECTED_PATHS.items()
    if path in _stored_paths() and path not in UNREPORTED_COLUMNS}
# Query 253 also fills these license fields (``accountLicense`` itself is already expected).
EXPECTED_REDASH_PATHS_V2: dict[str, frozenset[str]] = {
    **EXPECTED_REDASH_PATHS, **{path: EXPECTED_PATHS[path] for path in V2_LICENSE_COLUMNS}}


def expected_paths(query_id: int = QUERY_ID) -> dict[str, frozenset[str]]:
    return EXPECTED_REDASH_PATHS_V2 if profile_for(query_id).row_schema == ROW_SCHEMA_V2 else EXPECTED_REDASH_PATHS


@dataclass(frozen=True, slots=True)
class NormalizedResult:
    rows: tuple[dict[str, Any], ...]
    retrieved_at: datetime
    latest_modified: datetime | None
    unexpected_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    query_id: int = QUERY_ID
    v2_rows: Mapping[str, dict[str, Any]] = field(default_factory=dict)  # tenant id -> v2 values (query 253 only)
    withheld_cells: Mapping[str, int] = field(default_factory=dict)  # column -> rows whose list cell was withheld (253 only)


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


def _build_row(raw: Mapping[str, Any], profile: QueryProfile | None = None,
               withheld: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(v1 row, v2 values). The v2 values are empty for query 251."""
    profile = profile or PROFILES[QUERY_ID]
    v2_profile = profile.row_schema == ROW_SCHEMA_V2
    row: dict[str, Any] = {"accountUuid": None, "accountLicense": None}
    license_: dict[str, Any] = {}
    v2: dict[str, Any] = {}
    for column, value in _flat(raw).items():
        if v2_profile and column == "spyCloudEnabled":  # query 253's flat SpyCloud flag: the same single boolean
            enabled = _spycloud_flag(column, value)
            if enabled is not None:
                row["leakedCredentialsSettings"] = {"spyCloudSettings": {"enabled": enabled}}
                v2[v2_key(column)] = enabled
            continue
        if column in (SPYCLOUD_OBJECT_COLUMN, SPYCLOUD_COLUMN):
            enabled = _spycloud_flag(column, value)
            if enabled is not None:  # anything but a real boolean stays "unknown", never guessed
                row["leakedCredentialsSettings"] = {"spyCloudSettings": {"enabled": enabled}}
            continue
        if column not in profile.known or column in IGNORED_COLUMNS:
            continue
        if v2_profile and column in V2_COLUMNS:
            try:
                typed = _v2_value(V2_COLUMNS[column], value)
            except InventoryError:
                if column not in WITHHOLDABLE_LIST_COLUMNS or withheld is None:
                    raise
                try:
                    is_list = type(_domain_list(value)) is list
                except InventoryError:
                    is_list = False
                if not is_list:
                    raise  # unparseable text or a non-list stays a refusal
                typed = None
                withheld.append(column)
            if typed is not None:
                v2[v2_key(column)] = typed
            if column != "operatorCount":  # operatorCount also keeps feeding the v1 "operator assigned" flag
                continue
        if v2_profile and column in V2_LICENSE_COLUMNS:
            license_[column[len(_LICENSE_PREFIX):]] = _v2_value(V2_LICENSE_COLUMNS[column], value)
            continue
        if column in DATE_COLUMNS:
            value = parse_timestamp_ms(value)
        elif column in INT_COLUMNS:
            value = _whole_number(value)
            if value is not None and abs(value) >= 10 ** 15:
                raise InventoryError("redash_schema_unavailable")
        elif column in LIST_COLUMNS:
            value = _domain_list(value)
            if value is not None:
                value = _alternate_domains(value)
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
    return row, v2


def failing_columns(result: Any, query_id: int = V2_QUERY_ID) -> dict[str, int]:
    """Per column, how many rows fail that column's own check (names from the known list, counts only: no value, no row).

    A diagnostic for the probe after a ``redash_schema_unavailable`` refusal; it never raises."""
    try:
        profile = profile_for(query_id)
        table = result["query_result"]["data"]["rows"]
    except (InventoryError, KeyError, TypeError):
        return {}
    counts: dict[str, int] = {}
    for raw in table if type(table) is list else ():
        if not isinstance(raw, dict):
            continue
        for column, value in _flat(raw).items():
            if column not in profile.known or column in IGNORED_COLUMNS:
                continue
            try:
                if column in V2_COLUMNS:
                    _v2_value(V2_COLUMNS[column], value)
                elif column in V2_LICENSE_COLUMNS:
                    _v2_value(V2_LICENSE_COLUMNS[column], value)
                elif column in DATE_COLUMNS:
                    parse_timestamp_ms(value)
                elif column in INT_COLUMNS:
                    _whole_number(value)
                elif column in LIST_COLUMNS:
                    items = _domain_list(value)
                    if items is not None:
                        _alternate_domains(items)
            except (InventoryError, OverflowError, ValueError, TypeError):
                counts[column] = counts.get(column, 0) + 1
    return dict(sorted(counts.items()))


def failing_list_reasons(result: Any, query_id: int = V2_QUERY_ID) -> dict[str, int]:
    """``"column:reason"`` -> rows, for the list columns that fail (fixed reason words, counts only; never raises)."""
    try:
        profile = profile_for(query_id)
        table = result["query_result"]["data"]["rows"]
    except (InventoryError, KeyError, TypeError):
        return {}
    counts: dict[str, int] = {}
    for raw in table if type(table) is list else ():
        if not isinstance(raw, dict):
            continue
        for column, value in _flat(raw).items():
            if column not in profile.known or column not in LIST_COLUMNS and V2_COLUMNS.get(column) != "list":
                continue
            try:
                items = _domain_list(value)
            except InventoryError:
                reason = "unparseable"
            else:
                if items is None:
                    continue
                if len(items) > (ALT_DOMAINS_MAX_ITEMS if column in LIST_COLUMNS else _MAX_LIST_ITEMS):
                    reason = "too_many_items"
                elif not all(type(item) is str for item in items):
                    reason = "non_text_item"
                elif not all(_HOSTNAME.fullmatch(item) or (column in LIST_COLUMNS and "," in item) for item in items):
                    reason = "item_not_hostname"
                else:
                    continue
            key = column + ":" + reason
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def failing_list_detail(result: Any, query_id: int = V2_QUERY_ID) -> dict[str, Any]:
    """Sizes only, for list columns that fail: ``{column: {"max_items", "item_flags": {class: items}}}`` plus
    ``"max_row_list_chars"`` (all list characters of the largest row). No value is returned; never raises."""
    try:
        profile = profile_for(query_id)
        table = result["query_result"]["data"]["rows"]
    except (InventoryError, KeyError, TypeError):
        return {}
    detail: dict[str, Any] = {}
    max_chars = 0
    for raw in table if type(table) is list else ():
        if not isinstance(raw, dict):
            continue
        row_chars = 0
        for column, value in _flat(raw).items():
            if column not in profile.known or column not in LIST_COLUMNS and V2_COLUMNS.get(column) != "list":
                continue
            try:
                items = _domain_list(value)
            except InventoryError:
                continue
            if items is None:
                continue
            row_chars += sum(len(item) for item in items if type(item) is str)
            limit = ALT_DOMAINS_MAX_ITEMS if column in LIST_COLUMNS else _MAX_LIST_ITEMS
            bad = [item for item in items if type(item) is str and not _HOSTNAME.fullmatch(item)]
            if len(items) <= limit and not bad and all(type(item) is str for item in items):
                continue
            entry = detail.setdefault(column, {"max_items": 0, "item_flags": {}})
            entry["max_items"] = max(entry["max_items"], len(items))
            for item in bad:
                flag = ("has_at" if "@" in item else "has_space" if " " in item else "over_253_chars" if len(item) > 253
                        else "other_character")
                entry["item_flags"][flag] = entry["item_flags"].get(flag, 0) + 1
        max_chars = max(max_chars, row_chars)
    if detail or max_chars:
        detail["max_row_list_chars"] = max_chars
    return {key: detail[key] for key in sorted(detail)}


def _list_chars(row: Mapping[str, Any], v2: Mapping[str, Any]) -> int:
    """Characters held in the v2 lists and allowed modules of one row (alternate domains have their own bounds)."""
    total = 0
    lic = row.get("accountLicense")
    for value in [*(v2.values()), *((lic or {}).values() if isinstance(lic, dict) else ())]:
        if type(value) is list:
            total += sum(len(item) for item in value if type(item) is str)
    return total


def normalize(result: Any, query_id: int = QUERY_ID) -> NormalizedResult:
    """Turn a Redash ``{"query_result": {"data": {...}, "retrieved_at": ...}}`` reply into tenant rows.

    ``query_id`` picks the strict column profile (251 by default); unexpected and missing columns are reported per query.
    """
    profile = profile_for(query_id)
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
    v2_rows: dict[str, dict[str, Any]] = {}
    alt_chars = alt_items = 0
    withheld_counts: dict[str, int] = {}
    for raw in table:
        if not isinstance(raw, dict):
            raise InventoryError("redash_schema_unavailable")
        try:
            withheld: list[str] = []
            row, v2 = _build_row(raw, profile, withheld)
            for column in withheld:
                withheld_counts[column] = withheld_counts.get(column, 0) + 1
        except (OverflowError, ValueError, TypeError):  # huge or odd numbers: a clean refusal, never a traceback
            raise InventoryError("redash_schema_unavailable") from None
        if _list_chars(row, v2) > MAX_ROW_LIST_CHARS:
            raise InventoryError("redash_schema_unavailable")
        alt_chars += sum(len(host) for host in row.get("alternateDomains") or ())
        alt_items += len(row.get("alternateDomains") or ())
        if alt_chars > ALT_DOMAINS_MAX_TOTAL_CHARS or alt_items > ALT_DOMAINS_MAX_TOTAL_ITEMS:
            raise InventoryError("inventory_too_large")
        rows.append(row)
        if profile.row_schema == ROW_SCHEMA_V2 and type(row.get("id")) is str:
            v2_rows.setdefault(row["id"], v2)  # a repeated id keeps its first row, as assemble_rows does
    stamps = [row["_modified"] for row in rows if type(row.get("_modified")) is int]
    latest = max(stamps) if stamps else None
    try:
        retrieved_at = datetime.fromtimestamp(retrieved_ms / 1000, tz=timezone.utc)
        latest_modified = datetime.fromtimestamp(latest / 1000, tz=timezone.utc) if latest is not None else None
    except (OverflowError, OSError, ValueError):
        raise InventoryError("redash_date_unparseable") from None
    has_spycloud = any(name in names for name in (SPYCLOUD_OBJECT_COLUMN, SPYCLOUD_COLUMN))
    if profile.row_schema == ROW_SCHEMA_V2 and any(
            column not in names and not (has_spycloud and column == "spyCloudEnabled")
            for column in (*V2_COLUMNS, *V2_LICENSE_COLUMNS)):
        raise InventoryError("redash_columns_missing")  # query 253 must project every v2 column: not a count, an error
    unexpected = sorted(name if _SAFE_COLUMN.fullmatch(name) else "<column>"
                        for name in names if name not in profile.known)
    optional = profile.known - frozenset(REQUIRED_COLUMNS)
    return NormalizedResult(
        tuple(rows), retrieved_at, latest_modified,
        tuple(unexpected)[:20],
        tuple(sorted(name for name in optional - IGNORED_COLUMNS - UNREPORTED_COLUMNS if name not in names)),
        profile.query_id, v2_rows, dict(sorted(withheld_counts.items())))


def attach_v2(payload: dict[str, Any], normalized: NormalizedResult) -> dict[str, Any]:
    """Query 253 only: add each tenant's flat ``v2`` values, mark ``row_schema: v2`` and re-hash. Query 251: unchanged."""
    from . import leonardo_inventory as inventory

    if profile_for(normalized.query_id).row_schema != ROW_SCHEMA_V2:
        return payload
    for tenant in payload["tenants"]:
        tenant["v2"] = dict(normalized.v2_rows.get(tenant["id"], {}))
    payload["row_schema"] = ROW_SCHEMA_V2
    payload["rows_sha256"] = inventory._rows_sha256(payload["tenants"])  # noqa: SLF001 - the inventory's own digest
    validate_row_schema(payload)
    return payload


def source_meta(normalized: NormalizedResult, collected_at: datetime, *, refreshed: bool) -> dict[str, Any]:
    """Provenance stored in the snapshot (no row data): which query, how old the data is, what drifted."""
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "kind": "redash", "query_id": normalized.query_id, "data_source": "Prod (Cloned) - Mgmt",
        "retrieved_at": normalized.retrieved_at.strftime(stamp),
        "collected_at": collected_at.astimezone(timezone.utc).strftime(stamp),
        "data_latest_modified": normalized.latest_modified.strftime(stamp) if normalized.latest_modified else None,
        "refreshed_this_run": bool(refreshed),
        "withheld_list_cells": dict(normalized.withheld_cells),
        "unexpected_columns": list(normalized.unexpected_columns),
        "missing_columns": list(normalized.missing_columns),
    }
