"""The guarded production Surface renewal (owner go 2026-11-03, first CO-0829 Royal Reesink).

Route: ``Surface`` + ``Renewal of Existing Product`` (engine ``surface_renewal``; Q10 had kept single-product renewals out of
scope). The CO is approved after the owner checked the domains by hand ("Approved = manually checked"), so the run changes
ONLY two values on the existing production tenant, by one Edit Account:

* Scanning interval: from the new DealHub Surface tier (``build_renewal_plan``: Prime weekly, Go monthly);
* Expiration date: the new DealHub Surface term end.

Everything else stays: start date, domains, operators, licence counts (a difference with the licensed counts is shown, never
applied: owner 2026-11-03), Leaked Credentials, toggles, primary user. A renewal CO carries no Surface IDs (CO-0829 had none), so
the tenant is the ONE row whose name and primary domain equal the CO's; the guard then allows one edit for that row's id only.
After a verified edit the dashboard writes that row's Account ID and UUID to the CO and moves the stage to User Created
(``surface_renewal_followup``).

This module holds the pure rules: the intended values, the row checks before and after the edit and the result codes. It reads
and writes nothing.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from . import production_contract

ENGINE = "surface_renewal"
PRODUCT = "Surface"
TYPE = "Renewal of Existing Product"
STEP = "renewal_edit"  # the step name of the result lines in ``surface_renewal_followup.WRITES_FILE``

INTERVAL_LABEL = "Scanning interval"
INTERVAL_PATH = "scanningInterval"
EXPIRATION_KEY = "license_end"
EXPIRATION_PATH = "accountLicense.expirationDate"
INTERVALS = ("Weekly", "Monthly")
# The licence counts are shown beside the licensed ones and never changed (owner 2026-11-03).
COUNT_PATHS = (("Number of assets", "accountLicense.assetsNumber", "assets"),
               ("Number of domains", "accountLicense.domainsNumber", "domains"),
               ("Number of subdomains", "accountLicense.subDomainsNumber", "subdomains"))
# The row paths that must read the same after the edit. ``accountLicense.scanningFrequency`` is left out: it may mirror the
# interval. Absent paths are compared as absent.
PRESERVE_PATHS = (
    "id", "accountUuid", "accountName", "accountDomain", "accountType", "accountCountryCode", "enabled", "isDeleted",
    "alternateDomains", "subDomains", "userEmailDomains", "additionalNetworks", "operatorAccounts",
    "primaryUser.email", "primaryUser.firstName", "primaryUser.lastName", "campaignsTimeoutInHours",
    "accountLicense.startDate", "accountLicense.licenseType", "accountLicense.enabled",
    "accountLicense.assetsNumber", "accountLicense.domainsNumber", "accountLicense.subDomainsNumber",
    "accountLicense.leakedCredentialsAllowed", "accountLicense.provisioningEnabled", "accountLicense.notificationsAllowed",
    "accountLicense.multipleUsersAllowed", "accountLicense.apiAccessAllowed", "accountLicense.phishingEnabled",
    "accountLicense.subDomainsNumberAllowed", "accountLicense.reconLevel",
    "leakedCredentialsScanningInterval", "leakedCredentialsScannedDomains", "leakedCredentialsSettings.spyCloudSettings.enabled",
)

VERIFIED = "renewal_edit_verified"
ALREADY_CURRENT = "renewal_already_current"
# Outcomes after which Confirm was clicked (the row may have changed): never repeated automatically.
WRITE_OUTCOMES = frozenset({VERIFIED, "renewal_saved_unverified", "renewal_save_failed", "renewal_save_no_signal",
                            "renewal_save_id_mismatch", "renewal_values_not_kept", "renewal_other_fields_changed",
                            "renewal_session_expired_after_confirm"})
TEXT = {
    VERIFIED: "Scanning interval and Expiration date set in BackOffice by Edit Account and read back; nothing else changed.",
    ALREADY_CURRENT: "The tenant already had this Scanning interval and Expiration date: nothing was changed.",
}

_ID = re.compile(r"[0-9a-f]{24}")
_UUID = re.compile(r"[0-9a-f]{32}")
_DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


class RenewalRefused(ValueError):
    """A fixed reason code: the run stops before any browser."""


@dataclass(frozen=True)
class Intended:
    reference: str
    revision: str
    tenant_name: str
    primary_domain: str
    interval: str
    expiration: date
    term_start: date
    apply_from: date
    licensed: Mapping[str, int]  # assets / domains / subdomains of the new term (shown, never applied)

    def as_mapping(self) -> dict[str, Any]:
        """The ``tenant_name`` / ``primary_domain`` shape ``production_contract.select_unique_row`` callers use."""
        return {"tenant_name": self.tenant_name, "primary_domain": self.primary_domain}


def route_matches(product: Any, onboarding_type: Any) -> bool:
    return product == PRODUCT and onboarding_type == TYPE


def _iso(value: Any, code: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise RenewalRefused(code) from None


def intended_from(co_row: Mapping[str, Any], plan: Mapping[str, Any] | None, today: date) -> Intended:
    """The two target values from the CO row and the ``build_renewal_plan`` result; ``RenewalRefused(code)`` otherwise.

    Codes: renewal_route_mismatch, renewal_not_approved, renewal_source_unavailable, renewal_tenant_name_invalid,
    renewal_primary_domain_invalid, renewal_blocked_<blocker>, renewal_term_missing, renewal_interval_unknown,
    renewal_not_yet_applicable (Guide G8: before ``apply_from``), renewal_expiration_in_past.
    """
    if not isinstance(co_row, Mapping) or not route_matches(co_row.get("Onboarding_Product__c"), co_row.get("Onboarding_Type__c")):
        raise RenewalRefused("renewal_route_mismatch")
    if co_row.get("Onboarding_Approval_Status__c") != "Approved":
        raise RenewalRefused("renewal_not_approved")
    reference, revision = co_row.get("Name"), co_row.get("LastModifiedDate")
    if not isinstance(reference, str) or not isinstance(revision, str) or not revision:
        raise RenewalRefused("renewal_source_unavailable")
    name = " ".join(str(co_row.get("Account_Name__c") or "").split())
    if not name or any(ord(ch) < 32 for ch in name):
        raise RenewalRefused("renewal_tenant_name_invalid")
    domain = str(co_row.get("Main_Domain__c") or "").strip().lower()
    if not _DOMAIN.fullmatch(domain):
        raise RenewalRefused("renewal_primary_domain_invalid")
    if not isinstance(plan, Mapping) or plan.get("engine") != ENGINE:
        raise RenewalRefused("renewal_route_mismatch")
    blockers = plan.get("blockers") or []
    if blockers:
        raise RenewalRefused("renewal_blocked_" + str(blockers[0]))
    term = plan.get("surface_term")
    if not isinstance(term, Mapping):
        raise RenewalRefused("renewal_term_missing")
    interval = term.get("scanning_interval")
    if interval not in INTERVALS:
        raise RenewalRefused("renewal_interval_unknown")
    start = _iso(term.get("start"), "renewal_term_missing")
    end = _iso(term.get("end"), "renewal_term_missing")
    apply_from = _iso(term.get("apply_from"), "renewal_term_missing")
    if today < apply_from:
        raise RenewalRefused("renewal_not_yet_applicable")
    if end <= today:
        raise RenewalRefused("renewal_expiration_in_past")
    licensed = {key: int(term[key]) for _label, _path, key in COUNT_PATHS if isinstance(term.get(key), int)}
    return Intended(reference, revision, name, domain, str(interval), end, start, apply_from, licensed)


# --- the row ---------------------------------------------------------------------------------------------------------------

def _lookup(row: Mapping[str, Any], path: str) -> Any:
    """A flat dotted key first (the page script returns flat rows), else the nested walk; None when absent."""
    if path in row:
        return row[path]
    node: Any = row
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _enum(value: Any) -> str:
    """'Monthly' == 'MONTHLY'; None / '' / 'NO_SCHEDULE' == 'none'."""
    text = re.sub(r"[^a-z0-9]", "", str(value).casefold()) if value not in (None, "") else ""
    return "none" if text in ("", "noschedule") else text


def _row_day(value: Any) -> str | None:
    """The UTC calendar day of a row date given as ISO text or as epoch milliseconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?", value):
        return value[:10]
    return None


def select_row(reply: Any, intended: Intended) -> tuple[str, Mapping[str, Any] | None]:
    """("ok", row) for the ONE live tenant whose name and primary domain equal the CO's, with a well-formed id and UUID."""
    status = reply.get("status") if isinstance(reply, Mapping) else None
    if status in (401, 403):
        return "renewal_session_expired", None
    rows = reply.get("rows") if isinstance(reply, Mapping) else None
    if status != 200 or not isinstance(rows, list):
        return "renewal_row_unavailable", None
    row, code = production_contract.select_unique_row(rows, intended.tenant_name, intended.primary_domain)
    if row is None:
        return {"no_match": "renewal_tenant_not_found"}.get(code or "", "renewal_tenant_ambiguous"), None
    if _lookup(row, "isDeleted") is True:
        return "renewal_tenant_deleted", None
    if not (isinstance(row.get("id"), str) and _ID.fullmatch(row["id"])
            and isinstance(row.get("accountUuid"), str) and _UUID.fullmatch(row["accountUuid"])):
        return "renewal_row_ids_invalid", None
    return "ok", row


def current_values(row: Mapping[str, Any]) -> dict[str, Any]:
    """The two edited values as the row reports them (interval enum, expiration ISO day or None)."""
    return {"interval": _enum(_lookup(row, INTERVAL_PATH)), "expiration": _row_day(_lookup(row, EXPIRATION_PATH))}


def _stable(value: Any) -> str:
    if isinstance(value, list) and all(item is None or isinstance(item, (str, int, bool)) for item in value):
        value = sorted(value, key=str)
    return json.dumps(value, sort_keys=True, default=str)


def fingerprint(row: Mapping[str, Any]) -> dict[str, str]:
    """Stable fingerprints of every preserved path (never logged: the caller keeps it in memory only)."""
    return {path: _stable(_lookup(row, path)) for path in PRESERVE_PATHS}


def count_differences(row: Mapping[str, Any], intended: Intended) -> list[dict[str, Any]]:
    """The licence counts that differ from the new term (shown on the card, never applied)."""
    found = []
    for label, path, key in COUNT_PATHS:
        have, want = _lookup(row, path), intended.licensed.get(key)
        if want is not None and have != want:
            found.append({"field": label, "tenant": have if isinstance(have, int) else None, "licensed": want})
    return found


def before_edit(reply: Any, intended: Intended) -> tuple[str, Mapping[str, Any] | None]:
    """("ok", row) to edit, (``renewal_already_current``, row) when nothing changes, else (code, None) before the form opens."""
    code, row = select_row(reply, intended)
    if row is None:
        return code, None
    now = current_values(row)
    if now["expiration"] is None:
        return "renewal_row_unexpected", None
    if now["expiration"] > intended.expiration.isoformat():
        return "renewal_expiration_would_shorten", None  # someone set a later date: never shortened automatically
    if now["interval"] == _enum(intended.interval) and now["expiration"] == intended.expiration.isoformat():
        return ALREADY_CURRENT, row
    return "ok", row


def after_edit(reply: Any, intended: Intended, account_id: str, before: Mapping[str, str]) -> str:
    """``renewal_edit_verified`` when the re-read row is the same tenant, shows both targets and every preserved path is
    unchanged; else a code (names only, never values)."""
    code, row = select_row(reply, intended)
    if row is None:
        return "renewal_session_expired_after_confirm" if code == "renewal_session_expired" else "renewal_saved_unverified"
    if row.get("id") != account_id:
        return "renewal_saved_unverified"
    if changed_paths(row, before):
        return "renewal_other_fields_changed"
    now = current_values(row)
    if now["interval"] != _enum(intended.interval) or now["expiration"] != intended.expiration.isoformat():
        return "renewal_values_not_kept"
    return VERIFIED


def changed_paths(row: Mapping[str, Any], before: Mapping[str, str]) -> list[str]:
    """Preserved paths whose value moved (names only)."""
    after = fingerprint(row)
    return sorted(path for path, value in before.items() if after.get(path) != value)


def edit_response_problem(method: Any, path_id: Any, status: Any, account_id: str) -> str | None:
    """The app's own edit reply: exactly this tenant and 2xx, else the code."""
    if str(method).upper() != "POST" or path_id != account_id:
        return "renewal_save_id_mismatch"
    if status in (401, 403):
        return "renewal_session_expired_after_confirm"
    if not isinstance(status, int) or not 200 <= status < 300:
        return "renewal_save_failed"
    return None
