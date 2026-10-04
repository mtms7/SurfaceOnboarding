"""Minimized, verified local snapshot of the Leonardo tenant inventory.

The attended runner captures the Tenant Management page's own
``getAllDetailedAccounts`` responses while paging; this module turns those
payloads into an allow-listed snapshot file. It opens no connection. The
snapshot is informational only and is never authority for a create or
duplicate decision: those still require a live, attended read.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping

SCHEMA_VERSION = 1
MAX_TOTAL = 5000
MAX_PAGES = 200
ACQUISITION = "ui_pagination_intercept"
# <prefix>-inventory-<UTC stamp>.json; the unprefixed form is the first (2026-10-04) DEV export.
SNAPSHOT_FILE = re.compile(r"(?:(leonardo-dev|backoffice-prod)-)?inventory-(\d{8}T\d{6}Z)\.json")
LATEST_FILE = "latest.json"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_SAFE_KEY = re.compile(r"[A-Za-z_]\w{0,63}")
_STATUS = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
_MODULE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,63}")
_MAX_TEXT = 512
_FUTURE_SKEW = timedelta(minutes=5)


class InventoryError(Exception):
    """Fail-closed inventory outcome; ``reason`` is a stable, value-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class InventoryEnvironment:
    name: str
    origin: str
    endpoint_path: str = "/api/v1/backoffice/getAllDetailedAccounts"
    approved: bool = False
    # Shown in every file name, snapshot, CSV row, and page so DEV and PROD never mix.
    label: str = ""
    file_prefix: str = ""


ENVIRONMENTS: dict[str, InventoryEnvironment] = {
    "dev": InventoryEnvironment("dev", "https://leonardo.dev.app.pentera.io", approved=True,
                                label="DEV (Leonardo Development)", file_prefix="leonardo-dev"),
    # Production BackOffice reads need a separate explicit approval.
    "prod": InventoryEnvironment("prod", "https://app.pentera.io", approved=False,
                                 label="PROD (BackOffice production)", file_prefix="backoffice-prod"),
}


def snapshot_name(environment: InventoryEnvironment, captured_at: datetime) -> str:
    return f"{environment.file_prefix}-inventory-{captured_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def _name_matches(name: str, environment: InventoryEnvironment, captured_at: datetime) -> bool:
    legacy = environment.name == "dev" and name == f"inventory-{captured_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    return name == snapshot_name(environment, captured_at) or legacy


def require_environment(name: str) -> InventoryEnvironment:
    environment = ENVIRONMENTS.get(name) if isinstance(name, str) else None
    if environment is None:
        raise InventoryError("unknown_environment")
    if not environment.approved:
        raise InventoryError("production_inventory_not_approved")
    return environment


# Plan control paths in the tenant row (copied from tools/attended_ce_only_playwright.py).
VALIDATION_TOGGLE_PATHS = {
    "mfaRequired": "primaryUser.isMfaRequired",
    "automatedDiscoveryEnabled": "accountSettings.reconSettings.automatedDiscoveryEnabled",
    "subDomainsReconEnabled": "subDomainsReconEnabled",
    "webDictionaryBruteForceEnabled": "webDictionaryBruteForceEnabled",
    "webDorkingEnabled": "webDorkingEnabled",
    "fullNucleiScanEnabled": "fullNucleiScanEnabled",
    "authenticatedTestingEnabled": "authenticatedTestingEnabled",
    "staticOutboundIpEnabled": "staticOutboundIpEnabled",
    "aiEnabled": "aiEnabled",
    "multipleAttackStacksEnabled": "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled",
    "notificationsAllowed": "accountLicense.notificationsAllowed",
    "multipleUsersAllowed": "accountLicense.multipleUsersAllowed",
    "apiAccessAllowed": "accountLicense.apiAccessAllowed",
    "phishingEnabled": "accountLicense.phishingEnabled",
    "leakedCredentialsAllowed": "accountLicense.leakedCredentialsAllowed",
    "provisioningEnabled": "accountLicense.provisioningEnabled",
}
TOGGLE_PATHS: tuple[str, ...] = tuple(dict.fromkeys((
    *VALIDATION_TOGGLE_PATHS.values(), "webAiAttackerEnabled",
    "campaignExecutionSettings.subDomainsMultiAttackStackSettings.enabled",
)))

_LICENSE_FLAGS = {
    "notifications_allowed": "notificationsAllowed", "multiple_users_allowed": "multipleUsersAllowed",
    "api_access_allowed": "apiAccessAllowed", "leaked_credentials_allowed": "leakedCredentialsAllowed",
    "phishing_enabled": "phishingEnabled", "provisioning_enabled": "provisioningEnabled",
    "elevated_features_enabled": "elevatedFeaturesEnabled",
}
_COUNTED_LISTS = {
    "alternate_domains": "alternateDomains", "sub_domains": "subDomains", "additional_networks": "additionalNetworks",
    "leaked_credentials_scanned_domains": "leakedCredentialsScannedDomains", "user_email_domains": "userEmailDomains",
}
_DATE = "int|str|NoneType"
_FLAG = "bool|NoneType"
_LIST = "list|NoneType"
_EXPECTED_SPEC: dict[str, str] = {
    "id": "str", "_modified": "int|str", "_created": "int|str", "isDeleted": "bool", "accountName": "str",
    "accountUuid": "str|NoneType", "accountDomain": "str|NoneType", "userEmailDomains": _LIST,
    "accountCountryCode": "str|NoneType", "emailSettings": "dict|NoneType", "accountLicense": "dict|NoneType",
    "enabled": "bool", "termsOfUseApproval": "bool|dict|int|str|NoneType", "accountSettings": "dict|NoneType",
    "accountSettings.reconSettings": "dict|NoneType", "accountSettings.reconSettings.automatedDiscoveryEnabled": _FLAG,
    "primaryUserId": "str|NoneType", "accountType": "str|NoneType", "scanningInterval": "str|int|NoneType",
    "leakedCredentialsScanningInterval": "str|int|NoneType", "primaryUser": "dict|NoneType",
    "alternateDomains": _LIST, "subDomains": _LIST, "additionalNetworks": _LIST,
    "leakedCredentialsScannedDomains": _LIST, "lastReconScan": "int|NoneType",
    "lastReconScanDurationMilliseconds": "int|NoneType", "lastReconExecutionData": "dict|NoneType",
    "lastReconExecutionData.timedOutActions": _LIST, "pendingValidationAssets": "list|int|NoneType",
    "campaignsTimeoutInHours": "int|float|NoneType", "staticOutboundIpEnabled": _FLAG,
    "fullNucleiScanEnabled": _FLAG, "aiEnabled": _FLAG, "webAiAttackerEnabled": _FLAG,
    "webDictionaryBruteForceEnabled": _FLAG, "webEnumerationCustomDictionaryPaths": _LIST,
    "webDorkingEnabled": _FLAG, "authenticatedTestingEnabled": _FLAG, "operatorAccounts": _LIST,
    "subDomainsReconEnabled": _FLAG, "leakedCredentialsSettings": "dict|NoneType",
    "leakedCredentialsSettings.spyCloudSettings": "dict|NoneType", "campaignExecutionSettings": "dict|NoneType",
    "campaignExecutionSettings.domainsMultiAttackStackSettings": "dict|NoneType",
    "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled": _FLAG,
    "campaignExecutionSettings.subDomainsMultiAttackStackSettings": "dict|NoneType",
    "campaignExecutionSettings.subDomainsMultiAttackStackSettings.enabled": _FLAG,
    "lastScanStatusEnum": "str|NoneType", "accountSubtype": "str|NoneType",
    "accountLicense.id": "str", "accountLicense._created": "int|str", "accountLicense._modified": "int|str",
    "accountLicense.isDeleted": "bool", "accountLicense.shouldArchive": _FLAG,
    "accountLicense.textSearchField": "str|NoneType", "accountLicense.accountId": "str",
    "accountLicense.assetsNumber": "int|NoneType", "accountLicense.domainsNumber": "int|NoneType",
    "accountLicense.subDomainsNumber": "int|NoneType", "accountLicense.startDate": _DATE,
    "accountLicense.expirationDate": _DATE, "accountLicense.licenseType": "str|NoneType",
    "accountLicense.reconLevel": "str|int|NoneType", "accountLicense.enabled": "bool",
    "accountLicense.scanningFrequency": "str|int|NoneType", "accountLicense.allowedModules": _LIST,
    "accountLicense.leakedCredentialsScannedDomainsNumber": "int|NoneType",
    **{f"accountLicense.{name}": _FLAG for name in _LICENSE_FLAGS.values()},
    # Seen live 2026-10-04: quota objects (max/used counts, renewal time); known, not
    # stored, optional (never reported missing), children not modelled.
    "accountLicense.scanQuotaEnforcement": "dict|NoneType",
    "accountLicense.authWebAttackQuotaEnforcement": "dict|NoneType",
    "primaryUser.id": "str|NoneType", "primaryUser.firstName": "str|NoneType", "primaryUser.lastName": "str|NoneType",
    "primaryUser.email": "str|NoneType", "primaryUser.isMfaRequired": _FLAG, "primaryUser.jobTitle": "str|NoneType",
    "primaryUser.phoneNumber": "str|NoneType",
}
EXPECTED_PATHS: dict[str, frozenset[str]] = {
    path: frozenset(types.split("|")) for path, types in _EXPECTED_SPEC.items()}
# Newer fields not every tenant row carries yet: known when present, never "missing".
OPTIONAL_PATHS = frozenset({"accountLicense.scanQuotaEnforcement", "accountLicense.authWebAttackQuotaEnforcement"})
# Objects whose further children are not modelled; only their listed children are compared.
OPEN_PATHS = frozenset({
    "accountLicense.scanQuotaEnforcement", "accountLicense.authWebAttackQuotaEnforcement",
    "emailSettings", "termsOfUseApproval", "accountSettings", "accountSettings.reconSettings",
    "lastReconExecutionData", "leakedCredentialsSettings", "leakedCredentialsSettings.spyCloudSettings",
    "campaignExecutionSettings", "campaignExecutionSettings.domainsMultiAttackStackSettings",
    "campaignExecutionSettings.subDomainsMultiAttackStackSettings",
})
REQUIRED_PATHS = ("id", "accountUuid", "accountName", "accountLicense")


# ----- row minimization -------------------------------------------------------

def _typed(value: Any, *types: type) -> Any:
    """Return a JSON scalar only when its exact type is expected (bool is never an int)."""
    if type(value) not in types:
        return None
    if type(value) is float and not math.isfinite(value):
        return None
    if type(value) is str and len(value) > _MAX_TEXT:
        return None
    return value


def _count(value: Any) -> int | None:
    if type(value) is list:
        return len(value)
    count = _typed(value, int)
    return count if count is not None and count >= 0 else None


def _path(row: Mapping[str, Any], dotted: str) -> Any:
    value: Any = row
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _epoch_utc(value: Any) -> str | None:
    epoch = _typed(value, int)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch / 1000, tz=timezone.utc).strftime(_STAMP)
    except (OverflowError, OSError, ValueError):
        return None


def _terms_accepted(value: Any) -> bool | None:
    # Shape not confirmed live: a bool is taken as-is; a non-empty record or timestamp counts as accepted.
    if type(value) is bool:
        return value
    if type(value) in (dict, str):
        return bool(value)
    if type(value) is int:
        return value > 0
    return None


_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
MAX_ALTERNATE_DOMAINS = 500


def canonical_domain(value: Any) -> str | None:
    """Lower-case, no trailing dot, a plain domain name; anything else is None."""
    if type(value) is not str:
        return None
    domain = value.strip().casefold().rstrip(".")
    return domain if len(domain) <= 253 and _DOMAIN.fullmatch(domain) else None


def _domain_list(value: Any) -> list[str] | None:
    if type(value) is not list:
        return None
    domains = sorted({domain for domain in map(canonical_domain, value) if domain})
    return domains[:MAX_ALTERNATE_DOMAINS]


def _name_key(value: Any) -> str:
    """The live check's name rule: case-insensitive, whitespace collapsed."""
    return " ".join(value.casefold().split()) if type(value) is str else ""


def duplicate_precheck(payload: Mapping[str, Any], tenant_name: str, co_domains: Any) -> dict[str, Any]:
    """Would this CO duplicate a tenant already in the inventory? (informational; it can only block)

    A tenant matches on (A) the live check's rules: the same tenant name or
    the same primary domain as any of the CO's domains; or (B) one of the
    CO's domains is one of that tenant's alternate domains. A "no match" is
    never a clearance: the live duplicate check still runs at Start.
    """
    name = _name_key(tenant_name)
    wanted = {domain for domain in map(canonical_domain, co_domains or ()) if domain}
    tenants = [tenant for tenant in payload.get("tenants") or () if isinstance(tenant, dict)]
    matches = []
    for tenant in tenants:
        reasons = []
        if name and _name_key(tenant.get("account_name")) == name:
            reasons.append("tenant_name")
        if canonical_domain(tenant.get("account_domain")) in wanted:
            reasons.append("primary_domain")
        if wanted & set(tenant.get("alternate_domains") or ()):
            reasons.append("alternate_domain")
        if reasons:
            matches.append({"id": tenant.get("id"), "account_name": tenant.get("account_name"),
                            "is_deleted": tenant.get("is_deleted") is True, "reasons": reasons})
    return {"result": "inventory_match" if matches else "inventory_no_match", "matches": matches,
            "tenants_checked": len(tenants),
            "alternate_domains_available": any("alternate_domains" in tenant for tenant in tenants)}


def minimize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Allow-list projection of one tenant row; no personal data, domain lists, or settings blobs."""
    get = row.get
    license_ = get("accountLicense") if isinstance(get("accountLicense"), dict) else {}
    lic = license_.get
    modules = lic("allowedModules")
    allowed_modules = (sorted(modules) if type(modules) is list
                       and all(type(item) is str and _MODULE.fullmatch(item) for item in modules) else None)
    status = get("lastScanStatusEnum")
    duration = _typed(get("lastReconScanDurationMilliseconds"), int)
    execution = get("lastReconExecutionData")
    timed_out = execution.get("timedOutActions") if isinstance(execution, dict) else None
    operators = get("operatorAccounts")
    primary = get("primaryUser")
    return {
        "id": _typed(get("id"), str),
        "account_uuid": _typed(get("accountUuid"), str),
        "account_name": _typed(get("accountName"), str),
        "account_domain": _typed(get("accountDomain"), str),
        "account_type": _typed(get("accountType"), str),
        "account_subtype": _typed(get("accountSubtype"), str),
        "account_country_code": _typed(get("accountCountryCode"), str),
        "enabled": _typed(get("enabled"), bool),
        "is_deleted": _typed(get("isDeleted"), bool),
        "created": _typed(get("_created"), int, str),
        "modified": _typed(get("_modified"), int, str),
        "scanning_interval": _typed(get("scanningInterval"), str, int),
        "leaked_credentials_scanning_interval": _typed(get("leakedCredentialsScanningInterval"), str, int),
        "campaigns_timeout_hours": _typed(get("campaignsTimeoutInHours"), int, float),
        "license": {
            "type": _typed(lic("licenseType"), str),
            "start_date": _typed(lic("startDate"), int, str),
            "expiration_date": _typed(lic("expirationDate"), int, str),
            "recon_level": _typed(lic("reconLevel"), str, int),
            "enabled": _typed(lic("enabled"), bool),
            "scanning_frequency": _typed(lic("scanningFrequency"), str, int),
            "assets_number": _typed(lic("assetsNumber"), int),
            "domains_number": _typed(lic("domainsNumber"), int),
            "subdomains_number": _typed(lic("subDomainsNumber"), int),
            "leaked_credentials_scanned_domains_number": _typed(lic("leakedCredentialsScannedDomainsNumber"), int),
            **{name: _typed(lic(key), bool) for name, key in _LICENSE_FLAGS.items()},
            "allowed_modules": allowed_modules,
        },
        "toggles": {path: _typed(_path(row, path), bool) for path in TOGGLE_PATHS},
        "scan": {
            "last_recon_scan_ms": _typed(get("lastReconScan"), int),
            "last_recon_scan_utc": _epoch_utc(get("lastReconScan")),
            "duration_ms": duration if duration is not None and duration >= 0 else None,
            "status": status if type(status) is str and _STATUS.fullmatch(status) else None,
            "timed_out_actions_count": len(timed_out) if type(timed_out) is list else None,
            "pending_validation_assets_count": _count(get("pendingValidationAssets")),
        },
        "counts": {name: len(get(key)) if type(get(key)) is list else None for name, key in _COUNTED_LISTS.items()},
        # Owner decision 2026-10-04 (option B): company alternate domains are kept for the
        # duplicate pre-check. Still no user names, emails, phones, or other domain lists.
        "alternate_domains": _domain_list(get("alternateDomains")),
        "operator_assigned": bool(operators) if type(operators) is list else None,
        "terms_accepted": _terms_accepted(get("termsOfUseApproval")),
        "primary_user": {
            "present": isinstance(primary, dict),
            "mfa_required": _typed(primary.get("isMfaRequired"), bool) if isinstance(primary, dict) else None,
        },
    }


# ----- page assembly ----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AssembledInventory:
    rows: tuple[dict[str, Any], ...] = field(repr=False)  # raw rows: in memory only, never written
    total_count: int
    pages: int
    duplicates_dropped: int
    deleted_count: int
    query: dict[str, Any]
    # Tenants Leonardo returns with accountUuid null (2 in Development, live 2026-10-03):
    # kept and counted, never matchable to a CO (matching needs id AND UUID).
    uuid_missing_count: int = 0


def _page_parts(request: Any, response: Any) -> tuple[dict[str, Any], int, list[Any]]:
    server = request.get("tableServerData") if isinstance(request, dict) else None
    pagination = response.get("pagination_response") if isinstance(response, dict) else None
    if not isinstance(server, dict) or not isinstance(pagination, dict):
        raise InventoryError("inventory_schema_unavailable")
    total, rows = pagination.get("total_count"), pagination.get("table_data")
    if type(total) is not int or total < 0 or type(rows) is not list:
        raise InventoryError("inventory_schema_unavailable")
    return server, total, rows


def assemble_pages(pages: list[tuple[dict[str, Any], dict[str, Any]]], *, page_size: int) -> AssembledInventory:
    """Verify a complete, consistent capture of every page and return the de-duplicated rows."""
    if type(page_size) is not int or page_size <= 0:
        raise InventoryError("inventory_inconsistent")
    if len(pages) > MAX_PAGES:
        raise InventoryError("inventory_too_large")
    if not pages:
        raise InventoryError("inventory_schema_unavailable")
    parts = [_page_parts(request, response) for request, response in pages]
    totals = {total for _, total, _ in parts}
    if len(totals) != 1:
        raise InventoryError("inventory_inconsistent")
    total_count = totals.pop()
    if total_count > MAX_TOTAL:
        raise InventoryError("inventory_too_large")
    if total_count == 0:
        raise InventoryError("inventory_empty")
    first = parts[0][0]
    for index, (server, _, _) in enumerate(parts):
        offset = server.get("offset")
        if (type(offset) is not int or offset != index * page_size or server.get("items_per_page") != page_size
                or server.get("sort") != first.get("sort") or server.get("filters") != first.get("filters")):
            raise InventoryError("inventory_inconsistent")
    rows: list[dict[str, Any]] = []
    uuid_by_id: dict[str, str] = {}
    id_by_uuid: dict[str, str] = {}
    duplicates = 0
    for _, _, table in parts:
        for row in table:
            if not isinstance(row, dict) or not all(type(row.get(key)) is str and row[key]
                                                    for key in ("id", "accountName")):
                raise InventoryError("inventory_schema_unavailable")
            uuid = row.get("accountUuid", "")
            if uuid is not None and (type(uuid) is not str or not uuid):
                raise InventoryError("inventory_schema_unavailable")  # only an explicit null is tolerated
            row_id, row_uuid = row["id"], uuid.casefold() if uuid else ""
            if uuid_by_id.get(row_id, row_uuid) != row_uuid or (row_uuid and id_by_uuid.get(row_uuid, row_id) != row_id):
                raise InventoryError("inventory_id_conflict")
            if row_id in uuid_by_id:
                duplicates += 1
                continue
            uuid_by_id[row_id] = row_uuid
            if row_uuid:
                id_by_uuid[row_uuid] = row_id
            rows.append(row)
    if len(rows) != total_count:
        raise InventoryError("inventory_inconsistent")
    sort = first.get("sort") if isinstance(first.get("sort"), dict) else {}
    query = {"page_size": page_size,
             "sort": {"direction": _typed(sort.get("direction"), str), "key": _typed(sort.get("key"), str)},
             "filter": "present" if first.get("filters") else "none"}
    return AssembledInventory(tuple(rows), total_count, len(pages), duplicates,
                              sum(1 for row in rows if row.get("isDeleted") is True), query,
                              sum(1 for row in rows if row.get("accountUuid") is None))


# ----- schema drift -----------------------------------------------------------

def _walk(value: Any, path: str, open_: bool, expected: Mapping[str, frozenset[str]],
          observed: dict[str, set[str]]) -> None:
    known = path in expected
    if path and (known or not open_):
        observed.setdefault(path, set()).add(type(value).__name__)
    child_open = open_ or path in OPEN_PATHS or isinstance(value, list) or (bool(path) and not known)
    if isinstance(value, dict):
        for key, child in value.items():
            name = key if isinstance(key, str) and _SAFE_KEY.fullmatch(key) else "<key>"
            _walk(child, f"{path}.{name}" if path else name, child_open, expected, observed)
    elif isinstance(value, list):
        for item in value:
            _walk(item, f"{path}[]", child_open, expected, observed)


def schema_drift(rows: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
                 expected_paths: Mapping[str, frozenset[str]] = EXPECTED_PATHS) -> dict[str, list[str]]:
    """Type-only path comparison against the expected row shape; reports paths, never values."""
    observed: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or any(key not in row for key in REQUIRED_PATHS):
            raise InventoryError("inventory_schema_unavailable")
        _walk(row, "", False, expected_paths, observed)
    missing = [path for path in expected_paths if path not in observed and path not in OPTIONAL_PATHS
               and ("." not in path or "dict" in observed.get(path.rsplit(".", 1)[0], ()))]
    return {
        "unknown": sorted(path for path in observed if path not in expected_paths),
        "missing": sorted(missing),
        "type_changed": sorted(f"{path}:{name}" for path, names in observed.items() if path in expected_paths
                               for name in names - expected_paths[path]),
    }


# ----- snapshot ---------------------------------------------------------------

def _rows_sha256(tenants: Any) -> str:
    canonical = json.dumps(tenants, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_payload(env: InventoryEnvironment, assembled: AssembledInventory, captured_at: datetime) -> dict[str, Any]:
    if require_environment(env.name) != env:
        raise InventoryError("unknown_environment")
    if captured_at.tzinfo is None:
        raise ValueError("captured_at_requires_timezone")
    tenants = sorted((minimize_row(row) for row in assembled.rows), key=lambda tenant: tenant["id"])
    return {
        "schema_version": SCHEMA_VERSION, "environment": env.name, "environment_label": env.label,
        "origin": env.origin,
        "endpoint_path": env.endpoint_path, "acquisition": ACQUISITION,
        "captured_at": captured_at.astimezone(timezone.utc).strftime(_STAMP), "query": dict(assembled.query),
        "total_count": assembled.total_count, "row_count": len(tenants), "deleted_count": assembled.deleted_count,
        "pages": assembled.pages, "duplicates_dropped": assembled.duplicates_dropped,
        "uuid_missing_count": assembled.uuid_missing_count,
        "schema_drift": schema_drift(assembled.rows), "tenants": tenants, "rows_sha256": _rows_sha256(tenants),
    }


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "SurfaceOnboarding" / "leonardo-inventory"


def env_dir(root: Path, env_name: str) -> Path:
    if env_name not in ENVIRONMENTS:
        raise InventoryError("unknown_environment")
    return Path(root) / env_name


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_stamp(value: Any) -> datetime:
    try:
        return datetime.strptime(value, _STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise InventoryError("inventory_snapshot_tampered") from exc


def prune(directory: Path, keep: int) -> list[str]:
    """Delete snapshot files beyond the newest ``keep``; any other file is left alone."""
    if type(keep) is not int or keep < 1:
        raise ValueError("keep_must_be_positive")
    matches = [(SNAPSHOT_FILE.fullmatch(entry.name), entry.name) for entry in Path(directory).iterdir()
               if entry.is_file()]
    # Newest first by capture stamp (prefixed and legacy names sort differently by name).
    names = [name for match, name in sorted(((m, n) for m, n in matches if m), key=lambda item: item[0].group(2),
                                            reverse=True)]
    for name in names[keep:]:
        (Path(directory) / name).unlink(missing_ok=True)
        (Path(directory) / name).with_suffix(".csv").unlink(missing_ok=True)  # its CSV goes with it
    return names[keep:]


def write_snapshot(payload: Mapping[str, Any], root: Path, *, keep: int = 10) -> Path:
    environment = require_environment(payload.get("environment"))
    if payload.get("rows_sha256") != _rows_sha256(payload.get("tenants")):
        raise InventoryError("inventory_snapshot_tampered")
    captured_at = _parse_stamp(payload.get("captured_at"))
    directory = env_dir(root, environment.name)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / snapshot_name(environment, captured_at)
    _write_json_atomic(target, payload)
    _write_json_atomic(directory / LATEST_FILE, {
        "file": target.name, "rows_sha256": payload["rows_sha256"], "captured_at": payload["captured_at"],
        "schema_version": payload.get("schema_version"), "environment": environment.name})
    prune(directory, keep)
    return target


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryError("inventory_snapshot_missing") from exc
    except (OSError, ValueError) as exc:
        raise InventoryError("inventory_snapshot_tampered") from exc
    if not isinstance(value, dict):
        raise InventoryError("inventory_snapshot_tampered")
    return value


def load_latest(root: Path, env_name: str, *, max_age: timedelta, now: datetime) -> dict[str, Any]:
    """Load and re-verify the newest snapshot; any mismatch fails closed with a reason code."""
    environment = require_environment(env_name)
    if now.tzinfo is None:
        raise ValueError("now_requires_timezone")
    directory = env_dir(root, environment.name)
    latest = _read_json(directory / LATEST_FILE)
    name = latest.get("file")
    if type(name) is not str or not SNAPSHOT_FILE.fullmatch(name):
        raise InventoryError("inventory_snapshot_tampered")
    if latest.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError("inventory_snapshot_schema_version")
    if latest.get("environment") != environment.name:
        raise InventoryError("inventory_snapshot_wrong_environment")
    payload = _read_json(directory / name)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError("inventory_snapshot_schema_version")
    if (payload.get("environment"), payload.get("origin"), payload.get("endpoint_path")) != (
            environment.name, environment.origin, environment.endpoint_path):
        raise InventoryError("inventory_snapshot_wrong_environment")
    tenants = payload.get("tenants")
    captured_at = _parse_stamp(payload.get("captured_at"))
    digest = _rows_sha256(tenants)
    if (type(tenants) is not list or payload.get("rows_sha256") != digest or latest.get("rows_sha256") != digest
            or latest.get("captured_at") != payload.get("captured_at") or payload.get("row_count") != len(tenants)
            or not _name_matches(name, environment, captured_at)):
        raise InventoryError("inventory_snapshot_tampered")
    age = now - captured_at
    if age < -_FUTURE_SKEW:
        raise InventoryError("inventory_snapshot_tampered")
    if age > max_age:
        raise InventoryError("inventory_snapshot_stale")
    return {**payload, "age_seconds": max(0, int(age.total_seconds()))}


# ----- local views ------------------------------------------------------------

CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("environment", "environment_label"), ("id", "id"), ("account_uuid", "account_uuid"), ("account_name", "account_name"),
    ("account_domain", "account_domain"), ("account_type", "account_type"), ("enabled", "enabled"),
    ("is_deleted", "is_deleted"), ("created", "created"), ("license_type", "license.type"),
    ("license_start_date", "license.start_date"), ("license_expiration_date", "license.expiration_date"),
    ("license_assets_number", "license.assets_number"), ("license_domains_number", "license.domains_number"),
    ("license_subdomains_number", "license.subdomains_number"), ("scanning_interval", "scanning_interval"),
    ("last_recon_scan_utc", "scan.last_recon_scan_utc"), ("scan_duration_ms", "scan.duration_ms"),
    ("scan_status", "scan.status"), ("alternate_domains", "alternate_domains"),
)


def _csv_cell(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    if isinstance(value, (dict, list)):
        return ""
    text = "" if value is None else ("true" if value is True else "false" if value is False else str(value))
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(payload: Mapping[str, Any]) -> str:
    """Flat CSV of allow-listed scalars; formula-leading cells are neutralized with a quote."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for header, _ in CSV_COLUMNS])
    environment = ENVIRONMENTS.get(payload.get("environment"))
    label = payload.get("environment_label") or (environment.label if environment else "")
    for tenant in payload.get("tenants") or ():
        if isinstance(tenant, dict):
            row = {**tenant, "environment_label": label}
            writer.writerow([_csv_cell(_path(row, path)) for _, path in CSV_COLUMNS])
    return buffer.getvalue()


def match_readbacks(payload: Mapping[str, Any], readbacks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Relate local CO readbacks to snapshot tenants (informational; never create authority).

    A CO maps to a tenant only when the readback's surface_account_id AND
    account_uuid both match; a match on either one alone is a "conflict".
    """
    tenants = [tenant for tenant in payload.get("tenants") or () if isinstance(tenant, dict)
               and type(tenant.get("id")) is str]
    # A tenant without a UUID keeps an empty one here: it can never match, but a CO
    # whose captured id points at it is a conflict, and it still counts as an orphan.
    by_id = {tenant["id"]: (tenant["account_uuid"] or "").casefold() if type(tenant.get("account_uuid")) is str else ""
             for tenant in tenants}
    by_uuid = {uuid: tenant_id for tenant_id, uuid in by_id.items() if uuid}
    by_reference: dict[str, str | None] = {}
    matched: set[str] = set()
    for reference, readback in readbacks.items():
        account_id = readback.get("surface_account_id") if isinstance(readback, Mapping) else None
        account_uuid = readback.get("account_uuid") if isinstance(readback, Mapping) else None
        if type(account_id) is not str or type(account_uuid) is not str:
            by_reference[reference] = None
            continue
        account_uuid = account_uuid.casefold()
        if account_uuid and by_id.get(account_id) == account_uuid:
            by_reference[reference] = account_id
            matched.add(account_id)
        elif account_id in by_id or account_uuid in by_uuid:
            by_reference[reference] = "conflict"
        else:
            by_reference[reference] = None
    return {"by_reference": by_reference, "orphans": sorted(set(by_id) - matched)}
