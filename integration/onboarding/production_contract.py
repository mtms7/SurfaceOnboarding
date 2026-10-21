"""Production CE-only create/readback contract (docs/49): what the body and the readback row must look like.

Pure decisions on in-memory data. No browser, network, file read or Salesforce access happens here (the only file
write is the redacted readback report). Results are FIELD NAMES and pass/fail only: no value, email or UUID is ever
returned or printed. Every comparison is type-sensitive ("1" is not 1, True is not 1).

``intended`` is a mapping the runner derives from the Dev CE-only fill plan (``ce_production_intended`` in
tools/attended_ce_only_playwright.py): ``tenant_name``, ``primary_domain``, ``email_domain`` (the ONE internal user email
domain), ``license_end`` (date or ISO text), and optionally ``country_code`` (ISO-2), ``first_name``, ``last_name``,
``organization_email``. The constants below are pinned against the Dev runner's values by tests.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from . import production_probe

# --- Constants (docs/49 sections 1 and 3); tests pin them against the Dev runner -----------------------------------
NAME_SUFFIX = " - CE Only"
ACCOUNT_TYPE_CREATE = "customer"      # create body: lower case
ACCOUNT_TYPE_ROW = "Customer"         # readback row: capitalised
LICENSE_TYPE = "prepaid annual subscription"
SCANNING_INTERVAL = "NO_SCHEDULE"
LC_INTERVAL = "WEEKLY"
CAMPAIGN_TIMEOUT_HOURS = 24
LICENSE_COUNT_TEXT = "1"              # create body: assets and domains are STRINGS
LICENSE_COUNT = 1                     # create body: subdomains is a number; readback: all three are numbers
RECON_LEVEL_ROW = "ALL_ASSETS"
CREATE_FLAGS_OFF = ("automatedDiscoveryEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled",
                    "webDorkingEnabled", "staticOutboundIpEnabled", "aiEnabled", "webAiAttackerEnabled", "vodBetaEnabled")

ACCOUNTS_PATH = "/api/v1/backoffice/getAllDetailedAccounts"
SEARCH_PAGE_SIZE = production_probe.TABLE_ROW_LIMIT
REPORT_PREFIX = "readback-"

_MISSING = object()
_UUID = re.compile(r"[0-9a-f]{32}")
_ISO_UTC = re.compile(r"(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
_ISO2 = re.compile(r"[A-Z]{2}")


class _Shape:
    """An expected value described by a predicate (dates and values the contract only constrains by form)."""

    def __init__(self, name: str, test: Callable[[Any], bool]) -> None:
        self.name, self._test = name, test

    def matches(self, value: Any) -> bool:
        try:
            return bool(self._test(value))
        except Exception:
            return False

    def __repr__(self) -> str:
        return f"<shape {self.name}>"


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    return type(value) in (int, float)


def _iso_utc(value: Any) -> bool:
    return isinstance(value, str) and _ISO_UTC.fullmatch(value) is not None


def _iso_on(day: str) -> _Shape:
    return _Shape("iso_utc_on_" + day, lambda v: isinstance(v, str) and (m := _ISO_UTC.fullmatch(v)) is not None
                  and m.group(1) == day)


SHAPE_TEXT = _Shape("non_empty_text", _nonempty_text)
SHAPE_NUMBER = _Shape("number", _number)
SHAPE_ISO_UTC = _Shape("iso_utc", _iso_utc)
SHAPE_ISO2 = _Shape("iso2", lambda v: isinstance(v, str) and _ISO2.fullmatch(v) is not None)


def _same(expected: Any, observed: Any) -> bool:
    """Type-sensitive equality; lists compare element by element; a _Shape is judged by its predicate."""
    if isinstance(expected, _Shape):
        return expected.matches(observed)
    if isinstance(expected, list):
        return (isinstance(observed, list) and len(expected) == len(observed)
                and all(_same(a, b) for a, b in zip(expected, observed)))
    return type(expected) is type(observed) and expected == observed


def _text(intended: Mapping[str, Any], key: str) -> str:
    value = intended.get(key) if isinstance(intended, Mapping) else None
    if not isinstance(value, str) or not value.strip() or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(f"intended_{key}_invalid")
    return value


def _day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    raise ValueError("intended_license_end_invalid")


def _checked(intended: Mapping[str, Any]) -> tuple[str, str, str]:
    name, domain, email_domain = _text(intended, "tenant_name"), _text(intended, "primary_domain"), _text(intended, "email_domain")
    if not name.endswith(NAME_SUFFIX) or name == NAME_SUFFIX.strip():
        raise ValueError("tenant_name_not_ce_only")
    if "," in email_domain or " " in email_domain.strip() or email_domain.casefold() == domain.casefold():
        raise ValueError("email_domain_not_one_internal_domain")
    return name, domain, email_domain


def _optional(intended: Mapping[str, Any], key: str) -> Any:
    value = intended.get(key)
    return value if isinstance(value, str) and value.strip() else SHAPE_TEXT


# --- (1)-(2) The create body -----------------------------------------------------------------------------------------
def expected_create_payload(intended: Mapping[str, Any]) -> dict[str, Any]:
    """Flat dotted field name -> expected value (or shape) for the ``account/add`` body of a CE-only New (docs/49 s.1)."""
    name, domain, email_domain = _checked(intended)
    end = _day(intended.get("license_end"))
    country = intended.get("country_code")
    expected: dict[str, Any] = {
        "accountName": name, "accountDomain": domain, "accountType": ACCOUNT_TYPE_CREATE,
        "accountCountryCode": country if isinstance(country, str) and _ISO2.fullmatch(country) else SHAPE_ISO2,
        "organizationEmail": _optional(intended, "organization_email"),
        "firstName": _optional(intended, "first_name"), "lastName": _optional(intended, "last_name"),
        "mfaRequired": True,
        "alternateDomains": [], "subDomains": [], "additionalNetworks": None, "userEmailDomains": [email_domain],
        "operatorAccountNames": [],
        "scanningInterval": SCANNING_INTERVAL, "scanNow": False,
        "leakedCredentialsScanningInterval": LC_INTERVAL, "leakedCredentialsScannedDomains": [domain],
        "campaignsTimeoutInHours": CAMPAIGN_TIMEOUT_HOURS,
        "license.assetsNumber": LICENSE_COUNT_TEXT, "license.domainsNumber": LICENSE_COUNT_TEXT,
        "license.subDomainsNumber": LICENSE_COUNT, "license.licenseType": LICENSE_TYPE,
        "license.startDate": SHAPE_ISO_UTC, "license.expirationDate": _iso_on(end),
        "license.enabled": True, "license.notificationsAllowed": False, "license.multipleUsersAllowed": False,
        "license.apiAccessAllowed": False, "license.phishingEnabled": False, "license.provisioningEnabled": True,
        "license.leakedCredentialsAllowed": True, "license.reconLevel": None,
        "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled": False,
        "campaignExecutionSettings.domainsMultiAttackStackSettings.numOfAssetsPerStack": SHAPE_NUMBER,
        "campaignExecutionSettings.subDomainsMultiAttackStackSettings.enabled": False,
        "campaignExecutionSettings.subDomainsMultiAttackStackSettings.numOfAssetsPerStack": SHAPE_NUMBER,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
    }
    for flag in CREATE_FLAGS_OFF:
        expected[flag] = False
    return expected


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Nested dicts become dotted names; lists and empty dicts are leaves."""
    flat: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping) and item:
            flat.update(_flatten(item, name))
        else:
            flat[name] = item
    return flat


def compare_payload(expected: Mapping[str, Any], observed: Any) -> list[str]:
    """Mismatching field NAMES (sorted): wrong, wrong-typed or missing; keys not in ``expected`` as ``unexpected:<name>``."""
    if not isinstance(observed, Mapping):
        return sorted(expected)
    flat = _flatten(observed)
    names = [name for name, want in expected.items() if name not in flat or not _same(want, flat[name])]
    names += [f"unexpected:{name}" for name in flat if name not in expected]
    return sorted(names)


# --- (3) The readback row ----------------------------------------------------------------------------------------------
# Flags of the create (all false) -> candidate row paths (first present wins). Names follow the Dev validation paths.
# Live finding 2026-10-18 (full rows, no projection): the real paths below; the earlier fallback candidates were proven absent.
ROW_FLAG_PATHS: dict[str, tuple[str, ...]] = {
    "automatedDiscoveryEnabled": ("accountSettings.reconSettings.automatedDiscoveryEnabled",),
    "subDomainsReconEnabled": ("subDomainsReconEnabled",),
    "webDictionaryBruteForceEnabled": ("webDictionaryBruteForceEnabled",),
    "webDorkingEnabled": ("webDorkingEnabled",),
    "staticOutboundIpEnabled": ("staticOutboundIpEnabled",),
    "aiEnabled": ("aiEnabled",),
    "webAiAttackerEnabled": ("webAiAttackerEnabled",),
    "vodBetaEnabled": ("vodBetaEnabled",),
}
# Only the STRICT live comparison uses these (docs/53): row paths for values the form sets, all seen on the live full row
# (2026-10-18) and in the Dev schema (leonardo_inventory.py). A path the row does not carry compares as a mismatch NAME (fail
# closed). ``primaryUser.email`` is compared in memory only; it is never logged, recorded or printed.
STRICT_ROW_PATHS: dict[str, tuple[str, ...]] = {
    "licenseStartDate": ("accountLicense.startDate",),
    "licenseExpirationDate": ("accountLicense.expirationDate",),
    "mfaRequired": ("primaryUser.isMfaRequired",),
    "organizationEmail": ("primaryUser.email",),
    # Dev-verified path (VALIDATION_TOGGLE_PATHS); the create body sets it False, so the row carries a boolean.
    "multipleAttackStacksEnabled": ("campaignExecutionSettings.domainsMultiAttackStackSettings.enabled",),
}
ROW_PATHS: dict[str, tuple[str, ...]] = {
    "reconLevel": ("accountLicense.reconLevel",),
    "scanningFrequency": ("accountLicense.scanningFrequency",),
    **ROW_FLAG_PATHS,
    **STRICT_ROW_PATHS,
}
READBACK_PICK_PATHS = tuple(dict.fromkeys(
    ["accountUuid", "id", "accountName", "accountDomain", "accountType", "accountCountryCode", "scanningInterval",
     "leakedCredentialsScanningInterval", "userEmailDomains", "accountLicense.assetsNumber", "accountLicense.domainsNumber",
     "accountLicense.subDomainsNumber", "accountLicense.leakedCredentialsAllowed", "accountLicense.provisioningEnabled",
     "accountLicense.notificationsAllowed", "accountLicense.multipleUsersAllowed", "accountLicense.apiAccessAllowed",
     "accountLicense.phishingEnabled", "leakedCredentialsSettings.spyCloudSettings.enabled",
     *(path for paths in ROW_PATHS.values() for path in paths)]))


def expected_readback_row(intended: Mapping[str, Any], strict: bool = False) -> dict[str, Any]:
    """Logical row field name -> expected value for the getAllDetailedAccounts row of the created tenant (docs/49 s.3).

    ``strict`` (the live create) adds the license start/expiration day, MFA and the organization email; only names are reported.
    """
    name, domain, email_domain = _checked(intended)
    country = intended.get("country_code")
    expected: dict[str, Any] = {
        "accountName": name, "accountDomain": domain, "accountType": ACCOUNT_TYPE_ROW,
        "accountCountryCode": country if isinstance(country, str) and _ISO2.fullmatch(country) else SHAPE_ISO2,
        "scanningInterval": SCANNING_INTERVAL, "leakedCredentialsScanningInterval": LC_INTERVAL,
        "scanningFrequency": SCANNING_INTERVAL, "reconLevel": RECON_LEVEL_ROW,
        "accountLicense.assetsNumber": LICENSE_COUNT, "accountLicense.domainsNumber": LICENSE_COUNT,
        "accountLicense.subDomainsNumber": LICENSE_COUNT,
        "accountLicense.leakedCredentialsAllowed": True, "accountLicense.provisioningEnabled": True,
        "accountLicense.notificationsAllowed": False, "accountLicense.multipleUsersAllowed": False,
        "accountLicense.apiAccessAllowed": False, "accountLicense.phishingEnabled": False,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
        "userEmailDomains": [email_domain],
    }
    for flag in ROW_FLAG_PATHS:
        expected[flag] = False
    if strict:
        expected.update(strict_row_expectations(intended))
    return expected


def _row_day(value: Any) -> str | None:
    """The UTC calendar day of a row date given as ISO text or as epoch milliseconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and (m := _ISO_UTC.fullmatch(value)):
        return m.group(1)
    return None


def _day_shape(days: tuple[str, ...], name: str) -> _Shape:
    return _Shape(name, lambda v: _row_day(v) in days)


def strict_row_expectations(intended: Mapping[str, Any]) -> dict[str, Any]:
    """The extra readback checks of the live create: license start and expiration day, MFA on, the organization email, MAS off."""
    code = intended.get("country_code")
    if not isinstance(code, str) or _ISO2.fullmatch(code) is None:  # the exact filled form value, never just an ISO2 shape
        raise ValueError("intended_country_code_invalid")
    expected: dict[str, Any] = {"mfaRequired": True, "accountCountryCode": code, "multipleAttackStacksEnabled": False}
    end = _day(intended.get("license_end"))
    expected["licenseExpirationDate"] = _day_shape((end,), "expiration_day_" + end)
    start = intended.get("license_start")
    if start is None:
        raise ValueError("intended_license_start_invalid")
    first = date.fromisoformat(_day(start))
    days = tuple((first + timedelta(days=d)).isoformat() for d in (-1, 0, 1))  # the picker time zone may shift one day
    expected["licenseStartDate"] = _day_shape(days, "start_day_" + days[1])
    email = intended.get("organization_email")
    if not isinstance(email, str) or not email.strip() or re.search(r"[\x00-\x1f\x7f]", email):
        raise ValueError("intended_organization_email_invalid")
    expected["organizationEmail"] = email
    return expected


def _lookup(row: Mapping[str, Any], path: str) -> Any:
    """A flat dotted key first (the page script returns flat rows), else the nested walk; ``_MISSING`` if absent."""
    if path in row:
        return row[path]
    node: Any = row
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _row_value(row: Mapping[str, Any], logical: str) -> Any:
    for path in ROW_PATHS.get(logical, (logical,)):
        value = _lookup(row, path)
        if value is not _MISSING:
            return value
    return _MISSING


def uuid_well_formed(value: Any) -> bool:
    return isinstance(value, str) and _UUID.fullmatch(value) is not None


def compare_readback(expected: Mapping[str, Any], row: Any) -> tuple[list[str], bool]:
    """(mismatching field names sorted, uuid_ok). ``uuid_ok`` is True for a 32 lowercase-hex ``accountUuid``; never returned."""
    if not isinstance(row, Mapping):
        return sorted(expected), False
    mismatches = sorted(name for name, want in expected.items()
                        if (got := _row_value(row, name)) is _MISSING or not _same(want, got))
    uuid = _lookup(row, "accountUuid")
    return mismatches, uuid_well_formed(uuid)


def salesforce_uuid_matches(salesforce_uuid: Any, tenant_uuid: Any) -> bool | None:
    """None when Salesforce holds no UUID; else whether it equals the tenant's. Never returns either.

    Both sides are normalised first (hyphens stripped, lower-cased); the tenant side must then be 32 hex characters.
    """
    if not isinstance(salesforce_uuid, str) or not salesforce_uuid.strip():
        return None
    if not isinstance(tenant_uuid, str):
        return False
    tenant = tenant_uuid.strip().replace("-", "").casefold()
    if not uuid_well_formed(tenant):
        return False
    return salesforce_uuid.strip().replace("-", "").casefold() == tenant


_ACCOUNT_ID = re.compile(r"[0-9a-f]{24}")


def salesforce_account_id_matches(salesforce_id: Any, tenant_id: Any) -> bool | None:
    """None when Salesforce holds no Surface Account ID; else whether it equals the tenant's 24-hex id. Never returns either."""
    if not isinstance(salesforce_id, str) or not salesforce_id.strip():
        return None
    if not isinstance(tenant_id, str):
        return False
    ours, theirs = salesforce_id.strip().casefold(), tenant_id.strip().casefold()
    return _ACCOUNT_ID.fullmatch(ours) is not None and _ACCOUNT_ID.fullmatch(theirs) is not None and ours == theirs


# --- (4) One row, by exact name AND domain -------------------------------------------------------------------------------
def _identity(row: Mapping[str, Any]) -> tuple[Any, Any]:
    return _lookup(row, "accountUuid"), _lookup(row, "id")


def select_unique_row(rows: Any, name: str, domain: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """(row, None) for the single tenant whose accountName AND accountDomain equal ``name`` / ``domain`` exactly; else
    (None, code): ``no_match``, ``multiple_matches`` (distinct tenants), ``name_only_match`` (same name, other domain) or
    ``case_variant_match`` (a name equal to the target only case-insensitively).

    Rows of ONE tenant (equal non-empty accountUuid and id) count once. Any same-name row with another domain refuses,
    even beside an exact row: a duplicate name is never resolved by guessing.
    """
    if not isinstance(rows, (list, tuple)) or not isinstance(name, str) or not name or not isinstance(domain, str) or not domain:
        return None, "no_match"
    exact, name_only, variant = [], 0, 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        found = _lookup(row, "accountName")
        if isinstance(found, str) and found != name and found.casefold() == name.casefold():
            variant += 1
            continue
        if found != name:
            continue
        if _lookup(row, "accountDomain") == domain:
            exact.append(row)
        else:
            name_only += 1
    if variant:  # a name that differs only in case is another tenant: never resolved by guessing, even beside an exact row
        return None, "case_variant_match"
    if not exact:
        return (None, "name_only_match") if name_only else (None, "no_match")
    identities = {_identity(row) for row in exact}
    one_tenant = (len(identities) == 1 and all(isinstance(part, str) and part for part in next(iter(identities))))
    if len(exact) > 1 and not one_tenant:
        return None, "multiple_matches"
    if name_only:
        return None, "name_only_match"
    return exact[0], None


# --- The read-only page call --------------------------------------------------------------------------------------------
def build_readback_body(name: str) -> dict[str, Any]:
    """Exact-name search body (Dev grammar, docs/49 s.3). No projection: rows come back full (docs/53, 2026-10-18).

    ``unique_fields`` is name + domain so two tenants that share a name are not collapsed into one row (unverified
    against the server: a non-200 or odd reply refuses the run).
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("search_value_invalid")
    return {"tableServerData": {
        "offset": 0, "items_per_page": SEARCH_PAGE_SIZE, "sort": {"direction": "asc", "key": "accountName"},
        "filters": {"and": [{"method": "search", "value": name}]},
        "unique_fields": ["accountName", "accountDomain"]}}


def _pick_ok(value: Any) -> bool:
    return (value is None or isinstance(value, (str, int, float, bool))
            or (isinstance(value, list) and len(value) <= 50 and all(isinstance(x, str) for x in value)))


def readback_reply(status: Any, body: Any) -> dict[str, Any]:
    """Reduce the page's OWN table reply (HTTP status, parsed JSON body) to {status, total, rows} for the readback.

    Each row becomes a flat {dotted path: scalar | list of strings} holding only the READBACK_PICK_PATHS the contract
    compares; no other row field, token, cookie or header leaves this function. A missing status is status 0, a non-200
    status carries no rows, and a 200 with an unexpected schema has ``total``/``rows`` None (fail closed).
    """
    if type(status) is not int:
        return {"status": 0, "total": None, "rows": None}
    if status != 200:
        return {"status": status, "total": None, "rows": None}
    paged = body.get("pagination_response") if isinstance(body, Mapping) else None
    rows = paged.get("table_data") if isinstance(paged, Mapping) else None
    total = paged.get("total_count") if isinstance(paged, Mapping) else None
    if not isinstance(rows, list) or type(total) is not int or not all(isinstance(r, Mapping) for r in rows):
        return {"status": 200, "total": None, "rows": None}
    picked = []
    for row in rows[:200]:
        out: dict[str, Any] = {}
        for key in READBACK_PICK_PATHS:
            value = _lookup(row, key)
            if value is not _MISSING and _pick_ok(value):
                out[key] = value
        picked.append(out)
    return {"status": 200, "total": total, "rows": picked}


# --- Readback diagnostic (docs/53 note 2026-10-18): which page-own read variant returns the compared paths -------------
# NAMES ONLY: a fixed result word, two bounded counts and sorted READBACK_PICK_PATHS names; never a value or a row body.
VARIANT_NAMES = ("A", "B")  # A: page's unique_fields, no projection; B: the strict read (unique_fields override, no projection)
VARIANT_RESULTS = ("ok", "unavailable", "session_expired", "not_found", "ambiguous")
_VARIANT_COUNT_CAP = 1000


def _bounded_count(value: Any) -> int | None:
    return min(value, _VARIANT_COUNT_CAP) if type(value) is int and value >= 0 else None


def variant_summary(reply: Any, tenant_name: str, domain: str) -> dict[str, Any]:
    """Reduce one variant's ``readback_reply`` to {result, rows, total, present, absent} (names only; never a value).

    ``present`` / ``absent`` split READBACK_PICK_PATHS by whether the single matched row (exact name AND domain) carries
    the path; both are empty unless ``result`` is ``ok``. Anything unexpected is ``unavailable`` (fail closed).
    """
    empty: dict[str, Any] = {"result": "unavailable", "rows": None, "total": None, "present": [], "absent": []}
    if not isinstance(reply, Mapping):
        return empty
    status, rows, total = reply.get("status"), reply.get("rows"), reply.get("total")
    if status in (401, 403):
        return {**empty, "result": "session_expired"}
    if status != 200 or not isinstance(rows, list) or type(total) is not int or total < 0:
        return empty
    counts = {"rows": _bounded_count(len(rows)), "total": _bounded_count(total)}
    if total > len(rows):
        return {**empty, **counts, "result": "ambiguous"}
    row, code = select_unique_row(rows, tenant_name, domain)
    if row is None:
        return {**empty, **counts, "result": "not_found" if code == "no_match" else "ambiguous"}
    present = sorted(path for path in READBACK_PICK_PATHS if _lookup(row, path) is not _MISSING)
    return {"result": "ok", **counts, "present": present, "absent": sorted(set(READBACK_PICK_PATHS) - set(present))}


def variant_line_fields(summaries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The part of the printed line: result word and absent path names per variant (A, B), nothing else."""
    return {"variant_result": {name: summaries[name]["result"] for name in VARIANT_NAMES},
            "variant_absent": {name: list(summaries[name]["absent"]) for name in VARIANT_NAMES}}


def variant_line_problem(fields: Any) -> str | None:
    """None only when ``fields`` is exactly the fixed-enum shape of ``variant_line_fields``: result words from
    VARIANT_RESULTS and sorted, unique READBACK_PICK_PATHS names, per variant A, B. No free text can pass."""
    bad = "variant_line_not_fixed_enum"
    if not isinstance(fields, dict) or set(fields) != {"variant_result", "variant_absent"}:
        return bad
    results, absent = fields["variant_result"], fields["variant_absent"]
    if not isinstance(results, dict) or set(results) != set(VARIANT_NAMES):
        return bad
    if not isinstance(absent, dict) or set(absent) != set(VARIANT_NAMES):
        return bad
    if not all(isinstance(word, str) and word in VARIANT_RESULTS for word in results.values()):
        return bad
    allowed = set(READBACK_PICK_PATHS)
    for names in absent.values():
        if not isinstance(names, list) or len(names) > len(allowed):
            return bad
        if not all(isinstance(n, str) and n in allowed for n in names) or names != sorted(set(names)):
            return bad
    return None


def variant_report(summaries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The report's ``variants`` section: per variant the result word, bounded counts and present / absent names."""
    return {name: {key: summaries[name][key] for key in ("result", "rows", "total", "present", "absent")}
            for name in VARIANT_NAMES}


# --- Redacted report -----------------------------------------------------------------------------------------------------
def write_readback_report(base: Path, report: Mapping[str, Any], now: datetime) -> Path:
    """Write ``<base>/production-probe/readback-<UTC>.json`` atomically; ValueError if the report is not redacted."""
    if production_probe.report_problem(report):
        raise ValueError("production_readback_report_not_redacted")
    folder = base / production_probe.REPORT_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (REPORT_PREFIX + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path
