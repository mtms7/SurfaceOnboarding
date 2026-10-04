"""Desktop-only, attended Leonardo Development auto-confirm runner.

Two route contracts share the browser, duplicate-check, fill, confirm, and
readback machinery (ROUTES): the CE-only route (case_2_new_ce_only, the
default, unchanged) and the Surface-only route (case_1_new_surface_only,
selected with --route). A RouteContract supplies the source reader, license
dates, fill plan, duplicate lookups, run-log redactions, and whether the
readback may observe a tenant that is already scanning.

The runner deliberately has no Salesforce writeback.  It uses a dedicated,
persisted, desktop-only browser profile (the documented §10 option-3 temporary
development bridge) so the operator's Leonardo Development session is retained
and SSO/MFA is only required when it expires.  That profile is never the
operator's main Chrome, never placed on the VM, and never copied to Git, logs,
or backups.  A read-only session check (check_leonardo_session) reports the
session state and a reset (reset_leonardo_profile) clears it.

One automation Chrome window is reused: each attended operation opens its own
new tab in the already-running automation browser (discovered and verified via
the profile's DevToolsActivePort file and the loopback /json/version endpoint),
or launches it when none is running, and closes only that tab afterwards.
While the window stays open its loopback CDP port lets local processes on the
desktop drive that Leonardo Development session; the operator closes the
automation window (or runs --close-browser) at the end of the day.

After a clear duplicate check and a validated fill, the runner auto-confirms:
it clicks the single Add Account Confirm control exactly once and never retries
an uncertain submit.  After the form closes it re-searches (read-only) and
reads the exact tenant back, recording minimal local readback evidence; it
never updates Salesforce.

The runner is revision-bound: it accepts the source revision acknowledged by
the dashboard and fails closed if its fresh source read differs.  Each
acknowledged revision may be run once; the local state file records the start
and the final result so the dashboard can display it and refuse a retry.  A
completed, non-successful run may be re-armed by reset_runner_record; a
verified creation can never be reset.

The Leonardo Development details-page labels used for the readback
("Surface Account ID", "Account UUID", "Account Scanning") are derived from
the dashboard display names and must be confirmed on the first attended run;
any unavailable or ambiguous control stops the runner without writing
evidence.
"""
from __future__ import annotations

import argparse
import dataclasses
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from typing import Any
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REFERENCE = re.compile(r"CO-[0-9]{4,10}$")
DEVELOPMENT_LOGIN = "https://leonardo.dev.app.pentera.io/login"
TENANT_MANAGEMENT = "https://leonardo.dev.app.pentera.io/backoffice/tenantManagement"
TENANT_MANAGEMENT_PATH = "/backoffice/tenantManagement"
MAX_WAIT_SECONDS = 15 * 60
POLL_SECONDS = 5
# Bounded window for the read-only session check: long enough for a valid
# session to reach tenant-management, short enough to keep the check snappy.
SESSION_CHECK_SECONDS = 25
# Reused automation browser (one window, one new tab per attended run). Chrome
# writes the chosen CDP port (line 1) and its per-instance browser WebSocket
# path (line 2) to this file inside the user-data-dir.
DEVTOOLS_ACTIVE_PORT_FILE = "DevToolsActivePort"
DEVTOOLS_ACTIVE_PORT_MAX_BYTES = 512
BROWSER_LAUNCH_SECONDS = 30.0
BROWSER_CLOSE_SECONDS = 15.0
PROFILE_REMOVE_ATTEMPTS = 5
# The launched window keeps this neutral anchor tab so closing the run's own
# tab never closes the last tab (which would exit the reused browser).
ANCHOR_TAB_URL = "about:blank"
CDP_TARGET_ID = re.compile(r"[A-Za-z0-9-]{1,128}")
CDP_BROWSER_WS_PATH = re.compile(r"/devtools/browser/[A-Za-z0-9-]{1,128}")
RUNNER_STATE_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_ce_only_runner_state.json"
# Latest read-only check per CO (duplicate check / readback): local evidence
# for the dashboard only; it never gates or consumes a create run.
CHECK_STATE_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_ce_only_check_state.json"
CHECK_KINDS = frozenset({"duplicate_check", "readback", "scan_status", "validation"})
READBACK_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_leonardo_readbacks.json"
# Surface-only scan observations (plan §5: Surface-owned, short-lived, never in Salesforce).
SCAN_STATUS_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_scan_status.json"
SCAN_STATUS_TTL = timedelta(hours=6)
# lastScanStatusEnum values whose meaning has been confirmed from a live read.
# Empty until the first live sweep is reviewed: until then a set status shows
# raw and the tenant is "Scan started", never "Scan completed" (fail closed).
# COMPLETED confirmed 2026-10-01 (CO-0649: Leonardo UI showed the finished
# weekly scan, Last Scan Sep 30 15:45 local, duration 03:08:12, matching the
# read's lastReconScan and lastReconScanDurationMilliseconds).
SCAN_STATUS_COMPLETED: frozenset[str] = frozenset({"COMPLETED"})
SCAN_STATUS_FAILED: frozenset[str] = frozenset()
SCAN_STATUS_ENUM_PATTERN = r"[A-Za-z][A-Za-z0-9_]{0,39}"
# Surface validation (2026-10-02): checks of a tenant's own search row against
# the route plan. Values are kept only for booleans, enums, numbers, and dates;
# names, domains, and emails are reduced to ok/drift plus counts.
VALIDATION_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_surface_validation.json"
VALIDATION_TTL = timedelta(hours=6)
DIAGNOSTICS_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_ce_only_diagnostics.json"
READBACK_SOURCE = "Leonardo Development Details readback"
READBACK_STATE = "Account Scanning"
SURFACE_ACCOUNT_ID_PATTERN = r"[A-Za-z0-9]{16,64}"
ACCOUNT_UUID_PATTERN = r"[a-f0-9]{32}"
# CE-only Add Account fill contract (owner-confirmed 2026-09-25, per the
# CO-0702 creation HAR and the 2026-09-24 live form diagnostics). The form
# has no "Email Domains" or "Primary User Email" controls. The accessible
# label is the primary locator for every control; native selects are
# resolved by label and set by option label text; checkboxes are resolved
# by their stable name attribute (the "Scan now" control by its aria name).
CE_PRIMARY_USER_FIRST_NAME = "Milton"
CE_PRIMARY_USER_LAST_NAME = "Stevenson"
CE_PRIMARY_USER_EMAIL_LOCAL = "milton.stevenson"
CE_USER_EMAIL_DOMAIN = "pentera.io"
# All Pentera Core Plus subscriptions include CE; the license dates come from
# the account's Core Plus subscription rows ("Bulk"/"Additional" rows never
# match this prefix).
CE_SUBSCRIPTION_PRODUCT_PREFIX = "Pentera Core Plus Commercial"
# Exact Salesforce picklist values of the only route this runner may create
# (observed live on CO-0679/CO-0728, 2026-09-29).
CE_ROUTE_PRODUCT = "Credential Exposure"
CE_ROUTE_TYPE = "New Product Onboarding"
# License quantities are fixed 1/1/1 for the CE-only pilot; the subscription
# endpoint count is not the license quantity.
CE_LICENSE_QUANTITIES = {
    "Number of assets": "1",
    "Number of domains": "1",
    "Number of subdomains": "1",
}
# License date controls (confirmed by the 2026-09-25 extended read-only
# diagnostic): two plain text inputs with NO label, name, id, or placeholder.
# They are identified only by their stable data-am attributes, which is the
# primary locator. The label candidates below are kept only as a fallback in
# case the form regresses to labeled controls.
LICENSE_DATE_DATA_AM = {
    "license_start": "AddEditTenantModal-date-startDate",
    "license_end": "AddEditTenantModal-date-expirationDate",
}
# Display format the date-picker text inputs accept (strftime pattern),
# confirmed by the operator from the CO-0702 manual creation (YYYY-MM-DD).
# The post-fill re-read still verifies the value and fails closed on any
# mismatch (e.g. if the picker reformats the input).
LICENSE_DATE_INPUT_FORMAT = "%Y-%m-%d"
# Live form (2026-09-29 probe): the date inputs are readonly, display
# "Sep 29, 2026", and are set through a Material-UI (v3/v4) picker dialog.
LICENSE_DATE_DISPLAY_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d")
PICKER_DIALOG_SELECTOR = ".MuiPickersModal-dialogRoot"
PICKER_HEADER_SELECTOR = ".MuiPickersCalendarHeader-transitionContainer"
PICKER_ARROW_SELECTOR = ".MuiPickersCalendarHeader-switchHeader button"
PICKER_DAY_SELECTOR = "button.MuiPickersDay-day:not(.MuiPickersDay-hidden)"
PICKER_DAY_DISABLED_CLASS = "MuiPickersDay-dayDisabled"
PICKER_DAY_SELECTED_SELECTOR = "button.MuiPickersDay-daySelected:not(.MuiPickersDay-hidden)"
# react-transition-group classes present only while a month slide runs.
PICKER_TRANSITION_SELECTOR = '[class*="MuiPickersSlideTransition-slideE"]'
PICKER_MAX_MONTH_STEPS = 60
DRY_RUN_MODE = "dry_run"
ADD_ACCOUNT_MODAL_SELECTOR = ".tenants-add-account"
PICKER_SETTLE_POLLS = 20  # x 100 ms per month step
LICENSE_DATE_LABEL_CANDIDATES = {
    "license_start": ("Start date", "Start Date", "License start date", "License start"),
    "license_end": ("Expiration date", "Expiration Date", "License expiration date", "Expiration", "End date"),
}
# Observed Account Scanning states accepted in local readback evidence.
READBACK_STATES = frozenset({"Account Scanning", "No scan started"})
# Normalized (casefolded, whitespace-collapsed, trailing period removed)
# MUIDataTable zero-result messages. Only these exact texts mark a clean,
# zero-result search; anything else in a short row fails closed.
EMPTY_STATE_MESSAGES = frozenset({
    "no records found", "no matching records found", "sorry, no matching records found",
})
# Re-reads of an unclassifiable table after a search (1 s apart) so a
# transient loading row can settle; a table that never settles fails closed.
TABLE_SETTLE_RETRIES = 4
DEVELOPMENT_ORIGIN = "https://leonardo.dev.app.pentera.io"
# Add Account <select> controls by their stable name attribute (the live
# selects have no associated <label>; 2026-09-29 diagnostics).
SELECT_NAMES = {
    "Account Type": "accountType",
    "Country": "accountCountry",
    "Scanning interval": "scanningInterval",
    "Leaked Credentials scanning interval": "leakedCredentialsScanningInterval",
    "Type": "licenseType",
}
# Bounded per-field wait so a disabled/hidden control fails fast and is logged
# instead of hanging on Playwright's 30 s default.
FIELD_TIMEOUT_MS = 5_000
TOGGLE_CLICK_TIMEOUT_MS = 2_000
CONFIRM_ENABLE_POLLS = 12  # x 250 ms
DIAGNOSE_SETTLE_MS = 700  # per no-submit Confirm probe (--diagnose-confirm)
# Toggles no diagnostic probe may ever switch ON (a Surface-only tenant has no CE).
DIAGNOSE_NEVER_ENABLE = frozenset({"leakedCredentialsAllowed", "phishingEnabled"})
FORM_CLOSE_POLL_SECONDS = 0.5
# Server-side tenant search (getAllDetailedAccounts per edit, HAR-verified):
# wait for the response that carries this lookup instead of a fixed sleep.
TENANT_SEARCH_API = "getAllDetailedAccounts"
SEARCH_RESPONSE_TIMEOUT_MS = 10_000
ACCOUNT_ADD_API = "/backoffice/account/add"
# Selects that can reset dependent controls are set before the toggles.
EARLY_SELECTS = ("Account Type", "Type", "Scanning interval")
# The Surface/web toggles live under a collapsed "Advanced options" section
# (2026-09-29 probe). Three of them default ON and must be OFF for CE-only
# (owner decision 2026-09-29); the rest default OFF and are pinned OFF so the
# form is verified against the CO-0702 HAR contract. Two dependent controls
# (MAS for subdomains, Web Agent) are disabled by the form and left alone.
ADVANCED_OPTIONS_TEXT = "Advanced options"
ADVANCED_TOGGLES_OFF = (
    "automatedDiscoveryEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled",
    "webDorkingEnabled", "fullNucleiScanEnabled", "authenticatedTestingEnabled",
    "staticOutboundIpEnabled", "aiEnabled", "multipleAttackStacksEnabled",
)
ADVANCED_EXPAND_POLLS = 20  # x 100 ms
RUN_LOG_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_ce_only_run_log.json"
RUN_LOG_MAX_EVENTS = 700
# Diagnose timeline: which run-log steps trigger a Confirm/state snapshot.
TIMELINE_STEP_PREFIXES = ("add_account_open", "fill_", "absent_toggle", "advanced_options", "max_scan_duration",
                          "verify_", "pick_date", "diagnose_lc_prefill")
# Value-free snapshot: Confirm enabled/disabled and the boolean hooks of the
# form component (the one whose state object carries "<field>Error" keys).
TIMELINE_SNAPSHOT_JS = """() => {
    const b = Array.from(document.querySelectorAll('button')).find(x => (x.textContent || '').trim() === 'Confirm');
    if (!b) return 'confirm=absent';
    const s = 'confirm=' + (b.disabled ? 'disabled' : 'enabled');
    const key = Object.keys(b).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
    if (!key) return s;
    let f = b[key];
    for (let d = 0; f && d < 30; d++, f = f.return) {
        let h = f.memoizedState; const bools = []; let form = false;
        for (let i = 0; h && typeof h === 'object' && 'next' in h && i < 60; i++, h = h.next) {
            const v = h.memoizedState;
            if (typeof v === 'boolean') bools.push(i + ':' + v);
            else if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).some(k => /Error$/.test(k))) form = true;
        }
        if (form) return s + ' form_hooks=' + bools.join(',');
    }
    return s;
}"""
RUN_LOG_MAX_RUNS = 20
RUN_LOG_DETAIL_CHARS = 300
RUN_LOG_MAX_DIAGNOSTICS = 3


class RunnerStateUnavailable(RuntimeError):
    """The local runner state file is unreadable or violates its schema."""


class LoginTimeout(Exception):
    """The operator did not reach tenant-management within the wait window."""


@dataclass(frozen=True, slots=True)
class CeOnlyNames:
    tenant_name: str
    primary_user_alias: str


def _account_display_name_and_alias(account_name: str) -> tuple[str, str]:
    """Shared naming rule: (display name without trailing periods, email alias).

    Used by every attended route so the primary-user alias rule is identical:
    names of 15 characters or fewer drop spaces and symbols; longer names use
    word initials; the alias is always lowercase.
    """
    compact = " ".join(account_name.split())
    if not compact:
        raise ValueError("account_name_unavailable")
    # Owner decision 2026-09-29: a trailing period is dropped from the tenant
    # name ("Tango Group Ltd." -> "Tango Group Ltd - CE Only"). Only trailing
    # periods are removed; internal ones stay. The alias below is unchanged.
    display_name = compact.rstrip(". ")
    if not display_name:
        raise ValueError("account_name_unavailable")
    alphanumeric = re.findall(r"[A-Za-z0-9]+", compact)
    if not alphanumeric:
        raise ValueError("account_name_unavailable")
    if len(compact) <= 15:
        alias = "".join(alphanumeric).casefold()
    else:
        alias = "".join(word[0] for word in alphanumeric).casefold()
    if not alias:
        raise ValueError("primary_user_alias_unavailable")
    return display_name, alias


def ce_only_names(account_name: str) -> CeOnlyNames:
    """Derive the owner-approved CE-only tenant name and Pentera alias."""
    display_name, alias = _account_display_name_and_alias(account_name)
    return CeOnlyNames(display_name + " - CE Only", alias)


def surface_names(account_name: str) -> CeOnlyNames:
    """Surface-only tenant name (the account name, trailing periods removed,
    no suffix) and the same Pentera alias rule as the CE-only route."""
    display_name, alias = _account_display_name_and_alias(account_name)
    return CeOnlyNames(display_name, alias)


def one_email_domain(value: object) -> str | None:
    """Return exactly one valid CE domain; no normalization expands its scope."""
    if not isinstance(value, str):
        return None
    domains = [item.casefold() for item in re.split(r"[,;\s]+", value.strip()) if item]
    if len(domains) != 1:
        return None
    domain = domains[0]
    pattern = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
    return domain if re.fullmatch(pattern, domain) else None


def sf_command() -> str:
    return os.environ.get("SURFACE_SF_CLI", "sf.cmd" if os.name == "nt" else "sf")


def sf_target_org() -> str:
    """The Salesforce CLI alias every runner read is pinned to (the dashboard's alias).

    Since 2026-10-03 the dashboard no longer sets a global default org, so an
    unpinned read finds no org (every validation sweep failed that way).
    """
    alias = os.environ.get("SURFACE_SF_TARGET_ORG", "surface-onboarding")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", alias):
        raise RuntimeError("invalid_salesforce_target_org")
    return alias


def source_for_fill(reference: str) -> tuple[str, str, str, str, str]:
    """Fresh, fixed-field source read; retain values only in this process.

    For a Credential Exposure the Primary Domain is the single Email Domains
    value; Main_Domain__c is not required and is not used.
    """
    if not REFERENCE.fullmatch(reference):
        raise RuntimeError("invalid_co_reference")
    query = (
        "SELECT Name, LastModifiedDate, Account_Name__c, "
        "Email_Domains__c FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2"
    )
    try:
        # Decode CLI output as UTF-8 (the --json contract) rather than the
        # locale code page; cp1252 cannot decode UTF-8 continuation bytes and
        # would otherwise leave completed.stdout as None and fail the read.
        completed = subprocess.run([sf_command(), "data", "query", "--query", query, "--json",
                                    "--target-org", sf_target_org()],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                   timeout=45, check=False)
        if completed.stdout is None:
            raise ValueError()
        payload = json.loads(completed.stdout)
        rows = payload["result"]["records"]
        if completed.returncode or payload["status"] != 0 or not isinstance(rows, list) or len(rows) != 1:
            raise ValueError()
        row = rows[0]
        if not isinstance(row, dict) or row.get("Name") != reference:
            raise ValueError()
        revision, account = row.get("LastModifiedDate"), row.get("Account_Name__c")
        email_domain = one_email_domain(row.get("Email_Domains__c"))
        if not all(isinstance(value, str) and value.strip() for value in (revision, account, email_domain)):
            raise ValueError()
        # CE-only: the Primary Domain is the single Email Domains value.
        return revision, account, email_domain, email_domain, ce_only_names(account).tenant_name
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("salesforce_fill_source_unavailable") from exc


def _sf_records(query: str) -> list[Any]:
    """Run one read-only sf query and return its records, failing closed.

    A timeout (subprocess.TimeoutExpired is not an OSError) becomes a
    ValueError, so every caller reports its normal "source unavailable" code
    instead of crashing the run (review item 4, 2026-10-01).
    """
    try:
        completed = subprocess.run(
            [sf_command(), "data", "query", "--query", query, "--json", "--target-org", sf_target_org()],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=45, check=False,
        )
    except subprocess.SubprocessError as exc:
        raise ValueError("salesforce_cli_timeout") from exc
    except RuntimeError as exc:  # an invalid alias setting: a normal "source unavailable"
        raise ValueError("invalid_salesforce_target_org") from exc
    if completed.stdout is None:
        raise ValueError()
    payload = json.loads(completed.stdout)
    records = payload["result"]["records"]
    if completed.returncode or payload["status"] != 0 or not isinstance(records, list):
        raise ValueError()
    return records


def _parse_subscription_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def select_ce_subscription(rows: list[Any]) -> tuple[date, date]:
    """Select the CE license subscription dates, failing closed.

    Prefers "Pentera Core Plus Commercial" rows (all Pentera Core Plus
    subscriptions include CE); "Bulk"/"Additional" rows never match the
    prefix. Every matching row must carry parseable dates with end after
    start, and all matching rows must agree on the same (start, end) pair.
    """
    matches: list[tuple[date, date]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product = row.get("Product_Full_Name__c")
        if not isinstance(product, str):
            continue
        if not product.casefold().startswith(CE_SUBSCRIPTION_PRODUCT_PREFIX.casefold()):
            continue
        # "Bulk"/"Additional" rows never carry the CE license, even when their
        # product name starts with the Core Plus prefix.
        if "bulk" in product.casefold() or "additional" in product.casefold():
            continue
        start = _parse_subscription_date(row.get("DealHub_Subscription_Start_Date__c"))
        end = _parse_subscription_date(row.get("DealHub_Subscription_End_Date__c"))
        if start is None or end is None or end <= start:
            raise RuntimeError("ce_subscription_ambiguous")
        matches.append((start, end))
    if not matches:
        raise RuntimeError("ce_subscription_unavailable")
    if any(pair != matches[0] for pair in matches[1:]):
        raise RuntimeError("ce_subscription_ambiguous")
    return matches[0]


def _add_one_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:  # February 29
        return value.replace(year=value.year + 1, day=28)


def ce_license_dates(subscription_start: date, subscription_end: date) -> tuple[date, date]:
    """CE license dates per the owner-confirmed rule (2026-09-25).

    startDate is the Core Plus subscription start; expirationDate is one
    year minus one day from the start, capped at the subscription end.
    (Future renewal rule: a CE renewal never modifies the start date.)
    """
    annual_end = _add_one_year(subscription_start) - timedelta(days=1)
    return subscription_start, min(annual_end, subscription_end)


def _run_day() -> date:
    """The local calendar day of this attended run (patched in tests)."""
    return date.today()


def ce_run_license_dates(subscription_start: date, subscription_end: date,
                         run_day: date | None = None) -> tuple[date, date]:
    """License dates entered for an attended Development run.

    Owner decision 2026-09-29: Leonardo Development refuses a start date after
    the current day, so the license starts on the day the onboarding runs.
    The expiration keeps the contract rule (ce_license_dates). Raises
    ValueError ("ce_license_dates_unavailable") when that expiration is not
    after the run day, so the run stops before any browser work.
    """
    day = run_day or _run_day()
    _contract_start, expiration = ce_license_dates(subscription_start, subscription_end)
    if expiration <= day:
        raise ValueError("ce_license_dates_unavailable")
    return day, expiration


@dataclass(frozen=True, slots=True)
class CeFillSource:
    reference: str
    source_revision: str
    account_id: str
    account_name: str
    email_domain: str
    country: str
    subscription_start: date
    subscription_end: date
    tenant_name: str
    primary_user_alias: str
    # True when Account_UUID__c already holds a value (pre-create gate).
    salesforce_id_present: bool = False


class RouteMismatch(RuntimeError):
    """The CO is not a new Credential Exposure onboarding; nothing may run."""

    def __init__(self) -> None:
        super().__init__("ce_route_mismatch")


def ce_fill_source(reference: str) -> CeFillSource:
    """Fresh, fixed-field CE fill source read (CO + DealHub subscription).

    Fails closed (RuntimeError) on any missing or ambiguous value: the CO
    must be a single row with a valid account id, account name, country,
    and one valid email domain, and the account must carry an unambiguous
    Pentera Core Plus Commercial subscription.
    """
    if not REFERENCE.fullmatch(reference):
        raise RuntimeError("invalid_co_reference")
    try:
        rows = _sf_records(
            "SELECT Name, LastModifiedDate, Account__c, Account_Name__c, "
            "Email_Domains__c, Account_Country__c, Onboarding_Product__c, Onboarding_Type__c, Account_UUID__c "
            "FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2")
        if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("Name") != reference:
            raise ValueError()
        row = rows[0]
        # Route gate: this runner creates CE-only tenants, so the CO must be
        # exactly a new Credential Exposure onboarding (2026-09-29 audit: the
        # route was previously inferred from the email-domain count alone).
        if (row.get("Onboarding_Product__c") != CE_ROUTE_PRODUCT
                or row.get("Onboarding_Type__c") != CE_ROUTE_TYPE):
            raise RouteMismatch()
        revision = row.get("LastModifiedDate")
        account_id = row.get("Account__c")
        account_name = row.get("Account_Name__c")
        country = row.get("Account_Country__c")
        if not isinstance(revision, str) or not revision:
            raise ValueError()
        if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
            raise ValueError()
        if not isinstance(account_name, str) or not " ".join(account_name.split()):
            raise ValueError()
        if not isinstance(country, str) or not " ".join(country.split()):
            raise ValueError()
        email_domain = one_email_domain(row.get("Email_Domains__c"))
        if email_domain is None:
            raise ValueError()
        names = ce_only_names(account_name)
        subscription_rows = _sf_records(
            "SELECT Product_Full_Name__c, DealHub_Subscription_Start_Date__c, "
            "DealHub_Subscription_End_Date__c FROM DealHub_Subscription__c "
            "WHERE DealHub_Account__c = '" + account_id + "' LIMIT 100")
        subscription_start, subscription_end = select_ce_subscription(subscription_rows)
        return CeFillSource(
            reference, revision, account_id, " ".join(account_name.split()),
            email_domain, " ".join(country.split()),
            subscription_start, subscription_end,
            names.tenant_name, names.primary_user_alias,
            salesforce_id_present=bool((row.get("Account_UUID__c") or "").strip()))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("salesforce_fill_source_unavailable") from exc


def build_ce_only_fill(source: CeFillSource, run_day: date | None = None) -> dict[str, Any]:
    """Build the full CE-only Add Account fill plan from one fresh source read.

    The contract (owner-confirmed 2026-09-25, per the CO-0702 creation HAR):
    company name "<Account> - CE Only", customer type, the single Email
    Domains value as primary domain, pentera.io user email domain, Milton
    Stevenson as primary user with the lowercase-alias organization email,
    the Salesforce country, no account scan, weekly leaked-credentials scan
    on the primary domain, a prepaid annual subscription with 1/1/1
    quantities and the CE license date rule, tenant MFA required (owner
    decision 2026-09-29, matching the CO-0702 manual creation HAR and the
    Leonardo default), and every feature toggle off except Leaked
    Credentials, Provisioning, and Subdomains.
    """
    start, end = ce_run_license_dates(source.subscription_start, source.subscription_end, run_day)
    return {
        "texts": {
            "Company name": source.tenant_name,
            "Company primary domain": source.email_domain,
            "User email domains  (Comma Separated Values)": CE_USER_EMAIL_DOMAIN,
            "First name": CE_PRIMARY_USER_FIRST_NAME,
            "Last name": CE_PRIMARY_USER_LAST_NAME,
            "Organization Email": (
                f"{CE_PRIMARY_USER_EMAIL_LOCAL}+{source.primary_user_alias}@{CE_USER_EMAIL_DOMAIN}"),
            "Leaked Credentials scanned domains (Comma Separated Values)": source.email_domain,
            **CE_LICENSE_QUANTITIES,
        },
        "selects": {
            "Account Type": "Customer",
            "Country": source.country,
            "Scanning interval": "None",
            "Leaked Credentials scanning interval": "Weekly",
            "Type": "Prepaid annual subscription",
        },
        "checkboxes": {
            "mfaRequired": True,
            "scan_now": False,
            **{key: False for key in ADVANCED_TOGGLES_OFF},
            "notificationsAllowed": False,
            "multipleUsersAllowed": False,
            "apiAccessAllowed": False,
            "phishingEnabled": False,
            "leakedCredentialsAllowed": True,
            "provisioningEnabled": True,
            "subDomainsNumberAllowed": True,
        },
        "license_start": start,
        "license_end": end,
    }


# --- Surface-only route (Case 1, engine case_1_new_surface_only) -------------
# Owner decisions 2026-09-29 (operator), per the Guru card "Surface Customer
# Onboarding - New Surface Account Only" (docs/37_CASE1_SURFACE_ONLY_GURU_NOTES).
CE_ENGINE = "case_2_new_ce_only"
SURFACE_ENGINE = "case_1_new_surface_only"
SURFACE_ROUTE_PRODUCT = "Surface"
SURFACE_ROUTE_TYPE = "New Product Onboarding"
# Engine values a runner-state record may carry in its optional "route" key.
RUNNER_STATE_ROUTES = frozenset({CE_ENGINE, SURFACE_ENGINE, "case_3_combined_baseline"})
# Core Plus detection lives behind is_core_plus_baseline_row so the rule can
# change in one place: any "Pentera Core Plus ..." baseline row (Commercial,
# Enterprise, any tier); "Bulk"/"Additional" rows are add-ons of a Core Plus
# baseline and are ignored. Owner decision 2026-09-29: on an exact Surface /
# New Product Onboarding CO a Core Plus row does NOT block; the Surface-only
# tenant is created (Leaked Credentials OFF) and the dashboard reminds the
# operator to enable Credential Exposure later.
CORE_PLUS_PRODUCT_PREFIX = "Pentera Core Plus"
CORE_PLUS_ADDON_MARKERS = ("bulk", "additional")
# A Surface baseline/add-on row counts when Active, or when Pending and its
# subscription starts within this many days of the run day. Other statuses
# (Expired, later-starting Pending, ...) are ignored.
SURFACE_PENDING_START_WINDOW_DAYS = 14
# Owner decision 2026-10-04: an Approved CO (Onboarding_Approval_Status__c =
# "Approved", the team's review) counts a Pending baseline whatever its start
# date (CO-0757 started Nov 3, 30 days out). Not Approved keeps the window.
APPROVED_STATUS = "Approved"
# Owner decision 2026-10-04 on timing: in production a tenant is onboarded 2 days
# before the subscription starts, unless the CSM asks for immediate onboarding
# (force option). Leonardo Development onboards immediately, for manual checks.
ONBOARD_DAYS_BEFORE_START = 2


def earliest_onboarding_day(subscription_start: date) -> date:
    return subscription_start - timedelta(days=ONBOARD_DAYS_BEFORE_START)


def onboarding_allowed_now(subscription_start: date, run_day: date, *, environment: str = "dev",
                           csm_immediate: bool = False) -> bool:
    """Development: always. Production: from 2 days before the start, or earlier on a CSM request."""
    if environment == "dev" or csm_immediate:
        return True
    return run_day >= earliest_onboarding_day(subscription_start)
SURFACE_LICENSE_ASSETS = "10000"
SURFACE_MAX_SCAN_DURATION_LABEL = "Maximum scan Duration (hours)"
SURFACE_MAX_SCAN_DURATION_HOURS = "90"
ALTERNATE_DOMAINS_LABEL = "Alternate Domains (Comma Separated Values)"
SUBDOMAINS_LABEL = "SubDomains (Comma Separated Values)"
NETWORKS_LABEL = "Networks (Comma Separated Values)"
USER_EMAIL_DOMAINS_LABEL = "User email domains  (Comma Separated Values)"
# The Operator Account control is a react-select text input (2026-09-24 form
# inventory: placeholder "Select Operator Accounts"). Development has no
# operator mapping, so it must stay empty (owner decision: Dev skip).
OPERATOR_ACCOUNT_INPUT_SELECTOR = 'input[placeholder="Select Operator Accounts"]'
# Surface advanced-option profile (Guru card; MAS for subdomains and Web Agent
# are disabled by the form and left untouched).
SURFACE_ADVANCED_TOGGLES = {
    "automatedDiscoveryEnabled": False,
    "subDomainsReconEnabled": True,
    "webDictionaryBruteForceEnabled": True,
    "webDorkingEnabled": False,
    "fullNucleiScanEnabled": True,
    "authenticatedTestingEnabled": False,
    "staticOutboundIpEnabled": False,
    "aiEnabled": False,
    "multipleAttackStacksEnabled": False,
}


class SurfaceSourceError(RuntimeError):
    """A stable, value-free Surface source result code (fail closed)."""


def is_core_plus_baseline_row(product: object) -> bool:
    """True for a "Pentera Core Plus ..." baseline row (CE purchased on the account)."""
    if not isinstance(product, str):
        return False
    name = " ".join(product.split()).casefold()
    return (name.startswith(CORE_PLUS_PRODUCT_PREFIX.casefold())
            and not any(marker in name for marker in CORE_PLUS_ADDON_MARKERS))


def _row_status(row: dict[str, Any]) -> str:
    status = row.get("DealHub_Status__c")
    return " ".join(status.split()).casefold() if isinstance(status, str) else ""


def _surface_row_counts(status: str, start: date | None, run_day: date, approved: bool = False) -> bool:
    """Owner rule: Active counts; Pending counts when it starts within the window, or at all once Approved."""
    if status == "active":
        return True
    if status == "pending":
        if start is None:
            return False
        return approved or start <= run_day + timedelta(days=SURFACE_PENDING_START_WINDOW_DAYS)
    return False


@dataclass(frozen=True, slots=True)
class SurfaceEntitlement:
    tier: str
    scanning_interval: str
    baseline_subdomains: int
    addon_subdomains: int
    product_domains: int | None
    subscription_start: date
    subscription_end: date
    core_plus_present: bool = False

    @property
    def licensed_subdomains(self) -> int:
        return self.baseline_subdomains + self.addon_subdomains


def select_surface_entitlement(rows: list[Any], run_day: date | None = None, *,
                               approved: bool = False) -> SurfaceEntitlement:
    """Select exactly one counted Surface baseline plus its subdomain add-ons.

    A Surface row counts when Active, or Pending with a start no later than
    run day + SURFACE_PENDING_START_WINDOW_DAYS; Expired and later Pending
    rows are ignored. A Core Plus baseline row (is_core_plus_baseline_row)
    that is not Expired never blocks: it only sets core_plus_present (CE is
    enabled later, separately). Fails closed (SurfaceSourceError) on: a
    product row without a name, or a Surface row without a status
    (surface_product_unrecognized / surface_subscription_invalid); an
    unrecognized "Pentera Surface" product; zero or several counted
    baselines (surface_baseline_unavailable / surface_baseline_ambiguous); a
    baseline without an approved tier (surface_tier_unknown); unparseable
    baseline dates (surface_subscription_invalid).
    """
    from phase1_validator.surface_source_readiness import classify_surface_product, surface_scanning_interval

    day = run_day or _run_day()
    baselines: list[tuple[Any, date, date]] = []
    addon_subdomains = 0
    core_plus_present = False
    for row in rows:
        if not isinstance(row, dict):
            raise SurfaceSourceError("surface_subscription_invalid")
        product = row.get("Product_Full_Name__c")
        if not isinstance(product, str) or not product.strip():
            raise SurfaceSourceError("surface_product_unrecognized")
        status = _row_status(row)
        start = _parse_subscription_date(row.get("DealHub_Subscription_Start_Date__c"))
        end = _parse_subscription_date(row.get("DealHub_Subscription_End_Date__c"))
        if is_core_plus_baseline_row(product):
            # Conservative: an unknown status still shows the (non-blocking)
            # "enable Credential Exposure later" reminder.
            if status not in ("expired", "cancelled", "canceled", "inactive"):
                core_plus_present = True
            continue
        classified = classify_surface_product(product)
        if classified is None:
            continue
        if classified.kind == "unrecognized":
            raise SurfaceSourceError("surface_product_unrecognized")
        if not status:
            raise SurfaceSourceError("surface_subscription_invalid")
        if not _surface_row_counts(status, start, day, approved):
            continue
        if classified.kind == "baseline":
            if start is None or end is None or end <= start:
                raise SurfaceSourceError("surface_subscription_invalid")
            baselines.append((classified, start, end))
        elif classified.kind == "subdomain_addon":
            addon_subdomains += classified.subdomains
    if not baselines:
        raise SurfaceSourceError("surface_baseline_unavailable")
    if len(baselines) != 1:
        raise SurfaceSourceError("surface_baseline_ambiguous")
    baseline, start, end = baselines[0]
    interval = surface_scanning_interval(baseline.tier)
    if interval is None:
        raise SurfaceSourceError("surface_tier_unknown")
    return SurfaceEntitlement(baseline.tier, interval, baseline.subdomains, addon_subdomains,
                              baseline.domains, start, end, core_plus_present)


_PSL: Any = None


def _public_suffix_list() -> Any:
    """The pinned Public Suffix List snapshot (loaded once; local file only)."""
    global _PSL
    if _PSL is None:
        from phase2_leonardo.intake import PublicSuffixList
        _PSL = PublicSuffixList()
    return _PSL


def classify_surface_domains(main_domain: object, alternate_domains: object) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Return (main root, alternate roots, subdomains) with the shared normalizer.

    Main_Domain__c must be one valid registrable root (not a subdomain, not a
    public suffix, not a network). Alternate_Domains__c entries (comma,
    semicolon, or newline separated) are split into alternate roots and
    subdomains; a duplicate of the main root is dropped. Networks/IPs are not
    supported in v1 (surface_networks_not_supported); wildcards and malformed
    entries fail closed (surface_domains_invalid).
    """
    from phase2_leonardo.intake import IntakeError, normalize_domain_candidate

    psl = _public_suffix_list()
    if not isinstance(main_domain, str) or not main_domain.strip():
        raise SurfaceSourceError("surface_main_domain_invalid")
    try:
        main = normalize_domain_candidate(main_domain, psl)
    except IntakeError as exc:
        raise SurfaceSourceError("surface_main_domain_invalid") from exc
    if main.kind != "root_domain":
        raise SurfaceSourceError("surface_main_domain_invalid")
    if alternate_domains is None:
        alternate_domains = ""
    if not isinstance(alternate_domains, str):
        raise SurfaceSourceError("surface_domains_invalid")
    roots: set[str] = set()
    subdomains: set[str] = set()
    for raw in (item.strip() for item in re.split(r"[,;\r\n]+", alternate_domains)):
        if not raw:
            continue
        try:
            candidate = normalize_domain_candidate(raw, psl)
        except IntakeError as exc:
            raise SurfaceSourceError("surface_domains_invalid") from exc
        if candidate.kind == "network":
            raise SurfaceSourceError("surface_networks_not_supported")
        if candidate.kind == "root_domain":
            if candidate.value != main.value:
                roots.add(candidate.value)
        elif candidate.kind == "subdomain":
            subdomains.add(candidate.value)
        else:
            raise SurfaceSourceError("surface_domains_invalid")
    return main.value, tuple(sorted(roots)), tuple(sorted(subdomains))


@dataclass(frozen=True, slots=True)
class SurfaceFillSource:
    reference: str
    source_revision: str
    account_id: str
    account_name: str
    country: str
    main_domain: str
    alternate_domains: tuple[str, ...]
    subdomains: tuple[str, ...]
    entitlement: SurfaceEntitlement
    tenant_name: str
    primary_user_alias: str
    # True when Surface_Account_ID__c already holds a value (pre-create gate).
    salesforce_id_present: bool = False

    @property
    def listed_domains(self) -> int:
        """Domains the CO lists: 1 (main) + the alternate root domains."""
        return 1 + len(self.alternate_domains)

    @property
    def number_of_domains(self) -> int:
        """Owner rule 2026-09-30: the licensed subdomains (baseline + DealHub add-ons).

        Replaces the 2026-09-29 rule (1 + alternate roots). The listed domains
        must fit in it (surface_fill_source fails closed otherwise).
        """
        return self.entitlement.licensed_subdomains

    @property
    def subscription_start(self) -> date:
        return self.entitlement.subscription_start

    @property
    def subscription_end(self) -> date:
        return self.entitlement.subscription_end

    @property
    def core_plus_present(self) -> bool:
        return self.entitlement.core_plus_present


def surface_fill_source(reference: str, run_day: date | None = None) -> SurfaceFillSource:
    """Fresh, fixed-field Surface-only fill source read (CO + DealHub rows).

    The CO must be exactly Onboarding_Product__c "Surface" with
    Onboarding_Type__c "New Product Onboarding" (surface_route_mismatch).
    Fails closed with a stable SurfaceSourceError code on any missing,
    ambiguous, or unsupported value. Values stay in this process only.
    """
    if not REFERENCE.fullmatch(reference):
        raise RuntimeError("invalid_co_reference")
    try:
        rows = _sf_records(
            "SELECT Name, LastModifiedDate, Account__c, Account_Name__c, Account_Country__c, "
            "Main_Domain__c, Alternate_Domains__c, Onboarding_Product__c, Onboarding_Type__c, Surface_Account_ID__c, "
            "Onboarding_Approval_Status__c "
            "FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2")
        if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("Name") != reference:
            raise ValueError()
        row = rows[0]
        if (row.get("Onboarding_Product__c") != SURFACE_ROUTE_PRODUCT
                or row.get("Onboarding_Type__c") != SURFACE_ROUTE_TYPE):
            raise SurfaceSourceError("surface_route_mismatch")
        source, _subscription_rows = _surface_source_from_row(reference, row, run_day)
        return dataclasses.replace(
            source, salesforce_id_present=bool((row.get("Surface_Account_ID__c") or "").strip()))
    except SurfaceSourceError:
        raise
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SurfaceSourceError("surface_source_unavailable") from exc


def _surface_source_from_row(reference: str, row: dict[str, Any],
                             run_day: date | None = None) -> tuple[SurfaceFillSource, list[Any]]:
    """Validate one CO row and read its DealHub rows into a Surface source.

    Shared by the Surface-only and the combined (Case 3) routes; the caller
    checks the route and wraps errors. Returns the source and the DealHub rows.
    """
    revision = row.get("LastModifiedDate")
    account_id = row.get("Account__c")
    account_name = row.get("Account_Name__c")
    country = row.get("Account_Country__c")
    if not isinstance(revision, str) or not revision:
        raise ValueError()
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
        raise ValueError()
    if not isinstance(account_name, str) or not " ".join(account_name.split()):
        raise ValueError()
    if not isinstance(country, str) or not " ".join(country.split()):
        raise ValueError()
    main, roots, subdomains = classify_surface_domains(row.get("Main_Domain__c"), row.get("Alternate_Domains__c"))
    names = surface_names(account_name)
    subscription_rows = _sf_records(
        "SELECT Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, "
        "DealHub_Subscription_End_Date__c FROM DealHub_Subscription__c "
        "WHERE DealHub_Account__c = '" + account_id + "' LIMIT 100")
    entitlement = select_surface_entitlement(subscription_rows, run_day,
                                             approved=row.get("Onboarding_Approval_Status__c") == APPROVED_STATUS)
    if 1 + len(roots) > entitlement.licensed_subdomains:
        raise SurfaceSourceError("surface_domains_exceed_license")
    return SurfaceFillSource(
        reference, revision, account_id, " ".join(account_name.split()), " ".join(country.split()),
        main, roots, subdomains, entitlement, names.tenant_name, names.primary_user_alias), subscription_rows


# --- Combined route (Case 3, engine case_3_combined_baseline) ----------------
# Owner decisions 2026-10-01: Case 3 = the Surface-only contract plus the CE
# contract's Leaked Credentials, on one tenant. The term rule fails closed:
# the Surface and Core Plus (CE) expirations must agree (1a); LC scans the
# single CE email domain (2a); both Salesforce IDs are written (3a); the
# duplicate check also searches the CE email domain (4a).
CASE3_ENGINE = "case_3_combined_baseline"
CASE3_ROUTE_PRODUCT = "Surface & Credential Exposure"
CASE3_ROUTE_TYPE = "New Product Onboarding"


@dataclass(frozen=True)
class Case3FillSource:
    """Surface source plus the CE email domain and Core Plus term (in memory only)."""

    surface: SurfaceFillSource
    ce_email_domain: str
    ce_subscription_start: date
    ce_subscription_end: date
    salesforce_id_present: bool = False

    def __getattr__(self, name: str) -> Any:
        # Surface fields (reference, source_revision, tenant_name, ...) by delegation.
        if name == "surface":
            raise AttributeError(name)
        return getattr(self.surface, name)


def case3_fill_source(reference: str, run_day: date | None = None) -> Case3FillSource:
    """Fresh, fixed-field Case 3 source read (one CO read, one DealHub read).

    The CO must be exactly "Surface & Credential Exposure" / "New Product
    Onboarding" (case3_route_mismatch), carry exactly one valid CE email
    domain (case3_ce_email_domain_invalid), and have a Core Plus row
    (case3_core_plus_missing) with an unambiguous Core Plus Commercial term.
    """
    if not REFERENCE.fullmatch(reference):
        raise RuntimeError("invalid_co_reference")
    try:
        rows = _sf_records(
            "SELECT Name, LastModifiedDate, Account__c, Account_Name__c, Account_Country__c, "
            "Main_Domain__c, Alternate_Domains__c, Email_Domains__c, Onboarding_Product__c, Onboarding_Type__c, "
            "Surface_Account_ID__c, Account_UUID__c, Onboarding_Approval_Status__c "
            "FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2")
        if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("Name") != reference:
            raise ValueError()
        row = rows[0]
        if (row.get("Onboarding_Product__c") != CASE3_ROUTE_PRODUCT
                or row.get("Onboarding_Type__c") != CASE3_ROUTE_TYPE):
            raise SurfaceSourceError("case3_route_mismatch")
        email_domain = one_email_domain(row.get("Email_Domains__c"))
        if email_domain is None:
            raise SurfaceSourceError("case3_ce_email_domain_invalid")
        surface, subscription_rows = _surface_source_from_row(reference, row, run_day)
        if not surface.entitlement.core_plus_present:
            raise SurfaceSourceError("case3_core_plus_missing")
        try:
            ce_start, ce_end = select_ce_subscription(subscription_rows)
        except SurfaceSourceError:
            raise
        except RuntimeError as error:
            raise SurfaceSourceError(str(error)) from error
        present = any((row.get(field) or "").strip() for field in ("Surface_Account_ID__c", "Account_UUID__c"))
        return Case3FillSource(surface, email_domain, ce_start, ce_end, salesforce_id_present=present)
    except SurfaceSourceError:
        raise
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SurfaceSourceError("case3_source_unavailable") from exc


def case3_term_problem(source: Case3FillSource, run_day: date | None = None) -> str | None:
    """Plain-language explanation when the Surface and Core Plus terms disagree, else None."""
    surface_start, surface_end = surface_run_license_dates(source.surface, run_day)
    ce_start, ce_end = ce_run_license_dates(source.ce_subscription_start, source.ce_subscription_end, run_day)
    if surface_end == ce_end:
        return None
    return (f"The Surface licence would end {surface_end.isoformat()} but the Core Plus (Credential Exposure) "
            f"licence would end {ce_end.isoformat()}. One tenant has one licence term, so this CO needs a "
            "manual review: correct the DealHub terms in Salesforce, or onboard it manually.")


def case3_license_dates(source: Case3FillSource, run_day: date | None = None) -> tuple[date, date]:
    """The shared licence dates; raises ValueError("case3_term_mismatch") when the terms disagree (1a)."""
    if case3_term_problem(source, run_day) is not None:
        raise ValueError("case3_term_mismatch")
    return surface_run_license_dates(source.surface, run_day)


def build_case3_fill(source: Case3FillSource, run_day: date | None = None) -> dict[str, Any]:
    """The Surface-only plan with Leaked Credentials ON for the CE email domain."""
    start, end = case3_license_dates(source, run_day)
    plan = build_surface_only_fill(source.surface, run_day)
    plan["texts"]["Leaked Credentials scanned domains (Comma Separated Values)"] = source.ce_email_domain
    plan["selects"]["Leaked Credentials scanning interval"] = "Weekly"
    plan["checkboxes"]["leakedCredentialsAllowed"] = True
    plan["checkboxes"]["apiAccessAllowed"] = True  # Core Plus is required on this route
    plan["license_start"], plan["license_end"] = start, end
    return plan


def case3_scope_summary(source: Case3FillSource, run_day: date | None = None) -> dict[str, Any]:
    """Surface scope summary plus the CE overlay (counts only)."""
    scope = surface_scope_summary(source.surface, run_day)
    start, end = case3_license_dates(source, run_day)
    scope.update({"license_start": start.isoformat(), "license_end": end.isoformat(),
                  "leaked_credentials_domains": 1, "leaked_credentials_interval": "Weekly"})
    return scope


# --- Renewal plan (Cases 4-6), read-only, 2026-10-02 -------------------------
# Proposed rules pending owner answers (docs/38 Q1, Q3, Q7, Q10, Q11): only
# new-model rows (Surface Go/Prime + same-term add-ons, Core Plus) define the
# new term; legacy rows are listed as ignored; both expiration options are
# shown; the start date never changes (Renew card R4).
RENEWAL_CASES = {
    ("Surface & Credential Exposure", "Renewal of Existing Product"): ("case_6_renew_both", "Case 6 · renew Surface + CE", True, True),
    ("Surface & Credential Exposure", "Renewal of Surface + New Credential Exposure Module"):
        ("case_4_renew_surface_new_ce", "Case 4 · renew Surface + new CE", True, True),
    ("Surface & Credential Exposure", "Renewal of Credential Exposure Module + New Surface Product"):
        ("case_5_renew_ce_new_surface", "Case 5 · renew CE + new Surface", True, True),
    ("Surface", "Renewal of Existing Product"): ("surface_renewal", "Surface renewal (not one of the six cases, Q10)", True, False),
    ("Credential Exposure", "Renewal of Existing Product"): ("ce_renewal", "CE renewal (not one of the six cases, Q10)", False, True),
}
RENEWAL_NEW_MODEL_TIERS = frozenset({"go", "prime"})
RENEWAL_APPLY_WINDOW_DAYS = 14  # Guide G8: a renewal may be applied up to two weeks before it starts
_INACTIVE_STATUSES = ("expired", "cancelled", "canceled", "inactive")


def renewal_case(product: Any, onboarding_type: Any) -> tuple[str, str, bool, bool] | None:
    """(engine, label, renews Surface, renews CE) for a renewal CO, else None."""
    return RENEWAL_CASES.get((product, onboarding_type))


def _renewal_term(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    start, end = rows[0]["start"], rows[0]["end"]
    annual = min(_add_one_year(start) - timedelta(days=1), end)
    apply_from = start - timedelta(days=RENEWAL_APPLY_WINDOW_DAYS)
    return {"products": [row["product"] for row in rows], "status": rows[0]["status"],
            "start": start.isoformat(), "end": end.isoformat(), "annual_expiration": annual.isoformat(),
            "apply_from": apply_from.isoformat(), "applicable_now": apply_from <= today}


def build_renewal_plan(product: Any, onboarding_type: Any, subscription_rows: list[Any],
                       today: date | None = None) -> dict[str, Any] | None:
    """Read-only renewal plan for one CO from its DealHub rows (pure; values are not stored).

    Returns None for a non-renewal CO. Blockers (never raised) explain why a
    term cannot be planned: no_new_surface_term, no_core_plus_term,
    surface_term_ambiguous, core_plus_term_ambiguous, terms_differ.
    """
    from phase1_validator.surface_source_readiness import classify_surface_product, surface_scanning_interval

    case = renewal_case(product, onboarding_type)
    if case is None:
        return None
    engine, label, renews_surface, renews_ce = case
    today = today or _run_day()
    baselines: list[dict[str, Any]] = []
    addons: list[dict[str, Any]] = []
    core_plus: list[dict[str, Any]] = []
    ce_domain_addons: list[dict[str, Any]] = []
    legacy: list[str] = []
    for raw in subscription_rows:
        if not isinstance(raw, dict):
            continue
        name = raw.get("Product_Full_Name__c")
        status = _row_status(raw)
        start = _parse_subscription_date(raw.get("DealHub_Subscription_Start_Date__c"))
        end = _parse_subscription_date(raw.get("DealHub_Subscription_End_Date__c"))
        if not isinstance(name, str) or status in _INACTIVE_STATUSES or start is None or end is None or end <= start:
            continue
        row = {"product": name, "status": status, "start": start, "end": end}
        if is_core_plus_baseline_row(name):
            core_plus.append(row)
            continue
        match = re.search(r"Credential Exposure - Additional (\d+) Email Domain", name)
        if match:
            ce_domain_addons.append(dict(row, count=int(match.group(1))))
            continue
        classified = classify_surface_product(name)
        if classified is None:
            continue
        if classified.kind == "baseline" and classified.tier in RENEWAL_NEW_MODEL_TIERS:
            baselines.append(dict(row, tier=classified.tier, subdomains=classified.subdomains))
        elif classified.kind == "subdomain_addon" and any(tier in name.casefold() for tier in ("surface go", "surface prime")):
            addons.append(dict(row, subdomains=classified.subdomains))
        else:
            legacy.append(name)  # older model, or an unrecognized legacy product (Q11)
    blockers: list[str] = []
    plan: dict[str, Any] = {"engine": engine, "label": label, "renews_surface": renews_surface, "renews_ce": renews_ce,
                            "legacy_ignored": legacy, "blockers": blockers}
    surface_term = ce_term = None
    if renews_surface:
        if not baselines:
            blockers.append("no_new_surface_term")
        else:
            latest = max(row["start"] for row in baselines)
            current = [row for row in baselines if row["start"] == latest]
            if len({(row["tier"], row["subdomains"], row["end"]) for row in current}) != 1:
                blockers.append("surface_term_ambiguous")
            else:
                surface_term = _renewal_term(current, today)
                term_addons = [row for row in addons if row["start"] == latest]
                subdomains = current[0]["subdomains"] + sum(row["subdomains"] for row in term_addons)
                surface_term.update({
                    "tier": current[0]["tier"], "scanning_interval": surface_scanning_interval(current[0]["tier"]),
                    "baseline_subdomains": current[0]["subdomains"],
                    "addon_subdomains": sum(row["subdomains"] for row in term_addons),
                    "addon_products": [row["product"] for row in term_addons],
                    "subdomains": subdomains, "domains": subdomains, "assets": int(SURFACE_LICENSE_ASSETS)})
    if renews_ce:
        if not core_plus:
            blockers.append("no_core_plus_term")
        else:
            latest = max(row["start"] for row in core_plus)
            current = [row for row in core_plus if row["start"] == latest]
            if len({row["end"] for row in current}) != 1:
                blockers.append("core_plus_term_ambiguous")
            else:
                ce_term = _renewal_term(current, today)
                extra = sum(row["count"] for row in ce_domain_addons if row["start"] == latest)
                ce_term.update({"leaked_credentials_interval": "Weekly", "ce_domains_included": 1,
                                "ce_domains_addon": extra})
    if surface_term and ce_term:
        same_annual = surface_term["annual_expiration"] == ce_term["annual_expiration"]
        same_end = surface_term["end"] == ce_term["end"]
        plan["terms_agree"] = {"annual": same_annual, "term_end": same_end}
        if not (same_annual and same_end):
            blockers.append("terms_differ")
    plan["surface_term"], plan["ce_term"] = surface_term, ce_term
    return plan


def surface_run_license_dates(source: SurfaceFillSource, run_day: date | None = None) -> tuple[date, date]:
    """Surface license dates: the CE rule (start = run day; expiration =
    min(subscription start + 1 year - 1 day, subscription end)).

    Raises ValueError("surface_license_dates_unavailable") when that
    expiration is not after the run day.
    """
    try:
        return ce_run_license_dates(source.subscription_start, source.subscription_end, run_day)
    except ValueError as exc:
        raise ValueError("surface_license_dates_unavailable") from exc


# Open owner question (2026-09-29): the Guru tier card says API access
# "depends on Core Plus" while the Verified Case 1 card says API access ON.
# Until answered, API access stays ON; flip this constant to derive it from
# core_plus_present instead (one place).
SURFACE_API_ACCESS_REQUIRES_CORE_PLUS = False


def surface_api_access(source: SurfaceFillSource) -> bool:
    """apiAccessAllowed for the Surface route (see SURFACE_API_ACCESS_REQUIRES_CORE_PLUS)."""
    return source.core_plus_present if SURFACE_API_ACCESS_REQUIRES_CORE_PLUS else True


def surface_primary_user_email(source: SurfaceFillSource | CeFillSource) -> str:
    return f"{CE_PRIMARY_USER_EMAIL_LOCAL}+{source.primary_user_alias}@{CE_USER_EMAIL_DOMAIN}"


def build_surface_only_fill(source: SurfaceFillSource, run_day: date | None = None) -> dict[str, Any]:
    """Build the Surface-only Add Account fill plan (owner decisions 2026-09-29).

    Company name = account name (no suffix); Customer; main root as primary
    domain; alternate roots / subdomains as CSV when provided (verified empty
    otherwise); pentera.io user email domain; Networks, phone, job title
    verified empty; Salesforce country; Milton Stevenson with the shared alias
    rule; MFA ON; Operator Account verified empty (Dev skip); Scanning
    interval per tier; Scan now ON; Surface advanced profile with Maximum scan
    Duration 90 h; Notifications/Multiple users/API ON; Phishing and Leaked
    Credentials OFF (their interval/domains untouched); Provisioning and
    Subdomains ON; Prepaid annual subscription, 10000 assets, domains and
    subdomains both = baseline + add-on subdomains (owner rule 2026-09-30);
    CE date rule.
    """
    start, end = surface_run_license_dates(source, run_day)
    texts: dict[str, str] = {
        "Company name": source.tenant_name,
        "Company primary domain": source.main_domain,
    }
    blank_texts: list[str] = []
    for label, values in ((ALTERNATE_DOMAINS_LABEL, source.alternate_domains), (SUBDOMAINS_LABEL, source.subdomains)):
        if values:
            texts[label] = ", ".join(values)
        else:
            blank_texts.append(label)
    texts.update({
        USER_EMAIL_DOMAINS_LABEL: CE_USER_EMAIL_DOMAIN,
        "First name": CE_PRIMARY_USER_FIRST_NAME,
        "Last name": CE_PRIMARY_USER_LAST_NAME,
        "Organization Email": surface_primary_user_email(source),
        "Number of assets": SURFACE_LICENSE_ASSETS,
        "Number of domains": str(source.number_of_domains),
        "Number of subdomains": str(source.entitlement.licensed_subdomains),
    })
    blank_texts.extend((NETWORKS_LABEL, "Phone number", "Job title"))
    # Live form (2026-09-29 probe): the "Scan now" control exists only while
    # Scanning interval is None; a Weekly/Monthly schedule removes it. With a
    # schedule it is verified absent instead of being set.
    interval = source.entitlement.scanning_interval
    scan_now = {"scan_now": True} if interval == "None" else {}
    absent_checkboxes = () if scan_now else ("scan_now",)
    return {
        "texts": texts,
        "selects": {
            "Account Type": "Customer",
            "Country": source.country,
            "Scanning interval": interval,
            "Type": "Prepaid annual subscription",
        },
        "absent_checkboxes": absent_checkboxes,
        "checkboxes": {
            "mfaRequired": True,
            **scan_now,
            **SURFACE_ADVANCED_TOGGLES,
            "notificationsAllowed": True,
            "multipleUsersAllowed": True,
            "apiAccessAllowed": surface_api_access(source),
            "phishingEnabled": False,
            "leakedCredentialsAllowed": False,
            "provisioningEnabled": True,
            "subDomainsNumberAllowed": True,
        },
        "advanced_texts": {SURFACE_MAX_SCAN_DURATION_LABEL: SURFACE_MAX_SCAN_DURATION_HOURS},
        "blank_texts": tuple(blank_texts),
        "operator_account_empty": True,
        "license_start": start,
        "license_end": end,
    }


def surface_scope_summary(source: SurfaceFillSource, run_day: date | None = None) -> dict[str, Any]:
    """Counts-only scope summary for the dashboard's manual scope review."""
    from integration.onboarding.scope_policy import ScopeCounts, manual_review_reason

    entitlement = source.entitlement
    start, end = surface_run_license_dates(source, run_day)
    counts = ScopeCounts(root_domains=source.listed_domains, subdomains=len(source.subdomains),
                         licensed_domains=source.number_of_domains,
                         licensed_subdomains=entitlement.licensed_subdomains)
    return {
        "tier": entitlement.tier,
        "scanning_interval": entitlement.scanning_interval,
        "main_domains": 1,
        "alternate_root_domains": len(source.alternate_domains),
        "requested_subdomains": len(source.subdomains),
        "number_of_domains": source.number_of_domains,
        "baseline_subdomains": entitlement.baseline_subdomains,
        "addon_subdomains": entitlement.addon_subdomains,
        "licensed_subdomains": entitlement.licensed_subdomains,
        "product_domains": entitlement.product_domains,
        "assets": int(SURFACE_LICENSE_ASSETS),
        "license_start": start.isoformat(),
        "license_end": end.isoformat(),
        "large_scope": manual_review_reason(counts) is not None,
        "core_plus_present": entitlement.core_plus_present,
        # Timing rule (owner decision 2026-10-04); Development onboards immediately.
        "subscription_start": entitlement.subscription_start.isoformat(),
        "production_onboarding_day": earliest_onboarding_day(entitlement.subscription_start).isoformat(),
    }


@dataclass(frozen=True)
class RouteContract:
    """One attended route: how to read its source and build/verify its tenant."""

    engine: str
    load_source: Any  # (reference) -> source
    license_dates: Any  # (source, run_day | None) -> (start, end)
    build_fill: Any  # (source, run_day | None) -> fill plan
    primary_domain: Any  # (source) -> str
    redactions: Any  # (source) -> tuple[str, ...]
    allow_scan_started: bool

    extra_lookups: Any = None  # (source) -> tuple[(name, lookup, expected_domain), ...]

    def duplicate_lookups(self, source: Any) -> tuple[tuple[str, str], ...]:
        """Duplicate check: tenant name, then primary domain, then any route extras."""
        lookups = (("tenant_name", source.tenant_name), ("primary_domain", self.primary_domain(source)))
        extras = self.extra_lookups(source) if self.extra_lookups else ()
        return lookups + tuple((name, lookup) for name, lookup, _domain in extras)

    def lookup_domain(self, source: Any, lookup_name: str) -> str:
        """The domain a lookup's rows are compared against (the primary domain by default)."""
        for name, _lookup, domain in (self.extra_lookups(source) if self.extra_lookups else ()):
            if name == lookup_name:
                return domain
        return self.primary_domain(source)


def _ce_license_dates(source: CeFillSource, run_day: date | None = None) -> tuple[date, date]:
    return ce_run_license_dates(source.subscription_start, source.subscription_end, run_day)


# Module-level lookups (not bound functions) so tests can patch the source
# readers and plan builders by name.
CE_ROUTE = RouteContract(
    engine=CE_ENGINE,
    load_source=lambda reference: ce_fill_source(reference),
    license_dates=lambda source, run_day=None: _ce_license_dates(source, run_day),
    build_fill=lambda source, run_day=None: build_ce_only_fill(source, run_day),
    primary_domain=lambda source: source.email_domain,
    redactions=lambda source: (source.account_name, source.tenant_name, source.email_domain,
                               surface_primary_user_email(source)),
    allow_scan_started=False,
)
SURFACE_ROUTE = RouteContract(
    engine=SURFACE_ENGINE,
    load_source=lambda reference: surface_fill_source(reference),
    license_dates=lambda source, run_day=None: surface_run_license_dates(source, run_day),
    build_fill=lambda source, run_day=None: build_surface_only_fill(source, run_day),
    primary_domain=lambda source: source.main_domain,
    redactions=lambda source: (source.account_name, source.tenant_name, source.main_domain,
                               *source.alternate_domains, *source.subdomains,
                               surface_primary_user_email(source)),
    allow_scan_started=True,
)
CASE3_ROUTE = RouteContract(
    engine=CASE3_ENGINE,
    load_source=lambda reference: case3_fill_source(reference),
    license_dates=lambda source, run_day=None: case3_license_dates(source, run_day),
    build_fill=lambda source, run_day=None: build_case3_fill(source, run_day),
    primary_domain=lambda source: source.main_domain,
    redactions=lambda source: (source.account_name, source.tenant_name, source.main_domain,
                               *source.alternate_domains, *source.subdomains, source.ce_email_domain,
                               surface_primary_user_email(source)),
    allow_scan_started=True,
    # 4a: an existing CE-only tenant for the customer is found by its email domain.
    extra_lookups=lambda source: (("ce_email_domain", source.ce_email_domain, source.ce_email_domain),),
)
ROUTES = {CE_ENGINE: CE_ROUTE, SURFACE_ENGINE: SURFACE_ROUTE, CASE3_ENGINE: CASE3_ROUTE}


def _format_date_for_placeholder(value: date, placeholder: Any) -> str:
    """Format a date for a date-picker input from its placeholder skeleton.

    Recognizes yyyy/yy/mm/m/dd/d tokens (e.g. "mm/dd/yyyy" -> "10/24/2026",
    "yyyy-mm-dd" -> "2026-10-24"), preserving the placeholder separators.
    Any missing or unrecognized placeholder falls back to mm/dd/yyyy so the
    fill stays deterministic; the post-fill re-read still verifies the value.
    """
    skeleton = (placeholder or "").casefold()
    tokens = re.findall(r"yyyy|yy|mm|m|dd|d", skeleton)
    if not tokens or not (any(t in ("yyyy", "yy") for t in tokens)
                          and any(t in ("mm", "m") for t in tokens)
                          and any(t in ("dd", "d") for t in tokens)):
        return value.strftime("%m/%d/%Y")
    parts = {
        "yyyy": str(value.year), "yy": str(value.year % 100).zfill(2),
        "mm": str(value.month).zfill(2), "m": str(value.month),
        "dd": str(value.day).zfill(2), "d": str(value.day),
    }
    output: list[str] = []
    index = 0
    while index < len(skeleton):
        match = re.match(r"yyyy|yy|mm|m|dd|d", skeleton[index:])
        if match:
            output.append(parts[match.group(0)])
            index += len(match.group(0))
        else:
            output.append(skeleton[index])
            index += 1
    return "".join(output)


def _locate_checkbox(page: Any, key: str) -> Any | None:
    """Locate one Add Account checkbox by its stable name (or aria name)."""
    try:
        if key == "scan_now":
            control = page.get_by_role("checkbox", name="primary checkbox", exact=True)
        else:
            control = page.locator(f'input[type=checkbox][name="{key}"]')
        if control.count() == 1:
            return control
    except Exception:
        return None
    return None


def _set_checkbox(page: Any, key: str, target: bool) -> bool:
    """Set one checkbox to its target state, verifying by re-reading it.

    Returns False (fail-closed) when the control is missing or ambiguous,
    cannot be set, or does not report the target state afterwards.
    """
    control = _locate_checkbox(page, key)
    if control is None:
        _log().event("fill_toggle", "not_found", key)
        return False
    try:
        if control.first.is_checked() == target:
            return True
        try:
            control.first.click(timeout=TOGGLE_CLICK_TIMEOUT_MS)
        except Exception as exc:
            # A switch's visual track can intercept the pointer on the input;
            # retry once with a forced click (the state is still re-verified).
            _log().error("fill_toggle", f"{key}:click", exc)
            control.first.click(force=True, timeout=TOGGLE_CLICK_TIMEOUT_MS)
        for _poll in range(5):
            if control.first.is_checked() == target:
                return True
            page.wait_for_timeout(200)
        _log().event("fill_toggle", "state_unchanged", key)
        return False
    except Exception as exc:
        _log().error("fill_toggle", key, exc)
        return False


def _locate_license_date(page: Any, key: str) -> Any | None:
    """Locate one license date control.

    Primary: the stable data-am attribute (the live form's date inputs carry
    no label, name, id, or placeholder). Fallback: candidate accessible labels,
    in case the form regresses to labeled controls.
    """
    data_am = LICENSE_DATE_DATA_AM.get(key)
    if data_am:
        try:
            control = page.locator(f'[data-am="{data_am}"]')
            if control.count() == 1:
                return control
        except Exception:
            pass
    for label in LICENSE_DATE_LABEL_CANDIDATES[key]:
        control = _locate_form_field(page, label, "")
        if control is not None:
            return control
    return None


def _fill_license_date(page: Any, key: str, value: date) -> bool:
    """Fill one license date control, verifying the value by re-reading it.

    The date inputs are plain text fields with no placeholder, so the value is
    formatted with the confirmed LICENSE_DATE_INPUT_FORMAT. Returns False
    (fail-closed) when the control is missing, the fill fails, or the control
    does not keep the formatted value (a date-picker reformat is caught here).
    """
    control = _locate_license_date(page, key)
    if control is None:
        _log().event("fill_date", "not_found", key)
        return False
    element = control.first
    try:
        read_only = element.get_attribute("readonly") is not None
    except Exception:
        read_only = False
    if read_only:
        # Live form (2026-09-29 probe): the date inputs are readonly and set
        # only through a Material-UI picker dialog; typing can never work.
        return _pick_license_date(page, element, key, value)
    formatted = value.strftime(LICENSE_DATE_INPUT_FORMAT)
    # The picker keeps its own datetime state (the CO-0702 HAR carries the
    # form-open minute), so a typed value can revert on blur: blur (Tab) and
    # re-read after a short settle. One keystroke-by-keystroke retry covers a
    # picker that ignores programmatic fill.
    for attempt in ("fill", "type"):
        try:
            if attempt == "fill":
                element.fill(formatted, timeout=FIELD_TIMEOUT_MS)
            else:
                element.fill("", timeout=FIELD_TIMEOUT_MS)
                element.press_sequentially(formatted, delay=30, timeout=FIELD_TIMEOUT_MS)
            element.press("Tab")
            page.wait_for_timeout(300)
            actual = element.input_value()
        except Exception as exc:
            _log().error("fill_date", f"{key}:{attempt}", exc)
            continue
        if actual == formatted:
            return True
        _log().event("fill_date", "reverted", f"{key}:{attempt}", f"length={len(actual or '')}")
    return False


def _parse_display_date(text: object) -> date | None:
    """Parse a license date as the form displays it ("Sep 29, 2026") or ISO."""
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split())
    for pattern in LICENSE_DATE_DISPLAY_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def _picker_month(header_text: object) -> tuple[int, int] | None:
    """Parse the picker header ("September 2026") to (year, month)."""
    if not isinstance(header_text, str):
        return None
    try:
        parsed = datetime.strptime(" ".join(header_text.split()), "%B %Y")
    except ValueError:
        return None
    return parsed.year, parsed.month


def _settled_picker_month(page: Any, header: Any, previous: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Read the picker header once its slide transition has settled.

    Mid-transition the header holds both the outgoing and incoming month, so
    poll (bounded) until it parses as exactly one month that differs from
    ``previous`` (when given).
    """
    month = None
    for _poll in range(PICKER_SETTLE_POLLS):
        try:
            month = _picker_month(header.first.inner_text()) if header.count() >= 1 else None
        except Exception:
            month = None
        if month is not None and month != previous:
            return month
        page.wait_for_timeout(100)
    return month


def _picker_idle(page: Any, picker: Any) -> bool:
    """True once no slide transition (header or day grid) is in progress."""
    transitions = picker.first.locator(PICKER_TRANSITION_SELECTOR)
    for _poll in range(PICKER_SETTLE_POLLS):
        try:
            if transitions.count() == 0:
                return True
        except Exception:
            pass
        page.wait_for_timeout(100)
    return False


def _close_picker(page: Any, picker: Any) -> bool:
    """Dismiss an open picker without accepting a value; always returns False.

    The picker's own Cancel (scoped to the picker dialog, never the Add
    Account form's Cancel) is preferred; Escape is the fallback.
    """
    try:
        if picker.count() >= 1 and picker.first.is_visible():
            cancel = picker.first.get_by_role("button", name="Cancel", exact=True)
            if cancel.count() == 1:
                cancel.first.click(timeout=FIELD_TIMEOUT_MS)
            else:
                page.keyboard.press("Escape")
            picker.first.wait_for(state="hidden", timeout=FIELD_TIMEOUT_MS)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    return False


def _pick_license_date(page: Any, element: Any, key: str, value: date) -> bool:
    """Set one readonly license date through the Material-UI picker dialog.

    Opens the picker, steps month by month to the target (bounded, and it
    stops if the header does not advance), clicks the single enabled day
    button with the exact day number, accepts with the picker's own OK, and
    verifies the displayed value parses back to the target date. Fails
    closed (False) on any missing or ambiguous picker control.
    """
    log = _log()
    picker = page.locator(PICKER_DIALOG_SELECTOR)
    try:
        element.click(timeout=FIELD_TIMEOUT_MS)
        picker.first.wait_for(state="visible", timeout=FIELD_TIMEOUT_MS)
        if picker.count() != 1:
            log.event("pick_date", "picker_ambiguous", key, f"count={picker.count()}")
            return False
        header = picker.first.locator(PICKER_HEADER_SELECTOR)
        arrows = picker.first.locator(PICKER_ARROW_SELECTOR)
        target = (value.year, value.month)
        current = _settled_picker_month(page, header)
        for _step in range(PICKER_MAX_MONTH_STEPS + 1):
            if current is None:
                log.event("pick_date", "header_unreadable", key)
                return _close_picker(page, picker)
            if current == target:
                break
            if arrows.count() != 2:
                log.event("pick_date", "arrows_unavailable", key, f"count={arrows.count()}")
                return _close_picker(page, picker)
            (arrows.nth(1) if current < target else arrows.nth(0)).click(timeout=FIELD_TIMEOUT_MS)
            # The header slides: mid-transition it holds both months, so wait
            # for a single, different month before the next step.
            following = _settled_picker_month(page, header, previous=current)
            if following is None or following == current:
                log.event("pick_date", "header_stuck", key)
                return _close_picker(page, picker)
            current = following
        else:
            log.event("pick_date", "month_out_of_range", key)
            return _close_picker(page, picker)
        # The day grid slides too: wait until no transition is in progress so
        # the counted buttons are the target month's, not the outgoing one's.
        if not _picker_idle(page, picker):
            log.event("pick_date", "calendar_not_idle", key)
            return _close_picker(page, picker)
        day_text = str(value.day)
        days = picker.first.locator(PICKER_DAY_SELECTOR)
        matches = [index for index in range(days.count())
                   if " ".join(days.nth(index).inner_text().split()) == day_text]
        if len(matches) != 1:
            log.event("pick_date", "day_ambiguous", key, f"matches={len(matches)}")
            return _close_picker(page, picker)
        day = days.nth(matches[0])
        if (day.get_attribute("disabled") is not None
                or PICKER_DAY_DISABLED_CLASS in (day.get_attribute("class") or "")):
            log.event("pick_date", "day_disabled", key)
            return _close_picker(page, picker)
        day.click(timeout=FIELD_TIMEOUT_MS)
        # Live picker auto-accepts: the day click closes it (2026-09-29 probe).
        # If it stays open instead, the day must be selected and OK accepts it.
        closed = False
        for _poll in range(PICKER_SETTLE_POLLS):
            if picker.count() == 0 or not picker.first.is_visible():
                closed = True
                break
            page.wait_for_timeout(100)
        if not closed:
            selected = picker.first.locator(PICKER_DAY_SELECTED_SELECTOR)
            if not (selected.count() == 1 and " ".join(selected.first.inner_text().split()) == day_text):
                log.event("pick_date", "day_not_selected", key, f"selected_count={selected.count()}")
                return _close_picker(page, picker)
            ok = picker.first.get_by_role("button", name="OK", exact=True)
            if ok.count() != 1:
                log.event("pick_date", "ok_unavailable", key, f"count={ok.count()}")
                return _close_picker(page, picker)
            ok.first.click(timeout=FIELD_TIMEOUT_MS)
            picker.first.wait_for(state="hidden", timeout=FIELD_TIMEOUT_MS)
        page.wait_for_timeout(300)
        shown = element.input_value()
    except Exception as exc:
        log.error("pick_date", key, exc)
        return _close_picker(page, picker)
    if _parse_display_date(shown) != value:
        # The form silently refuses some dates (e.g. a start date after today,
        # 2026-09-29 probe). A displayed license date is not sensitive.
        log.event("pick_date", "rejected_by_form", key, f"shown={shown!r} expected={value.isoformat()}")
        return False
    log.event("pick_date", "ok", key)
    return True


def _license_date_kept(page: Any, key: str, value: date) -> bool:
    """Re-read one license date control (final pre-Confirm verification)."""
    control = _locate_license_date(page, key)
    try:
        return control is not None and _parse_display_date(control.first.input_value()) == value
    except Exception:
        return False


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _capture_search_diagnostics(page: Any) -> None:
    """Capture redacted control metadata for selector diagnosis.

    Records only control metadata (roles, accessible names, input types,
    placeholders, aria-labels, ids, data-am attributes, associated label text,
    button names) — never form values, tenant names, or table contents. Written
    to a local file for offline diagnosis of page-layout (selector) drift.
    Failures are swallowed: diagnostics must never change the runner's
    fail-closed outcome.
    """
    def _safe(fn, default: str = "") -> str:
        try:
            value = fn()
            return str(value)[:120] if value is not None else default
        except Exception:
            return default
    try:
        url = _safe(lambda: page.url, "").split("?", 1)[0].split("#", 1)[0]
        title = _safe(lambda: page.title, "")
        roles: list[dict[str, str]] = []
        for role in ("textbox", "searchbox", "combobox", "button"):
            try:
                loc = page.get_by_role(role)
                for index in range(min(loc.count(), 40)):
                    element = loc.nth(index)
                    entry: dict[str, str] = {"role": role}
                    name = _safe(lambda e=element: e.get_attribute("aria-label"))
                    if not name:
                        name = _safe(lambda e=element: e.get_attribute("placeholder"))
                    if name:
                        entry["name"] = name
                    roles.append(entry)
            except Exception:
                continue
        inputs: list[dict[str, str]] = []
        try:
            # Evaluate in-page so we can capture data-am attributes and the
            # associated <label> text (label[for], wrapping label, or
            # aria-labelledby) that Playwright's get_by_label relies on. Only
            # metadata is returned — never form values.
            collected = page.evaluate(
                """() => {
                    const inputs = Array.from(document.querySelectorAll('input, select, textarea'));
                    const safe = (fn) => { try { return fn(); } catch (e) { return null; } };
                    return inputs.slice(0, 60).map(el => {
                        const meta = { tag: el.tagName.toLowerCase() };
                        for (const attr of ['type','placeholder','aria-label','name','id','data-am']) {
                            const v = el.getAttribute(attr);
                            if (v) meta[attr] = v;
                        }
                        let labelText = '';
                        if (el.id) {
                            labelText = safe(() => {
                                const label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                                return label ? label.textContent : '';
                            }) || '';
                        }
                        if (!labelText) {
                            labelText = safe(() => {
                                const wrapping = el.closest('label');
                                return wrapping ? wrapping.textContent : '';
                            }) || '';
                        }
                        if (!labelText && el.getAttribute('aria-labelledby')) {
                            labelText = safe(() => {
                                const ids = el.getAttribute('aria-labelledby').split(/\\s+/);
                                return ids.map(id => {
                                    const e = document.getElementById(id);
                                    return e ? e.textContent : '';
                                }).filter(Boolean).join(' ');
                            }) || '';
                        }
                        if (labelText) meta['label'] = labelText.trim().slice(0, 120);
                        return meta;
                    });
                }"""
            )
            if isinstance(collected, list):
                for item in collected:
                    if isinstance(item, dict):
                        inputs.append({k: str(v)[:120] for k, v in item.items() if v})
        except Exception:
            pass
        table_rows: list[dict[str, str]] = []
        try:
            # Table-structure metadata only (cell counts, colspan, checkbox
            # presence) — never row contents — so an unexpected row layout
            # (e.g. a zero-result empty-state row or a selectable-row checkbox
            # column) can be diagnosed offline.
            rows = page.locator("tbody tr")
            row_count = rows.count()
            for index in range(min(row_count, 5)):
                cells = rows.nth(index).locator("td")
                cell_count = cells.count()
                entry: dict[str, str] = {"index": str(index), "cell_count": str(cell_count)}
                if cell_count == 1:
                    colspan = _safe(lambda: cells.first.get_attribute("colspan"))
                    if colspan:
                        entry["colspan"] = colspan
                checkbox_cells = 0
                for cell_index in range(min(cell_count, 12)):
                    try:
                        if cells.nth(cell_index).locator("input[type=checkbox]").count() >= 1:
                            checkbox_cells += 1
                    except Exception:
                        pass
                if checkbox_cells:
                    entry["checkbox_cells"] = str(checkbox_cells)
                if cell_count <= 3:
                    # Short-row shape only: per-cell text lengths and whether a
                    # cell is a known empty-state message — never the text.
                    lengths: list[str] = []
                    matched = False
                    for cell_index in range(cell_count):
                        text = _safe(lambda c=cells.nth(cell_index): c.inner_text())
                        lengths.append(str(len(text)))
                        matched = matched or _normalized_ui_text(text) in EMPTY_STATE_MESSAGES
                    entry["cell_text_lengths"] = ",".join(lengths)
                    entry["empty_state_message"] = "yes" if matched else "no"
                table_rows.append(entry)
        except Exception:
            pass
        payload = {
            "url": url[:200],
            "title": title[:120],
            "roles": roles,
            "inputs": inputs,
            "table_rows": table_rows,
            "captured_on": datetime.now().isoformat(timespec="seconds"),
            "note": "Redacted control and table-structure metadata only. No form values, tenant names, or table contents.",
        }
        _write_json_atomic(DIAGNOSTICS_PATH, payload)
        # Keep a copy with the run's log so a later capture (another run or a
        # session check) cannot overwrite the evidence of this failure.
        _log().diagnostics.append(payload)
    except Exception:
        pass


def load_runner_state() -> dict[str, dict[str, str]]:
    """Load one-time acknowledgement records; absence means no records."""
    try:
        raw = json.loads(RUNNER_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        state: dict[str, dict[str, str]] = {}
        for reference, value in raw.items():
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict):
                raise ValueError()
            revision = value.get("source_revision")
            if not isinstance(revision, str) or not revision:
                raise ValueError()
            if set(value) - {"source_revision", "started_on", "result", "completed_on",
                             "scope_reviewed_on", "route", "license_start_entered", "license_end_entered",
                             "settled", "settled_from", "settled_on"}:
                raise ValueError()
            record: dict[str, str] = {"source_revision": revision}
            if "route" in value:
                # Only recorded for non-default routes (the Surface route).
                if value["route"] not in RUNNER_STATE_ROUTES:
                    raise ValueError()
                record["route"] = value["route"]
            for key in ("started_on", "completed_on", "scope_reviewed_on"):
                if key in value:
                    if not isinstance(value[key], str):
                        raise ValueError()
                    datetime.fromisoformat(value[key])
                    record[key] = value[key]
            for key in ("license_start_entered", "license_end_entered"):
                if key in value:
                    if not isinstance(value[key], str):
                        raise ValueError()
                    date.fromisoformat(value[key])
                    record[key] = value[key]
            if "settled" in value:
                if value["settled"] != "no_tenant":
                    raise ValueError()
                record["settled"] = value["settled"]
            if "settled_from" in value:
                if not isinstance(value["settled_from"], str) or not value["settled_from"]:
                    raise ValueError()
                record["settled_from"] = value["settled_from"]
            if "settled_on" in value:
                if not isinstance(value["settled_on"], str):
                    raise ValueError()
                datetime.fromisoformat(value["settled_on"])
                record["settled_on"] = value["settled_on"]
            if "completed_on" in record and "result" not in value:
                raise ValueError()
            if "result" in value:
                if not isinstance(value["result"], str) or not value["result"]:
                    raise ValueError()
                record["result"] = value["result"]
            state[reference] = record
        return state
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RunnerStateUnavailable() from exc


def load_check_state() -> dict[str, dict[str, str]]:
    """Load the latest read-only check per CO (duplicate check or readback).

    Local evidence only: it never gates a create run. A missing file means no
    checks; a corrupt one raises ValueError so the dashboard can say so.
    """
    try:
        raw = json.loads(CHECK_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("check_state_unreadable") from exc
    if not isinstance(raw, dict):
        raise ValueError("check_state_unreadable")
    state: dict[str, dict[str, str]] = {}
    for reference, value in raw.items():
        if (not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict)
                or value.get("kind") not in CHECK_KINDS
                or set(value) - {"kind", "started_on", "completed_on", "result"}
                or not all(isinstance(item, str) and item for item in value.values())):
            raise ValueError("check_state_unreadable")
        state[reference] = dict(value)
    return state


def record_check_start(reference: str, kind: str, started_on: str) -> None:
    """Record that a read-only check started; it replaces the CO's previous check."""
    if not REFERENCE.fullmatch(reference) or kind not in CHECK_KINDS:
        raise ValueError("invalid_check_record")
    state = load_check_state()
    state[reference] = {"kind": kind, "started_on": started_on}
    _write_json_atomic(CHECK_STATE_PATH, state)


def record_check_result(reference: str, kind: str, result: str, completed_on: str) -> None:
    """Record a read-only check's result (keeps the recorded start when it matches)."""
    if not REFERENCE.fullmatch(reference) or kind not in CHECK_KINDS or not result:
        raise ValueError("invalid_check_record")
    state = load_check_state()
    previous = state.get(reference, {})
    record = {"kind": kind, "result": result, "completed_on": completed_on}
    if previous.get("kind") == kind and "result" not in previous and previous.get("started_on"):
        record["started_on"] = previous["started_on"]
    state[reference] = record
    _write_json_atomic(CHECK_STATE_PATH, state)


def create_uncertain(record: dict[str, str] | None) -> bool:
    """True when a failed run had already entered the licence dates (set right before Confirm).

    Review item 2 (2026-10-01): such a run may have created a tenant, so it is
    never "nothing was created". A read-only readback settles it: a found
    tenant promotes the run to readback_verified; no tenant marks it settled.
    """
    return bool(record and record.get("license_start_entered") and record.get("result")
                and record["result"] != "readback_verified" and record.get("settled") != "no_tenant")


def settle_uncertain_create(reference: str, readback_result: str, settled_on: str) -> str | None:
    """Apply a read-only readback outcome to an uncertain create; returns what changed, if anything."""
    if not REFERENCE.fullmatch(reference):
        return None
    state = load_runner_state()
    record = state.get(reference)
    if not create_uncertain(record):
        return None
    if readback_result == "readback_only_verified":
        record["settled_from"], record["result"], record["settled_on"] = record["result"], "readback_verified", settled_on
        change = "tenant_found"
    elif readback_result == "readback_only_tenant_not_found":
        record["settled"], record["settled_on"] = "no_tenant", settled_on
        change = "no_tenant"
    else:
        return None
    _write_json_atomic(RUNNER_STATE_PATH, state)
    return change


def start_blocker(record: dict[str, str] | None) -> str | None:
    """Why a new attended start must not happen for a CO, whatever its revision, else None.

    "run_in_progress": a recorded start has no result yet (a runner may still
    be filling or confirming). "tenant_already_verified": a tenant was
    created and read back. Review item 1 (2026-10-01): before this, a CO
    edited in Salesforce during a run could launch a second runner.
    """
    if record is None:
        return None
    if not record.get("result"):
        return "run_in_progress"
    if record["result"] == "readback_verified":
        return "tenant_already_verified"
    if create_uncertain(record):
        return "create_uncertain"
    return None


def record_runner_start(reference: str, revision: str, started_on: str, *,
                        route: str | None = None, scope_reviewed_on: str | None = None) -> None:
    """Record one acknowledged start; a finished, failed run of another revision is superseded.

    Refuses (ValueError with the start_blocker code) while the CO's current
    record is in progress or verified, regardless of revision.

    ``route`` and ``scope_reviewed_on`` are recorded only when given (the
    Surface route's revision-bound scope-review acknowledgement), so a CE-only
    start writes exactly the same record as before.
    """
    if not REFERENCE.fullmatch(reference) or not revision:
        raise ValueError("invalid_runner_state_record")
    if route is not None and route not in RUNNER_STATE_ROUTES:
        raise ValueError("invalid_runner_state_record")
    if scope_reviewed_on is not None:
        try:
            datetime.fromisoformat(scope_reviewed_on)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_runner_state_record") from exc
    state = load_runner_state()
    blocker = start_blocker(state.get(reference))
    if blocker is not None:
        raise ValueError(blocker)
    record = {"source_revision": revision, "started_on": started_on}
    if route is not None:
        record["route"] = route
    if scope_reviewed_on is not None:
        record["scope_reviewed_on"] = scope_reviewed_on
    state[reference] = record
    _write_json_atomic(RUNNER_STATE_PATH, state)


def record_runner_result(reference: str, revision: str, result: str, completed_on: str,
                         license_entered: tuple[date, date] | None = None) -> None:
    """Record the final result for the acknowledged revision; the first result wins.

    ``license_entered`` is the (start, end) actually entered before Confirm,
    including the one-day-earlier start fallback, so the dashboard can show it.
    """
    if not REFERENCE.fullmatch(reference) or not revision or not result:
        raise ValueError("invalid_runner_state_record")
    state = load_runner_state()
    record = state.get(reference)
    if record is None:
        record = state[reference] = {"source_revision": revision, "result": result, "completed_on": completed_on}
    elif record["source_revision"] == revision and "result" not in record:
        record["result"] = result
        record["completed_on"] = completed_on
    else:
        return
    if license_entered is not None:
        record["license_start_entered"] = license_entered[0].isoformat()
        record["license_end_entered"] = license_entered[1].isoformat()
    _write_json_atomic(RUNNER_STATE_PATH, state)


class ReadbackConflict(ValueError):
    """A readback found different IDs than the ones already captured for this CO; nothing is replaced."""

    def __init__(self) -> None:
        super().__init__("readback_id_conflict")


def write_readback_evidence(reference: str, surface_account_id: str, account_uuid: str, observed_on: str,
                            state: str = READBACK_STATE) -> None:
    """Append one minimal local readback entry; never overwrites another CO's entry.

    The observed Account Scanning state must be one of the accepted states
    (a created-and-scanning tenant or a tenant with no scan started yet).
    """
    if not REFERENCE.fullmatch(reference):
        raise ValueError("invalid_readback_reference")
    if not re.fullmatch(SURFACE_ACCOUNT_ID_PATTERN, surface_account_id):
        raise ValueError("invalid_readback_surface_account_id")
    if not re.fullmatch(ACCOUNT_UUID_PATTERN, account_uuid):
        raise ValueError("invalid_readback_account_uuid")
    if not isinstance(state, str) or state not in READBACK_STATES:
        raise ValueError("invalid_readback_state")
    date.fromisoformat(observed_on)
    try:
        raw = json.loads(READBACK_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
    except FileNotFoundError:
        raw: dict[str, Any] = {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("readback_evidence_unreadable") from exc
    previous = raw.get(reference)
    if isinstance(previous, dict) and (
            previous.get("surface_account_id") != surface_account_id
            or str(previous.get("account_uuid", "")).casefold() != account_uuid.casefold()):
        raise ReadbackConflict()  # review item 3: refresh state/date only for the same tenant
    raw[reference] = {
        "surface_account_id": surface_account_id,
        "account_uuid": account_uuid,
        "leonardo_state": state,
        "observed_on": observed_on,
        "source": READBACK_SOURCE,
    }
    _write_json_atomic(READBACK_PATH, raw)


def _row_text_cells(cells: Any) -> list[str] | None:
    """Return a row's text-cell values, skipping leading checkbox-only cells.

    MUIDataTable renders a leading checkbox column when rows are selectable;
    such a cell has no text and must not be mistaken for the company name
    (comparing it would silently false-clear a real duplicate and break the
    readback). Returns None when the row has no usable text cells, so the
    caller can fail closed.
    """
    try:
        count = cells.count()
    except Exception:
        return None
    texts: list[str] = []
    for index in range(min(count, 12)):
        cell = cells.nth(index)
        try:
            has_checkbox = cell.locator("input[type=checkbox]").count() >= 1
            text = cell.inner_text()
        except Exception:
            return None
        if has_checkbox and not text.strip():
            continue
        texts.append(text)
    return texts or None


def _normalized_ui_text(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".")


def _is_empty_state_row(cells: Any) -> bool:
    """True for the MUIDataTable zero-result row, e.g. "No records found".

    Two live shapes are recognized:
    - the standard layout: a single <td> spanning the table (colspan > 1);
    - the stacked (responsive) layout the Development tenant table uses, where
      every body cell renders as a label <td> plus a value <td>: the zero-result
      row is 2-3 cells, exactly one holding a known empty-state message and
      the rest blank (observed 2026-09-29: one row, two cells).

    A zero-result search is by definition a clean duplicate check: no visible
    row can be a duplicate. Any other short-row shape (a loading row, a
    truncated row, unexpected text) cannot be classified and stays fail-closed.
    """
    try:
        count = cells.count()
        if count == 1:
            colspan = cells.first.get_attribute("colspan")
            return colspan is not None and int(colspan) > 1
        if not 2 <= count <= 3:
            return False
        texts = [_normalized_ui_text(cells.nth(index).inner_text()) for index in range(count)]
    except Exception:
        return False
    messages = [text for text in texts if text]
    return len(messages) == 1 and messages[0] in EMPTY_STATE_MESSAGES


def _tenant_row_values(texts: list[str]) -> tuple[str | None, str | None]:
    """Return (company, domain) from a tenant row's text cells.

    The live tenant table renders each column as a label cell followed by its
    value cell (with an empty spacer between pairs), e.g.
    ["Company name", <name>, "", "Company primary domain", <domain>, ...].
    Comparing the fixed first two cells would therefore compare the literal
    label "Company name" against the expected company name and never match.
    The values are resolved by their label instead, which is robust to column
    reordering. Returns (None, None) when either label is absent so the caller
    fails closed.
    """
    company = None
    domain = None
    for index, cell in enumerate(texts):
        normalized = " ".join(cell.casefold().split())
        if index + 1 >= len(texts):
            continue
        if normalized == "company name":
            company = texts[index + 1]
        elif normalized == "company primary domain":
            domain = texts[index + 1]
    return company, domain


def _exact_tenant_rows(page: Any, expected_name: str, expected_domain: str) -> str:
    """Classify a visible tenant table without retaining a result row.

    The zero-result empty-state row (a single full-width cell) is the only
    legitimate short row: it means the search matched nothing, which is a
    clean duplicate check. A leading checkbox-only cell is skipped so the
    company and domain cells are compared against the right columns; the
    company and domain values are resolved by their label cells (see
    _tenant_row_values). Any other short-row shape or missing label fails
    closed.
    """
    rows = page.locator("tbody tr")
    count = rows.count()
    if count > 100:
        return "duplicate_schema_unavailable"
    exact = 0
    for index in range(count):
        cells = rows.nth(index).locator("td")
        if count == 1 and _is_empty_state_row(cells):
            return "duplicate_clear"
        texts = _row_text_cells(cells)
        if texts is None or len(texts) < 2:
            return "duplicate_schema_unavailable"
        company, domain = _tenant_row_values(texts)
        if company is None or domain is None:
            return "duplicate_schema_unavailable"
        normalized_company = " ".join(company.casefold().split())
        normalized_domain = domain.casefold().strip().rstrip(".")
        if normalized_company == " ".join(expected_name.casefold().split()) or normalized_domain == expected_domain:
            exact += 1
        elif (normalized_company.startswith(" ".join(expected_name.casefold().split()) + " ")
              or " ".join(expected_name.casefold().split()).startswith(normalized_company + " ")):
            return "duplicate_ambiguous"
    return "duplicate_clear" if exact == 0 else "duplicate_found"


def _settled_tenant_rows(page: Any, expected_name: str, expected_domain: str) -> str:
    """Classify the tenant table after a search, letting a transient row settle.

    Waits for the client-side search to apply, then re-reads an
    unclassifiable table up to TABLE_SETTLE_RETRIES times (1 s apart) so a
    loading placeholder cannot fail an otherwise valid check. It never turns
    an unclassifiable table into a clear one: only a successful read can.
    """
    # The caller has already waited for this lookup's search response; this
    # only lets the table re-render from it.
    page.wait_for_timeout(500)
    result = _exact_tenant_rows(page, expected_name, expected_domain)
    for _attempt in range(TABLE_SETTLE_RETRIES):
        if result != "duplicate_schema_unavailable":
            break
        page.wait_for_timeout(1_000)
        result = _exact_tenant_rows(page, expected_name, expected_domain)
    return result


def _detail_value(page: Any, label: str) -> str | None:
    """Read one exact-labeled details control; ambiguity stops the readback."""
    control = page.get_by_label(label, exact=True)
    if control.count() != 1:
        return None
    element = control.first
    try:
        value = element.input_value()
    except Exception:
        try:
            value = element.inner_text()
        except Exception:
            return None
    value = " ".join(value.split())
    return value or None


def _open_tenant_details(page: Any, tenant_name: str, expected_domain: str | None = None) -> bool:
    """Click the exact tenant's row to open its details page (read-only).

    Requires exactly one row whose company name (and, when given, primary
    domain) matches; zero or several matches return False (review item 3:
    the first name match was clicked before, ignoring the domain).
    """
    expected = " ".join(tenant_name.casefold().split())
    domain_wanted = expected_domain.casefold().strip().rstrip(".") if expected_domain else None
    rows = page.locator("tbody tr")
    count = rows.count()
    matches = []
    for index in range(min(count, 100)):
        cells = rows.nth(index).locator("td")
        texts = _row_text_cells(cells)
        if not texts:
            continue
        company, domain = _tenant_row_values(texts)
        if company is None or " ".join(company.casefold().split()) != expected:
            continue
        if domain_wanted is not None and (domain or "").casefold().strip().rstrip(".") != domain_wanted:
            continue
        matches.append(rows.nth(index))
    if len(matches) != 1:
        return False
    target = matches[0]
    links = target.locator("a")
    if links.count() == 1:
        links.first.click()
    else:
        target.click()
    page.wait_for_timeout(1_500)
    return True


def _unique_api_row(result: "TenantSearchResult", tenant_name: str, expected_domain: str | None) -> dict[str, Any] | None:
    """The single server row with this exact name (and domain, when given), else None."""
    name = " ".join(tenant_name.casefold().split())
    rows = [row for row in result.rows
            if isinstance(row.get("accountName"), str) and " ".join(row["accountName"].casefold().split()) == name]
    if expected_domain is not None:
        rows = [row for row in rows if isinstance(row.get("accountDomain"), str)
                and row["accountDomain"].casefold().strip().rstrip(".") == expected_domain]
    return rows[0] if len(rows) == 1 else None


def _row_ids_valid(row: dict[str, Any]) -> bool:
    return bool(isinstance(row.get("id"), str) and re.fullmatch(SURFACE_ACCOUNT_ID_PATTERN, row["id"])
                and isinstance(row.get("accountUuid"), str) and re.fullmatch(ACCOUNT_UUID_PATTERN, row["accountUuid"]))


def _details_fallback(page: Any, searched: "TenantSearchResult | None", tenant_name: str,
                      expected_domain: str | None) -> tuple[tuple[str, str, str | None] | None, str | None]:
    """Read the details view only when the tenant is provably unique; the IDs must equal the server row.

    Returns (details, failure_code). With a server response, the name (and
    domain) must match exactly one row, else readback_tenant_ambiguous. The
    details view must then show that row's IDs, else readback_value_mismatch.
    """
    row = None
    if searched is not None:
        row = _unique_api_row(searched, tenant_name, expected_domain)
        if row is None:
            return None, "readback_tenant_ambiguous"
        if not _row_ids_valid(row):
            return None, "readback_value_mismatch"
    details = _readback_details_optional_state(page, tenant_name, expected_domain)
    if details is None:
        return None, "readback_schema_unavailable"
    if row is not None and (details[0] != row["id"] or details[1].casefold() != row["accountUuid"].casefold()):
        return None, "readback_value_mismatch"
    return details, None


def _readback_details(page: Any, tenant_name: str) -> tuple[str, str, str] | None:
    """Open the exact tenant's details page and read the three evidence values."""
    if not _open_tenant_details(page, tenant_name):
        return None
    surface_account_id = _detail_value(page, "Surface Account ID")
    account_uuid = _detail_value(page, "Account UUID")
    state = _detail_value(page, "Account Scanning")
    if not all(isinstance(item, str) and item for item in (surface_account_id, account_uuid, state)):
        return None
    return surface_account_id, account_uuid, state


def _readback_details_optional_state(page: Any, tenant_name: str,
                                     expected_domain: str | None = None) -> tuple[str, str, str | None] | None:
    """Read the tenant details, tolerating an empty Account Scanning state.

    A tenant with no scan yet has no observed state; the Surface Account ID
    and Account UUID are still required. Returns None when the row or either
    required value is unavailable.
    """
    if not _open_tenant_details(page, tenant_name, expected_domain):
        return None
    surface_account_id = _detail_value(page, "Surface Account ID")
    account_uuid = _detail_value(page, "Account UUID")
    if not (isinstance(surface_account_id, str) and surface_account_id
            and isinstance(account_uuid, str) and account_uuid):
        return None
    return surface_account_id, account_uuid, _detail_value(page, "Account Scanning")


def _open_search(page: Any) -> Any | None:
    """Open the tenant-list search and return its input control.

    The MUIDataTable tenant search is a toolbar icon button (accessible name
    "Search"), not a text box; the text input (also accessible name "Search",
    auto-focused) only exists once the icon is clicked. Returns the input
    locator when exactly one is present, or None so the caller fails closed.
    """
    search_input = page.get_by_role("textbox", name="Search", exact=True)
    if search_input.count() == 1:
        return search_input
    search_button = page.get_by_role("button", name="Search", exact=True)
    if search_button.count() != 1:
        return None
    search_button.click()
    deadline = monotonic() + 5.0
    while monotonic() < deadline:
        if search_input.count() == 1:
            return search_input
        sleep(0.25)
    return None


def _form_field_id_by_label(page: Any, label: str) -> str | None:
    """Return the id of the single input whose associated label text matches.

    The associated label is resolved in-page from a <label for>, a wrapping
    <label>, or aria-labelledby. Returns None when zero or more than one input
    matches (or the lookup fails), so the caller fails closed on ambiguity.
    """
    try:
        matched_id = page.evaluate(
            """(label) => {
                const inputs = Array.from(document.querySelectorAll('input, select, textarea'));
                const safe = (fn) => { try { return fn(); } catch (e) { return null; } };
                const labelFor = (el) => {
                    let t = '';
                    if (el.id) {
                        t = safe(() => {
                            const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                            return l ? l.textContent : '';
                        }) || '';
                    }
                    if (!t) {
                        t = safe(() => {
                            const w = el.closest('label');
                            return w ? w.textContent : '';
                        }) || '';
                    }
                    if (!t && el.getAttribute('aria-labelledby')) {
                        t = safe(() => {
                            const ids = el.getAttribute('aria-labelledby').split(/\\s+/);
                            return ids.map(id => {
                                const e = document.getElementById(id);
                                return e ? e.textContent : '';
                            }).filter(Boolean).join(' ');
                        }) || '';
                    }
                    return t.trim();
                };
                const matches = inputs.filter(el => labelFor(el) === label);
                return matches.length === 1 && matches[0].id ? matches[0].id : null;
            }""",
            label,
        )
    except Exception:
        return None
    return matched_id if isinstance(matched_id, str) and matched_id else None


def _locate_form_field(page: Any, label: str, data_am: str) -> Any | None:
    """Locate one Add Account form field by accessible label, then by its
    stable data-am attribute, then by its associated label text.

    Returns the resolved locator when exactly one control matches, or None so
    the caller fails closed. The accessible label is preferred (it is the
    operator-facing name); the data-am attribute and the associated label text
    are fallbacks for a renamed or unlabeled control.
    """
    control = page.get_by_label(label, exact=True)
    if control.count() == 1:
        return control
    control = page.locator(f'[data-am="{data_am}"]')
    if control.count() == 1:
        return control
    matched_id = _form_field_id_by_label(page, label)
    if matched_id:
        control = page.get_by_id(matched_id)
        if control.count() == 1:
            return control
    return None


def _finish(reference: str, acknowledged_revision: str, result: str) -> str:
    """Record the final result (best effort) and return it.

    A dry run never touches the one-time create gate; it only writes its log.
    """
    if _ACTIVE_RUN_LOG is None or _ACTIVE_RUN_LOG.mode != DRY_RUN_MODE:
        try:
            record_runner_result(reference, acknowledged_revision, result, datetime.now().isoformat(timespec="seconds"),
                                 _ENTERED_LICENSE)
        except (OSError, ValueError, RunnerStateUnavailable):
            pass
    if _ACTIVE_RUN_LOG is not None:
        _ACTIVE_RUN_LOG.event("finish", result)
        _ACTIVE_RUN_LOG.write(result)
    return result


def _free_port() -> int:
    """Return an available localhost TCP port for the CDP endpoint."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chrome_executable() -> str | None:
    """Return the operator's installed Chrome, or None to use the bundled build."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _wait_for_cdp(port: int, timeout_seconds: float = 30.0) -> bool:
    """Poll the CDP version endpoint until it answers or the deadline passes."""
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        sleep(0.5)
    return False


def _cdp_request(port: int, path: str, *, method: str = "GET", timeout: float = 3.0) -> bytes | None:
    """Issue one loopback-only CDP HTTP request; return the body or None.

    The host is always 127.0.0.1: the automation browser is launched without
    --remote-debugging-address, so Chrome binds its DevTools endpoint to
    loopback only, and this helper never talks to any other host. Proxy
    settings are ignored so a loopback request is never routed via a proxy.
    """
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read(1_000_000)
    except Exception:
        return None


def _cdp_json(port: int, path: str, *, method: str = "GET") -> Any:
    """Return the decoded JSON body of a CDP HTTP endpoint, or None."""
    body = _cdp_request(port, path, method=method)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _cdp_page_targets(port: int) -> list[dict[str, str]]:
    """Return [{"id", "url"}] for every live page target via CDP /json.

    The /json endpoint always reflects the current target list, unlike a
    Playwright context.pages reference held across a cross-origin SSO redirect
    (which can replace the underlying CDP target and leave the old reference
    stale). Returns an empty list when the endpoint is unreachable.
    """
    targets = _cdp_json(port, "/json")
    if not isinstance(targets, list):
        return []
    pages: list[dict[str, str]] = []
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        url = target.get("url")
        if not isinstance(url, str):
            continue
        target_id = target.get("id")
        pages.append({"id": target_id if isinstance(target_id, str) else "", "url": url})
    return pages


def _cdp_page_urls(port: int, tab: "_AutomationTab | None" = None) -> list[str]:
    """Return the URLs of live page targets via the CDP /json endpoint.

    With ``tab`` the result is restricted to the tab this run opened (see
    _AutomationTab.select), so another tab in the reused automation window
    (for example one the operator opened at tenant-management) can never be
    mistaken for this run's page. Without ``tab`` every page target counts.
    """
    targets = _cdp_page_targets(port)
    if tab is not None:
        targets = tab.select(targets)
    return [target["url"] for target in targets]


def _cdp_open_tab(port: int, url: str) -> str | None:
    """Open a new tab at url in the automation browser; return its target id.

    Uses the CDP HTTP endpoint (PUT /json/new, required since Chrome 111), so
    the tab exists before any Playwright attach and is tracked by target id.
    Only the fixed Leonardo Development URLs, the anchor tab, or the local
    dashboard's sign-in page are opened.
    """
    if url != ANCHOR_TAB_URL and not url.startswith(DEVELOPMENT_ORIGIN + "/") and _dashboard_port(url) is None:
        return None
    created = _cdp_json(port, "/json/new?" + url, method="PUT")
    if not isinstance(created, dict):
        return None
    target_id = created.get("id")
    if not isinstance(target_id, str) or not CDP_TARGET_ID.fullmatch(target_id):
        return None
    return target_id


# The operator's dashboard opens as the first tab of the automation window
# (2026-10-03): the dashboard and its Leonardo tabs then share one Chrome
# window. Only the exact loopback sign-in URL qualifies.
DASHBOARD_LOGIN_URL = re.compile(r"http://127\.0\.0\.1:([0-9]{4,5})/login")


def _dashboard_port(url: str) -> int | None:
    match = DASHBOARD_LOGIN_URL.fullmatch(url)
    port = int(match.group(1)) if match else 0
    return port if 1024 <= port <= 65535 else None


def open_dashboard_tab(dashboard_port: int) -> str:
    """Show the local dashboard in the automation window (reuse or launch it); no Leonardo action.

    An existing dashboard tab for this port is brought to the front instead of
    opening a second one. Only the persisted desktop profile is used, so the
    window outlives this process.
    """
    url = f"http://127.0.0.1:{dashboard_port}/login"
    if _dashboard_port(url) is None:
        return "dashboard_port_invalid"
    profile_dir, persist = leonardo_profile()
    if not persist:
        return "automation_profile_not_persisted"
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "leonardo_profile_unavailable"
    port = _verified_automation_port(profile_dir)
    if port is None:
        executable = _chrome_executable()
        if executable is None:
            try:
                from playwright.sync_api import sync_playwright
            except ModuleNotFoundError:
                return "playwright_runtime_unavailable"
            with sync_playwright() as playwright:
                executable = playwright.chromium.executable_path
        _chrome_proc, port = _launch_automation_chrome(executable, profile_dir, persist=True)
        if port is None:
            return "browser_cdp_unavailable"
    prefix = f"http://127.0.0.1:{dashboard_port}/"
    for target in _cdp_page_targets(port):
        if target["url"].startswith(prefix) and CDP_TARGET_ID.fullmatch(target["id"]):
            _cdp_request(port, "/json/activate/" + target["id"])
            return "dashboard_tab_activated"
    return "dashboard_tab_opened" if _cdp_open_tab(port, url) else "browser_tab_unavailable"


def _cdp_close_tab(port: int, target_id: str) -> bool:
    """Close one tab by target id via CDP /json/close; True when accepted."""
    if not CDP_TARGET_ID.fullmatch(target_id):
        return False
    return _cdp_request(port, "/json/close/" + target_id) is not None


def _read_devtools_active_port(profile_dir: Path) -> tuple[int, str] | None:
    """Parse the DevToolsActivePort file Chrome writes into the user-data-dir.

    Line 1 is the CDP port and line 2 the per-instance browser WebSocket path
    (/devtools/browser/<id>). Returns (port, ws_path), or None when the file is
    missing, oversized, not ASCII, or malformed. The file holds no session
    material; its values are never logged.
    """
    path = profile_dir / DEVTOOLS_ACTIVE_PORT_FILE
    try:
        with path.open("rb") as handle:
            raw = handle.read(DEVTOOLS_ACTIVE_PORT_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > DEVTOOLS_ACTIVE_PORT_MAX_BYTES:
        return None
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None
    if len(lines) < 2:
        return None
    port_text, ws_path = lines[0].strip(), lines[1].strip()
    if not re.fullmatch(r"[0-9]{1,5}", port_text):
        return None
    port = int(port_text)
    if not 1 <= port <= 65535 or not CDP_BROWSER_WS_PATH.fullmatch(ws_path):
        return None
    return port, ws_path


def _verified_automation_port(profile_dir: Path) -> int | None:
    """Return the CDP port of the live automation browser for profile_dir.

    The DevToolsActivePort entry is trusted only when the loopback
    /json/version endpoint answers with a browser WebSocket URL whose host is
    loopback, whose port matches, and whose path equals the file's per-instance
    browser path. That proves the listener is the Chrome instance using this
    dedicated profile, not a stale file (crash) or an unrelated process that
    reused the port. Anything else means "not running": nothing is deleted.
    """
    from urllib.parse import urlsplit

    entry = _read_devtools_active_port(profile_dir)
    if entry is None:
        return None
    port, ws_path = entry
    version = _cdp_json(port, "/json/version")
    if not isinstance(version, dict):
        return None
    ws_url = version.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str):
        return None
    try:
        parts = urlsplit(ws_url)
        ws_port = parts.port
    except ValueError:
        return None
    if parts.scheme != "ws" or parts.hostname not in ("127.0.0.1", "localhost") \
            or ws_port != port or parts.path != ws_path:
        return None
    return port


def _url_path(url: str) -> str:
    """Return the path component of a URL, tolerant of query strings/fragments.

    Post-login tenant-management URLs carry query strings (e.g. ?tab=accounts)
    and the SSO redirect can append fragments; an exact full-URL match misses
    those, which is what turned a valid login into a development_login_timeout.
    """
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).path
    except Exception:
        return url.split("?", 1)[0].split("#", 1)[0]


def _url_origin(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}".lower()
    except Exception:
        return ""


def _is_tenant_management_url(url: str) -> bool:
    """True when a live page target is at the Development tenant-management route (exact origin).

    The path is compared without case: after a fresh SSO sign-in Leonardo
    lands on its own spelling, /backOffice/tenantManagement (2026-10-03),
    while the runner opens /backoffice/tenantManagement.
    """
    return (_url_origin(url) == DEVELOPMENT_ORIGIN
            and TENANT_MANAGEMENT_PATH.casefold() in _url_path(url).casefold())


def _wait_for_tenant_management(port: int, timeout_seconds: float, stable_polls: int = 5,
                                tab: "_AutomationTab | None" = None) -> bool:
    """Poll the CDP /json endpoint until a page target is stably at
    tenant-management.

    "Stably" means the tenant-management URL is observed on stable_polls
    consecutive 1-second polls: the app can briefly show the
    tenant-management URL before a client-side auth redirect sends an
    expired session to /login, and that pre-redirect sighting must not
    count as having reached tenant-management. With ``tab`` only the tab this
    run opened counts.
    """
    deadline = monotonic() + timeout_seconds
    stable_count = 0
    while monotonic() < deadline:
        urls = _cdp_page_urls(port, tab) if tab is not None else _cdp_page_urls(port)
        if any(_is_tenant_management_url(url) for url in urls):
            stable_count += 1
            if stable_count >= stable_polls:
                return True
        else:
            stable_count = 0
        sleep(1)
    return False


def _classify_leonardo_session(port: int, timeout_seconds: float, stable_polls: int = 5,
                               tab: "_AutomationTab | None" = None) -> str:
    """Classify the Leonardo session from live CDP page targets (read-only).

    Returns "active" when a page is stably at tenant-management (the URL is
    observed on stable_polls consecutive 1-second polls, so a pre-redirect
    sighting of an expired session does not count), "expired" when a page is
    present but never stably reaches tenant-management (the session
    redirected to login/SSO), and "unavailable" when no page target is
    reachable (likely a VPN/network issue). With ``tab`` only the tab this
    check opened is classified. This never fills, submits, or creates anything.
    """
    deadline = monotonic() + timeout_seconds
    saw_page = False
    stable_count = 0
    while monotonic() < deadline:
        at_tenant_management = False
        urls = _cdp_page_urls(port, tab) if tab is not None else _cdp_page_urls(port)
        for url in urls:
            saw_page = True
            if _is_tenant_management_url(url):
                at_tenant_management = True
        if at_tenant_management:
            stable_count += 1
            if stable_count >= stable_polls:
                return "active"
        else:
            stable_count = 0
        sleep(1)
    return "expired" if saw_page else "unavailable"


def leonardo_profile() -> tuple[Path, bool]:
    """Return (profile_dir, persist) for the attended Leonardo browser.

    On the operator desktop this is a dedicated, persisted automation profile
    (the documented §10 option-3 temporary development bridge): it retains the
    Leonardo Development session so SSO/MFA is only required when it expires.
    It is never the operator's main Chrome profile, never placed on the VM,
    and never copied to Git, logs, or backups. persist=True means the dir must
    be retained across runs (not deleted on completion). On any non-Windows
    host, or when an override is not set, a fresh temporary profile is used and
    persist=False so no session is ever retained off the operator desktop.
    """
    override = os.environ.get("SURFACE_LEONARDO_PROFILE_DIR")
    if override:
        return Path(override), True
    if os.name != "nt":
        return Path(tempfile.mkdtemp(prefix="attended_ce_chrome_")), False
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "SurfaceOnboarding" / "leonardo-automation", True


@dataclass
class _AutomationTab:
    """The one tab an attended operation opened in the automation browser.

    ``target_id`` is the CDP target this run created. ``preexisting`` holds the
    page target ids that existed before it was opened, so if a cross-origin
    SSO redirect replaces the target (new id) the single new, not-preexisting
    target is adopted as ours; zero or several such candidates select nothing
    (fail closed) so another tab is never mistaken for this run's page.
    ``chrome_proc`` is set only when this operation launched the browser.
    """
    port: int
    target_id: str
    preexisting: frozenset[str]
    profile_dir: Path
    persist: bool
    chrome_proc: Any | None
    reused: bool

    def select(self, targets: list[dict[str, str]]) -> list[dict[str, str]]:
        """Return the entries of targets that are this run's tab (0 or 1)."""
        for target in targets:
            if target.get("id") == self.target_id:
                return [target]
        replacements = [target for target in targets
                        if target.get("id") and target.get("id") not in self.preexisting]
        if len(replacements) == 1:
            self.target_id = replacements[0]["id"]
            return replacements
        return []

    def current_target_id(self) -> str | None:
        """Resolve this run's live target id now (None when gone/ambiguous)."""
        selected = self.select(_cdp_page_targets(self.port))
        return selected[0]["id"] if selected else None


def _stop_chrome_process(chrome_proc: Any) -> None:
    """Terminate a Chrome process this operation launched (best effort)."""
    if chrome_proc is None:
        return
    try:
        chrome_proc.terminate()
        chrome_proc.wait(timeout=10)
    except Exception:
        try:
            chrome_proc.kill()
        except Exception:
            pass


def _launch_automation_chrome(executable: str, profile_dir: Path, persist: bool) -> tuple[Any, int | None]:
    """Launch the automation Chrome with an OS-chosen loopback CDP port.

    --remote-debugging-port=0 lets Chrome bind a free loopback port itself
    (no _free_port() probe-then-bind race and no fixed, predictable port) and
    record it in DevToolsActivePort, which is also how later runs discover and
    verify this same instance for reuse. The window opens on a neutral anchor
    tab; each operation opens its own tab next to it. A persisted launch is
    placed in its own process group so it outlives the runner process (the
    window is reused by the next run). Returns (chrome_proc, port) where port
    is None when the endpoint was not verified in time.
    """
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                              "stdin": subprocess.DEVNULL}
    if persist and os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    chrome_proc = subprocess.Popen(
        [executable, f"--user-data-dir={profile_dir}", "--remote-debugging-port=0",
         "--no-first-run", "--no-default-browser-check", ANCHOR_TAB_URL],
        **kwargs,
    )
    deadline = monotonic() + BROWSER_LAUNCH_SECONDS
    while monotonic() < deadline:
        port = _verified_automation_port(profile_dir)
        if port is not None:
            return chrome_proc, port
        sleep(0.5)
    return chrome_proc, None


def _open_automation_tab(playwright: Any, url: str) -> _AutomationTab:
    """Open a new tab at url in the (reused or freshly launched) automation browser.

    With the persisted desktop profile an already-running automation Chrome
    (verified via DevToolsActivePort + /json/version) is reused: only a new
    tab is opened, so the Leonardo session stays warm and no new window
    appears. Otherwise the browser is launched. A temporary (non-persisted)
    profile is never reused: it is always a fresh launch that
    _release_automation_tab tears down completely.

    Security: while the reused automation window stays open its loopback CDP
    port lets any local process on this desktop drive that Leonardo
    Development session. The operator closes the automation window (or uses
    --close-browser / close_automation_browser) at the end of the day.
    Raises RuntimeError with leonardo_profile_unavailable,
    browser_cdp_unavailable, or browser_tab_unavailable.
    """
    profile_dir, persist = leonardo_profile()
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("leonardo_profile_unavailable") from exc
    chrome_proc = None
    port = _verified_automation_port(profile_dir) if persist else None
    reused = port is not None
    try:
        if port is None:
            executable = _chrome_executable() or playwright.chromium.executable_path
            chrome_proc, port = _launch_automation_chrome(executable, profile_dir, persist)
            if port is None:
                raise RuntimeError("browser_cdp_unavailable")
        preexisting = frozenset(target["id"] for target in _cdp_page_targets(port) if target["id"])
        target_id = _cdp_open_tab(port, url)
        if target_id is None:
            raise RuntimeError("browser_tab_unavailable")
        return _AutomationTab(port=port, target_id=target_id, preexisting=preexisting,
                              profile_dir=profile_dir, persist=persist,
                              chrome_proc=chrome_proc, reused=reused)
    except BaseException:
        # A browser this operation launched but could not use is not left
        # behind; a reused browser is never terminated here.
        _stop_chrome_process(chrome_proc)
        if not persist:
            shutil.rmtree(profile_dir, ignore_errors=True)
        raise


def _release_automation_tab(tab: _AutomationTab) -> None:
    """Tear down what this operation opened.

    Persisted profile: close ONLY this run's tab (resolved by target id, so a
    replaced SSO target is still found) and leave the automation window and
    its session running for the next run. A persisted profile is never
    removed here. Temporary profile: terminate the launched Chrome and delete
    the temporary profile, exactly as before.
    """
    if tab.persist:
        target_id = tab.current_target_id()
        if target_id is not None:
            _cdp_close_tab(tab.port, target_id)
        return
    _stop_chrome_process(tab.chrome_proc)
    shutil.rmtree(tab.profile_dir, ignore_errors=True)


def _page_target_id(context: Any, page: Any) -> str | None:
    """Return the CDP target id of a Playwright page, or None."""
    session = None
    try:
        session = context.new_cdp_session(page)
        info = session.send("Target.getTargetInfo")
        target_id = info["targetInfo"]["targetId"]
        return target_id if isinstance(target_id, str) else None
    except Exception:
        return None
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def _attach_attended_browser(playwright: Any) -> tuple[Any, Any, Any, _AutomationTab]:
    """Open this run's tab in the automation browser, wait for the operator's
    SSO/MFA (skipped when the session is still valid), then attach over CDP.

    Returns (browser, context, page, tab); the caller must pass tab to
    _release_automation_tab. Playwright's own launch() is avoided because its
    flag set terminates the browser process when it reaches the Leonardo
    Development host over the RND (Fortinet) VPN; a normally-launched Chrome
    with a loopback CDP port reaches the host reliably while keeping the
    session isolated in the dedicated automation profile.

    The login wait polls the CDP /json endpoint (which always reflects the
    live target list) restricted to this run's tab, rather than a held
    Playwright page reference: a cross-origin SSO redirect can replace the
    underlying CDP target, so a reference taken before login can go stale.
    Attaching only after this tab reaches tenant-management yields a fresh,
    valid page object, matched to this run's target id so another tab in the
    reused window is never used. Raises LoginTimeout if tenant-management is
    not reached in time.
    """
    tab = _open_automation_tab(playwright, TENANT_MANAGEMENT)
    try:
        if not _wait_for_tenant_management(tab.port, MAX_WAIT_SECONDS, tab=tab):
            raise LoginTimeout()
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{tab.port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = _find_live_page(context, tab)
        if page is None:
            try:
                browser.close()
            except Exception:
                pass
            raise RuntimeError("login_page_schema_unavailable")
        return browser, context, page, tab
    except BaseException:
        _release_automation_tab(tab)
        raise


@contextmanager
def _attended_page(playwright: Any):
    """Attach the attended browser and guarantee teardown; yield the page.

    _attach_attended_browser already releases its tab on its own failure, so
    teardown here only handles the attached (success) path. browser.close() on
    a connect_over_cdp browser only disconnects Playwright; it does not close
    the automation browser. A persisted automation profile keeps its window
    and session (only this run's tab is closed); a temporary profile is torn
    down completely.
    """
    browser, _context, page, tab = _attach_attended_browser(playwright)
    try:
        yield page
    finally:
        try:
            browser.close()
        except Exception:
            pass
        _release_automation_tab(tab)


def _find_live_page(context: Any, tab: _AutomationTab | None = None) -> Any:
    """Return the live page at the tenant-management URL, or None.

    A cross-origin SSO redirect can replace the underlying CDP target, leaving
    any previously-held page reference stale (its .url never advances past the
    login page). Re-scanning context.pages adopts the current live page. With
    ``tab`` only the page whose CDP target id is this run's tab is accepted,
    and anything other than exactly one such page returns None (fail closed).
    """
    if tab is None:
        for candidate in context.pages:
            try:
                url = candidate.url
            except Exception:
                continue
            if _is_tenant_management_url(url):
                return candidate
        return None
    target_id = tab.current_target_id()
    if target_id is None:
        return None
    matches = []
    for candidate in context.pages:
        try:
            url = candidate.url
        except Exception:
            continue
        if _is_tenant_management_url(url) and _page_target_id(context, candidate) == target_id:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def check_leonardo_session() -> str:
    """Read-only Leonardo Development session check (the §10 bridge).

    Opens a new tab at tenant-management in the automation browser (reusing
    the running automation window, or launching it with the dedicated
    persisted profile) and classifies only that tab via CDP page targets.
    Returns "leonardo_session_active", "leonardo_session_expired", or
    "leonardo_session_unavailable" (plus fail-closed codes). It never fills,
    submits, or creates; it closes only its own tab and never deletes the
    persisted profile, so a valid session is reused by the next attended run.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    with sync_playwright() as playwright:
        try:
            tab = _open_automation_tab(playwright, TENANT_MANAGEMENT)
        except RuntimeError as error:
            return str(error)
        try:
            classification = _classify_leonardo_session(tab.port, SESSION_CHECK_SECONDS, tab=tab)
            if classification == "active" and SESSION_API_CHECK_ENABLED:
                return _api_session_check(playwright, tab)
            return {
                "active": "leonardo_session_active",
                "expired": "leonardo_session_expired",
                "unavailable": "leonardo_session_unavailable",
            }[classification]
        finally:
            _release_automation_tab(tab)


def bootstrap_leonardo_session(wait_seconds: float = MAX_WAIT_SECONDS) -> str:
    """Open the attended browser so the operator can complete SSO/MFA once.

    Opens a new tab at tenant-management in the automation browser (reused
    or launched with the dedicated persisted profile) and waits (bounded)
    until that tab is stably there: with a valid session this returns almost
    immediately; with an expired session the operator completes SSO/MFA in
    that tab and the wait then succeeds. It never fills, submits, or creates;
    it only establishes or refreshes the persisted session, then closes its
    own tab and leaves the automation window running.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    with sync_playwright() as playwright:
        try:
            tab = _open_automation_tab(playwright, TENANT_MANAGEMENT)
        except RuntimeError as error:
            return str(error)
        try:
            if not _wait_for_tenant_management(tab.port, wait_seconds, tab=tab):
                return "development_login_timeout"
            return "leonardo_session_bootstrapped"
        finally:
            _release_automation_tab(tab)


def _send_browser_close(port: int) -> None:
    """Ask the automation browser to exit via CDP Browser.close (best effort)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            # The connection drops as the browser exits; the caller verifies.
            pass
        try:
            browser.close()
        except Exception:
            pass


def _close_automation_browser_at(profile_dir: Path, timeout_seconds: float = BROWSER_CLOSE_SECONDS) -> str:
    """Close the running automation browser for profile_dir, if any.

    Returns "automation_browser_not_running" (no verified instance),
    "automation_browser_closed" (Browser.close sent and the loopback endpoint
    verified gone), or "automation_browser_close_unavailable" (fail closed:
    the browser could not be closed or is still answering).
    """
    port = _verified_automation_port(profile_dir)
    if port is None:
        return "automation_browser_not_running"
    try:
        _send_browser_close(port)
    except Exception:
        return "automation_browser_close_unavailable"
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if _cdp_request(port, "/json/version") is None:
            return "automation_browser_closed"
        sleep(0.5)
    return "automation_browser_close_unavailable"


def close_automation_browser() -> str:
    """Close the reused automation Chrome window (ends the warm CDP exposure).

    The persisted profile (and therefore the Leonardo session) is retained;
    only the running browser exits. A temporary (non-persisted) profile never
    outlives a run, so there is nothing to close.
    """
    profile_dir, persist = leonardo_profile()
    if not persist:
        shutil.rmtree(profile_dir, ignore_errors=True)
        return "automation_browser_not_running"
    return _close_automation_browser_at(profile_dir)


def reset_leonardo_profile() -> bool:
    """Wipe the dedicated persisted Leonardo automation profile.

    Forces a fresh SSO/MFA on the next attended run. A running automation
    browser is closed first (CDP Browser.close) so the profile is not wiped
    underneath a live session; if it cannot be closed the reset fails closed
    and nothing is removed. Only removes the dedicated automation profile
    (never the operator's main Chrome profile). Returns True when the profile
    was removed (or was already absent).
    """
    profile_dir, persist = leonardo_profile()
    if not profile_dir.exists():
        return True
    if persist and _close_automation_browser_at(profile_dir) == "automation_browser_close_unavailable":
        return False
    for attempt in range(PROFILE_REMOVE_ATTEMPTS):
        shutil.rmtree(profile_dir, ignore_errors=True)
        if not profile_dir.exists():
            return True
        if attempt + 1 < PROFILE_REMOVE_ATTEMPTS:
            # Chrome can hold profile files briefly after its endpoint closes.
            sleep(1)
    return not profile_dir.exists()


def reset_runner_record(reference: str) -> bool:
    """Re-arm one completed, non-successful attended run for the same revision.

    Refuses to reset an in-flight run (no result yet) or a run that verified a
    created tenant (readback_verified), so a successful creation can never be
    re-run. Any other recorded result (a blocked or no-create outcome) removes
    the record so the operator may start again; the duplicate check still
    guards against an existing tenant.
    """
    if not REFERENCE.fullmatch(reference):
        raise ValueError("invalid_runner_state_record")
    state = load_runner_state()
    record = state.get(reference)
    if record is None:
        return True
    if "result" not in record:
        raise ValueError("runner_in_progress_cannot_be_reset")
    if record["result"] == "readback_verified":
        raise ValueError("runner_result_cannot_be_reset")
    if create_uncertain(record):
        raise ValueError("create_uncertain_cannot_be_reset")
    del state[reference]
    _write_json_atomic(RUNNER_STATE_PATH, state)
    return True


def _locate_confirm_button(page: Any) -> Any | None:
    """Locate the single Add Account Confirm control, failing closed on ambiguity.

    The Confirm control is located by its exact accessible name. The exact
    label is confirmed on the first attended run; any zero or multiple match
    stops the runner before the form is submitted so an uncertain mutation
    never occurs. Diagnostics capture the live button names for diagnosis.
    """
    control = page.get_by_role("button", name="Confirm", exact=True)
    try:
        count = control.count()
    except Exception:
        return None
    if count == 1:
        return control
    return None


class RunLog:
    """Redacted, step-by-step local log of one attended run.

    Records each runner step and field outcome, plus browser-side signals
    (console errors/warnings, uncaught page errors, failed requests, and the
    method/path/status of Leonardo API calls) so a failed run can be diagnosed
    without re-running it. It never records cookies, headers, request or
    response bodies, or query strings, and every known source value (tenant
    name, domains, email) is replaced with a placeholder before it is stored.
    Failures are swallowed: logging must never change the runner's outcome.
    """

    def __init__(self, reference: str, mode: str, route: str = CE_ENGINE) -> None:
        self.reference = reference
        self.mode = mode
        # Recorded only for non-CE runs so the CE-only log format is unchanged.
        self.route = route
        self.started_on = datetime.now().isoformat(timespec="seconds")
        self.events: list[dict[str, str]] = []
        self.diagnostics: list[dict[str, Any]] = []
        self._redact: list[str] = []
        # Diagnose timeline (--diagnose-confirm): after each form step, record
        # Confirm's state and the form component's boolean hooks (no values),
        # and log every Leonardo API request as it starts.
        self.timeline = False
        self.timeline_page: Any = None
        self._in_timeline = False

    def _timeline_snapshot(self, step: str) -> None:
        if (not self.timeline or self.timeline_page is None or self._in_timeline
                or not step.startswith(TIMELINE_STEP_PREFIXES)):
            return
        self._in_timeline = True
        try:
            snapshot = self.timeline_page.evaluate(TIMELINE_SNAPSHOT_JS)
            self.event("timeline", "snapshot", step, snapshot if isinstance(snapshot, str) else "unreadable")
        except Exception:
            pass
        finally:
            self._in_timeline = False

    def add_redactions(self, *values: str) -> None:
        for value in values:
            if isinstance(value, str) and len(value.strip()) >= 3:
                self._redact.append(value.strip())
        self._redact.sort(key=len, reverse=True)

    def _clean(self, text: object) -> str:
        cleaned = " ".join(str(text).split())
        for value in self._redact:
            cleaned = re.sub(re.escape(value), "<value>", cleaned, flags=re.IGNORECASE)
        return cleaned[:RUN_LOG_DETAIL_CHARS]

    def event(self, step: str, outcome: str, field: str = "", detail: object = "") -> None:
        try:
            if len(self.events) >= RUN_LOG_MAX_EVENTS:
                return
            entry = {"t": datetime.now().isoformat(timespec="seconds"), "step": step, "outcome": outcome}
            if field:
                entry["field"] = field
            if detail:
                entry["detail"] = self._clean(detail)
            self.events.append(entry)
        except Exception:
            pass
        self._timeline_snapshot(step)

    def error(self, step: str, field: str, exc: BaseException) -> None:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        self.event(step, "error", field, f"{type(exc).__name__}: {message}")

    def attach(self, page: Any) -> None:
        """Subscribe to browser-side signals on the attended page."""
        def _path(url: str) -> str:
            return _url_path(url) if url.startswith(DEVELOPMENT_ORIGIN) else "<other-origin>"

        def on_console(message: Any) -> None:
            try:
                if message.type in ("error", "warning"):
                    self.event("browser_console", message.type, detail=message.text)
            except Exception:
                pass

        def on_page_error(error: Any) -> None:
            self.event("browser_page_error", "error", detail=error)

        def on_request_failed(request: Any) -> None:
            try:
                self.event("browser_request_failed", "error", _path(request.url),
                           f"{request.method} {request.failure or ''}")
            except Exception:
                pass

        def on_response(response: Any) -> None:
            try:
                url = response.url
                if "/api/" in url or response.status >= 400:
                    self.event("browser_response", str(response.status), _path(url), response.request.method)
            except Exception:
                pass

        def on_request(request: Any) -> None:
            try:
                if "/api/" in request.url:
                    self.event("browser_request", "started", _path(request.url), request.method)
            except Exception:
                pass

        handlers = [("console", on_console), ("pageerror", on_page_error),
                    ("requestfailed", on_request_failed), ("response", on_response)]
        if self.timeline:
            handlers.append(("request", on_request))
        for name, handler in handlers:
            try:
                page.on(name, handler)
            except Exception:
                pass

    def write(self, result: str) -> None:
        """Append this run to the local log file (keeps the newest runs only)."""
        try:
            try:
                existing = json.loads(RUN_LOG_PATH.read_text(encoding="utf-8"))
                runs = existing.get("runs", []) if isinstance(existing, dict) else []
                if not isinstance(runs, list):
                    runs = []
            except (OSError, ValueError):
                runs = []
            entry: dict[str, Any] = {
                "reference": self.reference, "mode": self.mode, "started_on": self.started_on,
                "completed_on": datetime.now().isoformat(timespec="seconds"),
                "result": result, "events": self.events,
                "diagnostics": self.diagnostics[-RUN_LOG_MAX_DIAGNOSTICS:],
            }
            if self.route != CE_ENGINE:
                entry["route"] = str(self.route)[:64]
            runs.append(entry)
            _write_json_atomic(RUN_LOG_PATH, {
                "note": "Redacted attended-runner step log. No cookies, headers, bodies, query strings, or source values.",
                "runs": runs[-RUN_LOG_MAX_RUNS:],
            })
        except Exception:
            pass


_ACTIVE_RUN_LOG: RunLog | None = None
# Licence (start, end) as entered just before Confirm in the current create run.
_ENTERED_LICENSE: tuple[date, date] | None = None


def _log() -> RunLog:
    """The active run's log, or a throwaway log outside a run."""
    return _ACTIVE_RUN_LOG if _ACTIVE_RUN_LOG is not None else RunLog("-", "none")


def _locate_select(page: Any, label: str) -> Any | None:
    """Locate one Add Account <select> by its stable name attribute.

    The live form's selects carry no associated <label> (2026-09-29
    diagnostics), so a label lookup finds nothing; the name attribute is the
    stable contract. The label lookup remains as a fallback.
    """
    name = SELECT_NAMES.get(label)
    if name:
        try:
            control = page.locator(f'select[name="{name}"]')
            if control.count() == 1:
                return control
        except Exception:
            pass
    return _locate_form_field(page, label, "")


def _selected_option_text(control: Any) -> str:
    selected = control.evaluate(
        "el => el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex].textContent : ''")
    return " ".join(str(selected or "").split())


def _fill_text_control(page: Any, label: str, expected: str, step: str = "fill_text") -> tuple[str | None, Any]:
    """Fill one labelled text control and verify it kept the value."""
    log = _log()
    control = _locate_form_field(page, label, "")
    if control is None:
        log.event(step, "not_found", label)
        return "fill_form_schema_unavailable", None
    try:
        if not control.first.is_enabled():
            log.event(step, "disabled", label)
            return "fill_form_schema_unavailable", None
        control.first.fill(expected, timeout=FIELD_TIMEOUT_MS)
        actual = control.first.input_value()
    except Exception as exc:
        log.error(step, label, exc)
        return "fill_form_schema_unavailable", None
    if actual != expected:
        log.event(step, "mismatch", label)
        return "fill_value_mismatch", None
    log.event(step, "ok", label)
    return None, control


def _text_control_value(page: Any, label: str) -> str | None:
    """Re-read one labelled text control; None when missing or unreadable."""
    control = _locate_form_field(page, label, "")
    if control is None:
        return None
    try:
        value = control.first.input_value()
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _verify_blank_text(page: Any, label: str) -> str | None:
    """Verify a control the plan leaves empty is present and empty."""
    control = _locate_form_field(page, label, "")
    if control is None:
        _log().event("verify_blank", "not_found", label)
        return "fill_form_schema_unavailable"
    value = _text_control_value(page, label)
    if value is None:
        _log().event("verify_blank", "unreadable", label)
        return "fill_form_schema_unavailable"
    if value.strip():
        _log().event("verify_blank", "not_empty", label)
        return "fill_value_mismatch"
    _log().event("verify_blank", "ok", label)
    return None


def _verify_operator_account_empty(page: Any) -> str | None:
    """Verify the Operator Account react-select has no selection (Dev skip).

    The control must resolve to exactly one input, its typed value must be
    empty, and its select container must hold no selected-value element.
    Anything unreadable fails closed; no value is ever entered.
    """
    log = _log()
    try:
        control = page.locator(OPERATOR_ACCOUNT_INPUT_SELECTOR)
        if control.count() != 1:
            log.event("verify_operator_account", "not_found", detail=f"count={control.count()}")
            return "fill_form_schema_unavailable"
        typed = control.first.input_value()
        selected = control.first.evaluate(
            """el => {
                const box = el.closest('[class*="container"]') || el.parentElement;
                if (!box) return -1;
                return box.querySelectorAll('[class*="multiValue"], [class*="multi-value"], '
                    + '[class*="singleValue"], [class*="single-value"]').length;
            }""")
    except Exception as exc:
        log.error("verify_operator_account", "Operator Account", exc)
        return "fill_form_schema_unavailable"
    if not isinstance(selected, int) or selected < 0:
        log.event("verify_operator_account", "unreadable")
        return "fill_form_schema_unavailable"
    if (typed or "").strip() or selected:
        log.event("verify_operator_account", "not_empty", detail=f"selected={selected}")
        return "fill_value_mismatch"
    log.event("verify_operator_account", "ok")
    return None


def _locate_advanced_text(page: Any, label: str) -> Any | None:
    """Locate one Advanced options input by its label, failing closed.

    Live probe 2026-09-29: "Maximum scan Duration (hours)" is a type=number
    input with no name, id, or data-am; its label is the <label> of the
    enclosing .MuiFormControl-root. The accessible-label lookups are tried
    first, then that enclosing form-control label (exactly one input).
    """
    control = _locate_form_field(page, label, "")
    if control is not None:
        return control
    try:
        escaped = label.replace("\\", "\\\\").replace('"', '\\"')
        control = page.locator(
            f'.MuiFormControl-root:has(> label:text-is("{escaped}")) input:not([type=hidden])')
        if control.count() == 1:
            return control
    except Exception:
        return None
    return None


def _fill_advanced_texts(page: Any, advanced_texts: dict[str, str]) -> str | None:
    """Fill the Advanced options text controls (Maximum scan Duration).

    The section is already expanded (the advanced toggles resolved). The live
    default (24) is overwritten and re-read. A missing or ambiguous control
    stops the run with max_scan_duration_schema_unavailable and a log event.
    """
    log = _log()
    for label, expected in advanced_texts.items():
        control = _locate_advanced_text(page, label)
        if control is None:
            log.event("max_scan_duration", "not_found", label)
            return "max_scan_duration_schema_unavailable"
        try:
            if not control.first.is_enabled():
                log.event("max_scan_duration", "disabled", label)
                return "max_scan_duration_schema_unavailable"
            control.first.fill(expected, timeout=FIELD_TIMEOUT_MS)
            actual = control.first.input_value()
        except Exception as exc:
            log.error("max_scan_duration", label, exc)
            return "max_scan_duration_schema_unavailable"
        if actual != expected:
            log.event("max_scan_duration", "mismatch", label)
            return "fill_value_mismatch"
        log.event("max_scan_duration", "ok", label)
    return None


def _advanced_text_value(page: Any, label: str) -> str | None:
    control = _locate_advanced_text(page, label)
    try:
        value = control.first.input_value() if control is not None else None
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _fill_add_account_form(page: Any, plan: dict[str, Any]) -> tuple[str | None, Any]:
    """Fill the Add Account form for any route plan, logging every field outcome.

    Order: early selects (they can reset dependent controls), expand Advanced
    options, toggles (dependent controls such as the Leaked Credentials
    interval and domains may only be enabled once their toggle is on), the
    optional Advanced options texts, the remaining selects, text fields, the
    optional blank-control and Operator Account checks, license dates, then a
    final re-read of every toggle, select, date, and optional control before
    Confirm. Each control must resolve to exactly one enabled element and keep
    the value set. The optional keys (advanced_texts, blank_texts,
    operator_account_empty) are absent from the CE-only plan, whose fill is
    unchanged. Returns (failure_code, company_name_control); failure_code is
    None on success.
    """
    log = _log()
    early = {label: option for label, option in plan["selects"].items() if label in EARLY_SELECTS}
    late = {label: option for label, option in plan["selects"].items() if label not in EARLY_SELECTS}
    for label, option in early.items():
        failure = _fill_select(page, label, option)
        if failure:
            return failure, None
    # Controls the plan expects the form to have removed (e.g. Scan now once a
    # scanning schedule is set) must really be gone.
    for key in plan.get("absent_checkboxes") or ():
        if _checkbox_count(page, key) != 0:
            log.event("absent_toggle", "present", key)
            return "fill_form_schema_unavailable", None
        log.event("absent_toggle", "ok", key)
    if not _expand_advanced_options(page, plan["checkboxes"]):
        return "fill_form_schema_unavailable", None
    for key, target in plan["checkboxes"].items():
        if not _set_checkbox(page, key, target):
            log.event("fill_toggle", "failed", key, f"target={'on' if target else 'off'}")
            return "fill_form_schema_unavailable", None
        log.event("fill_toggle", "ok", key)
    advanced_texts = plan.get("advanced_texts") or {}
    if advanced_texts:
        failure = _fill_advanced_texts(page, advanced_texts)
        if failure:
            return failure, None
    for label, option in late.items():
        failure = _fill_select(page, label, option)
        if failure:
            return failure, None
    company_control = None
    for label, expected in plan["texts"].items():
        failure, control = _fill_text_control(page, label, expected)
        if failure:
            return failure, None
        if label == "Company name":
            company_control = control
    for label in plan.get("blank_texts") or ():
        failure = _verify_blank_text(page, label)
        if failure:
            return failure, None
    if plan.get("operator_account_empty"):
        failure = _verify_operator_account_empty(page)
        if failure:
            return failure, None
    for key in ("license_start", "license_end"):
        if not _fill_license_date(page, key, plan[key]):
            log.event("fill_date", "failed", key)
            return "fill_form_schema_unavailable", None
        log.event("fill_date", "ok", key)
    # Final re-read before Confirm: a later change must not have flipped an
    # earlier toggle or select (e.g. a dependent control resetting another).
    for key, target in plan["checkboxes"].items():
        control = _locate_checkbox(page, key)
        try:
            ok = control is not None and control.first.is_checked() == target
        except Exception:
            ok = False
        if not ok:
            log.event("verify_toggle", "mismatch", key)
            return "fill_value_mismatch", None
    for label, option in plan["selects"].items():
        control = _locate_select(page, label)
        try:
            ok = control is not None and _selected_option_text(control.first) == option
        except Exception:
            ok = False
        if not ok:
            log.event("verify_select", "mismatch", label)
            return "fill_value_mismatch", None
    for key in ("license_start", "license_end"):
        if not _license_date_kept(page, key, plan[key]):
            log.event("verify_date", "mismatch", key)
            return "fill_value_mismatch", None
    for label, expected in advanced_texts.items():
        if _advanced_text_value(page, label) != expected:
            log.event("verify_text", "mismatch", label)
            return "fill_value_mismatch", None
    for label in plan.get("blank_texts") or ():
        value = _text_control_value(page, label)
        if value is None or value.strip():
            log.event("verify_blank", "mismatch", label)
            return "fill_value_mismatch", None
    if plan.get("operator_account_empty") and _verify_operator_account_empty(page) is not None:
        return "fill_value_mismatch", None
    for key in plan.get("absent_checkboxes") or ():
        if _checkbox_count(page, key) != 0:
            log.event("verify_absent", "present", key)
            return "fill_value_mismatch", None
    log.event("verify_form", "ok")
    return None, company_control


def _checkbox_count(page: Any, key: str) -> int:
    """How many controls match one checkbox key (-1 when the lookup fails)."""
    try:
        if key == "scan_now":
            return page.get_by_role("checkbox", name="primary checkbox", exact=True).count()
        return page.locator(f'input[type=checkbox][name="{key}"]').count()
    except Exception:
        return -1


# The CE-only name is kept for callers and tests; the CE plan has none of the
# optional keys, so its fill order and events are unchanged.
_fill_ce_form = _fill_add_account_form


def _expand_advanced_options(page: Any, checkboxes: dict[str, bool]) -> bool:
    """Expand the collapsed "Advanced options" section when the plan needs it.

    Idempotent: the section is opened only while an advanced toggle cannot be
    found, so a section the form already shows open is never collapsed. Fails
    closed (False) when the button is missing or the toggles never appear.
    """
    needed = [key for key in checkboxes if key in ADVANCED_TOGGLES_OFF]
    if not needed:
        return True
    log = _log()
    probe = needed[0]
    if _locate_checkbox(page, probe) is not None:
        log.event("advanced_options", "already_open")
        return True
    try:
        button = page.locator(ADD_ACCOUNT_MODAL_SELECTOR).get_by_text(ADVANCED_OPTIONS_TEXT, exact=True)
        if button.count() < 1:
            log.event("advanced_options", "button_not_found")
            return False
        button.first.click(timeout=FIELD_TIMEOUT_MS)
    except Exception as exc:
        log.error("advanced_options", "click", exc)
        return False
    for _poll in range(ADVANCED_EXPAND_POLLS):
        if all(_locate_checkbox(page, key) is not None for key in needed):
            log.event("advanced_options", "expanded")
            return True
        page.wait_for_timeout(100)
    missing = [key for key in needed if _locate_checkbox(page, key) is None]
    log.event("advanced_options", "toggles_missing", detail=",".join(missing))
    return False


def _confirm_enabled(page: Any, confirm: Any) -> bool:
    """Wait briefly for Confirm to enable; on failure log which fields are invalid.

    Only field identifiers (name, id, data-am, associated label) of controls
    marked invalid are logged — never their values.
    """
    for _poll in range(CONFIRM_ENABLE_POLLS):
        try:
            if confirm.first.is_enabled():
                return True
        except Exception:
            pass
        page.wait_for_timeout(250)
    try:
        invalid = page.evaluate(
            """() => Array.from(document.querySelectorAll('[aria-invalid="true"], .Mui-error input, .Mui-error select'))
                .slice(0, 20).map(el => {
                    const label = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
                    return [el.getAttribute('name'), el.getAttribute('data-am'), label ? label.textContent : '']
                        .filter(Boolean).join('/').slice(0, 80);
                })""")
    except Exception:
        invalid = []
    _log().event("confirm_enable", "disabled", detail="invalid=" + ",".join(str(i) for i in (invalid or [])))
    _log_form_validation(page)
    _log_form_field_errors(page)
    return False


LICENSE_START_FALLBACK_DAYS = 1


def _license_date_value(page: Any, key: str) -> date | None:
    control = _locate_license_date(page, key)
    try:
        return _parse_display_date(control.first.input_value()) if control is not None else None
    except Exception:
        return None


def _ensure_confirm_enabled(page: Any, confirm: Any, plan: dict[str, Any]) -> bool:
    """Wait for Confirm; if it stays disabled, retry once with the start one day earlier.

    Live 2026-09-30 (CO-0649, operator-verified by hand): Leonardo Development
    accepted the run day (Sep 30) in the start-date picker but kept Confirm
    disabled with no field error; Sep 29 enabled it. Owner decision
    2026-09-30 (option D): use the latest day Leonardo accepts, at most
    LICENSE_START_FALLBACK_DAYS before the run day. The start is re-picked,
    both dates are re-read (the expiration must be unchanged), and the runner
    continues only if Confirm then enables. The plan's license_start is
    updated to the date actually entered. Confirm is never clicked here.
    """
    if _confirm_enabled(page, confirm):
        return True
    log = _log()
    start, end = plan.get("license_start"), plan.get("license_end")
    if not isinstance(start, date) or not isinstance(end, date):
        return False
    for back in range(1, LICENSE_START_FALLBACK_DAYS + 1):
        earlier = start - timedelta(days=back)
        log.event("license_start_fallback", "retry", detail=f"start={earlier.isoformat()}")
        if not _fill_license_date(page, "license_start", earlier):
            log.event("license_start_fallback", "pick_failed", detail=f"start={earlier.isoformat()}")
            return False
        shown_start, shown_end = _license_date_value(page, "license_start"), _license_date_value(page, "license_end")
        if shown_start != earlier or shown_end != end:
            log.event("license_start_fallback", "dates_mismatch",
                      detail=f"start_ok={shown_start == earlier} end_ok={shown_end == end}")
            return False
        if _confirm_enabled(page, confirm):
            plan["license_start"] = earlier
            log.event("license_start_fallback", "confirm_enabled",
                      detail=f"start={earlier.isoformat()} end={end.isoformat()}")
            return True
    log.event("license_start_fallback", "still_disabled")
    return False


# Domains and email addresses in form messages are redacted before logging,
# on top of the run log's own redaction of every known source value.
_DOMAIN_TOKEN = re.compile(r"(?i)[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b")


def _redact_domains(text: object) -> str:
    return _DOMAIN_TOKEN.sub("<domain>", str(text))


def _log_form_validation(page: Any, stage: str = "") -> None:
    """Log the open form's validation state without any field value.

    For each visible control that is flagged (required and empty,
    aria-invalid, inside .Mui-error, failing the browser's constraint
    validation, or disabled) its label/name and flags are logged; number
    inputs always report their min/max/step. Visible error/helper texts and
    the Confirm control's disabled/title attributes are logged too. Values are
    only tested for emptiness in the page and never returned. Best effort:
    diagnostics never change the runner's outcome.
    """
    log = _log()
    try:
        report = page.evaluate(
            """(modalSelector) => {
                const root = document.querySelector(modalSelector) || document;
                const safe = (fn) => { try { return fn(); } catch (e) { return ''; } };
                const labelOf = (el) => {
                    let t = el.id ? safe(() => {
                        const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        return l ? l.textContent : '';
                    }) : '';
                    if (!t) t = safe(() => { const w = el.closest('label'); return w ? w.textContent : ''; });
                    if (!t) t = safe(() => {
                        const box = el.closest('.MuiFormControl-root');
                        const l = box ? box.querySelector('label') : null;
                        return l ? l.textContent : '';
                    });
                    return (t || '').replace(/\\s+/g, ' ').trim();
                };
                const controls = [];
                for (const el of root.querySelectorAll('input, select, textarea')) {
                    if (el.type === 'hidden') continue;
                    const name = (labelOf(el) || el.getAttribute('name') || el.getAttribute('placeholder')
                        || el.getAttribute('data-am') || el.tagName.toLowerCase()).slice(0, 80);
                    const flags = [];
                    const empty = el.type !== 'checkbox' && !String(el.value || '').trim();
                    if (el.required && empty) flags.push('required-empty');
                    if (el.getAttribute('aria-invalid') === 'true') flags.push('aria-invalid');
                    if (el.closest('.Mui-error')) flags.push('mui-error');
                    if (typeof el.checkValidity === 'function' && !el.checkValidity())
                        flags.push('invalid:' + String(el.validationMessage || '').slice(0, 100));
                    if (el.disabled) flags.push('disabled');
                    if (el.type === 'number') flags.push('min=' + el.min, 'max=' + el.max, 'step=' + el.step);
                    if (flags.length) controls.push(name + ' [' + flags.join(';') + ']');
                }
                // Settings dropdowns: the selected option text (never Country,
                // which is source data) and whether the control is disabled.
                const selects = [];
                for (const el of root.querySelectorAll('select')) {
                    const name = el.getAttribute('name') || labelOf(el) || 'select';
                    if (name === 'accountCountry') continue;
                    const opt = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    selects.push(name.slice(0, 60) + '=' + (opt ? String(opt.textContent || '').trim().slice(0, 60) || '<blank>' : '<none>')
                        + (el.disabled ? ' (disabled)' : '') + ' options=' + el.options.length);
                }
                const messages = [];
                const selector = '.Mui-error, .MuiFormHelperText-root, [role="alert"], [class*="error" i], '
                    + '[class*="invalid" i], [class*="warning" i]';
                for (const el of root.querySelectorAll(selector)) {
                    if (el.offsetParent === null) continue;
                    const text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (text && text.length <= 200 && !messages.includes(text)) messages.push(text.slice(0, 120));
                    if (messages.length >= 20) break;
                }
                let buttons = Array.from(root.querySelectorAll('button'))
                    .filter(b => (b.textContent || '').trim() === 'Confirm');
                if (!buttons.length) buttons = Array.from(document.querySelectorAll('button'))
                    .filter(b => (b.textContent || '').trim() === 'Confirm');
                const confirm = buttons.slice(0, 3).map(b => 'disabled=' + b.disabled
                    + ' aria-disabled=' + b.getAttribute('aria-disabled')
                    + ' title=' + String(b.getAttribute('title')
                        || (b.parentElement && b.parentElement.getAttribute('title')) || '').slice(0, 100));
                return {controls: controls.slice(0, 60), selects: selects.slice(0, 20), messages: messages, confirm: confirm};
            }""",
            ADD_ACCOUNT_MODAL_SELECTOR)
    except Exception as exc:
        log.error("form_validation", stage or "report", exc)
        return
    if not isinstance(report, dict):
        log.event("form_validation", "unreadable", stage)
        return
    for kind in ("controls", "selects", "messages", "confirm"):
        items = report.get(kind)
        for item in (items if isinstance(items, list) else [])[:60]:
            log.event("form_validation", kind.rstrip("s"), stage, _redact_domains(item))
    log.event("form_validation", "reported", stage,
              "controls={} messages={}".format(len(report.get("controls") or []), len(report.get("messages") or [])))


def _log_react_form_state(page: Any, stage: str = "") -> None:
    """Log the React form library's own validation state, without values.

    Walks the React fiber tree up from the Confirm button (bounded) and
    reports (a) any ``errors`` object found in component props, hook state,
    or context (Formik / react-hook-form style) as field paths with their
    messages, (b) scalar form flags such as isValid/dirty, and (c) boolean
    props named like valid/disabled/error on the Confirm button's ancestors.
    ``values`` are never read. Messages are domain-redacted and the run log
    also redacts every known source value. Best effort only.
    """
    log = _log()
    try:
        state = page.evaluate(
            """(modalSelector) => {
                const out = {errors: [], flags: [], props: [], sources: []};
                const button = Array.from(document.querySelectorAll('button'))
                    .find(b => (b.textContent || '').trim() === 'Confirm');
                const start = button || document.querySelector(modalSelector);
                if (!start) { out.flags.push('no-start-element'); return out; }
                const key = Object.keys(start).find(k => k.startsWith('__reactFiber$')
                    || k.startsWith('__reactInternalInstance$'));
                if (!key) { out.flags.push('no-react-fiber'); return out; }
                const seen = new WeakSet();
                const flatten = (obj, path, depth) => {
                    if (out.errors.length >= 40 || depth > 5 || obj === null || obj === undefined) return;
                    if (typeof obj === 'string') { out.errors.push(path + ': ' + obj.slice(0, 100)); return; }
                    if (typeof obj !== 'object') { out.errors.push(path + ': ' + String(obj).slice(0, 40)); return; }
                    if (typeof obj.message === 'string') { out.errors.push(path + ': ' + obj.message.slice(0, 100)); return; }
                    for (const k of Object.keys(obj).slice(0, 60)) {
                        if (k === 'ref' || k.startsWith('_')) continue;
                        flatten(obj[k], path ? path + '.' + k : k, depth + 1);
                    }
                };
                const flagKeys = ['isValid', 'dirty', 'isDirty', 'isSubmitting', 'isValidating', 'submitCount', 'valid'];
                const inspect = (obj, where, depth) => {
                    if (!obj || typeof obj !== 'object' || depth > 2) return;
                    if (seen.has(obj)) return;
                    seen.add(obj);
                    if (obj.errors && typeof obj.errors === 'object' && !Array.isArray(obj.errors)) {
                        out.sources.push(where + '.errors keys=' + Object.keys(obj.errors).length);
                        flatten(obj.errors, '', 0);
                    }
                    for (const k of flagKeys) {
                        if (k in obj && ['boolean', 'number'].includes(typeof obj[k])) out.flags.push(where + '.' + k + '=' + obj[k]);
                    }
                    for (const k of ['formik', 'formState', '_formState', 'form', 'control']) {
                        if (obj[k] && typeof obj[k] === 'object') inspect(obj[k], where + '.' + k, depth + 1);
                    }
                };
                const typeName = (f) => (f.type && (f.type.displayName || f.type.name))
                    || (typeof f.type === 'string' ? f.type : '?');
                let fiber = start[key];
                for (let depth = 0; fiber && depth < 60; depth++, fiber = fiber.return) {
                    const name = depth + ':' + typeName(fiber);
                    const props = fiber.memoizedProps;
                    inspect(props, name + '.props', 0);
                    if (depth < 12 && props && typeof props === 'object') {
                        for (const k of Object.keys(props)) {
                            if (typeof props[k] === 'boolean'
                                    && /valid|disabl|error|dirty|submit|ready|complete|missing|required|allow/i.test(k))
                                out.props.push(name + '.' + k + '=' + props[k]);
                        }
                    }
                    let hook = fiber.memoizedState;
                    inspect(hook, name + '.state', 0);
                    for (let i = 0; hook && typeof hook === 'object' && 'next' in hook && i < 40; i++, hook = hook.next) {
                        inspect(hook.memoizedState, name + '.hook' + i, 0);
                    }
                    let context = fiber.dependencies && fiber.dependencies.firstContext;
                    for (let j = 0; context && j < 10; j++, context = context.next) {
                        inspect(context.memoizedValue, name + '.context' + j, 0);
                    }
                }
                const unique = (list, n) => Array.from(new Set(list)).slice(0, n);
                return {errors: unique(out.errors, 40), flags: unique(out.flags, 30),
                        props: unique(out.props, 30), sources: unique(out.sources, 10)};
            }""",
            ADD_ACCOUNT_MODAL_SELECTOR)
    except Exception as exc:
        log.error("react_form_state", stage or "report", exc)
        return
    if not isinstance(state, dict):
        log.event("react_form_state", "unreadable", stage)
        return
    for kind in ("sources", "errors", "flags", "props"):
        items = state.get(kind)
        for item in (items if isinstance(items, list) else [])[:40]:
            text = str(item)
            if kind == "errors" and ": " in text:
                # "field.path: message" - only the message can carry data.
                path, message = text.split(": ", 1)
                text = path + ": " + _redact_domains(message)
            # sources/flags/props are code identifiers (component.prop=bool).
            log.event("react_form_state", kind.rstrip("s"), stage, text)
    log.event("react_form_state", "reported", stage,
              "errors={} flags={}".format(len(state.get("errors") or []), len(state.get("flags") or [])))


def _log_react_component_keys(page: Any, stage: str = "") -> None:
    """Log the shape of the components above Confirm: names and booleans only.

    For the first ancestors of the Confirm button: each component's prop
    names (booleans with their value, other types as their type name) and
    each hook state's keys (booleans with value). Strings, numbers, and
    nested values are never returned. Used to spot an internal validity flag
    when the form library keeps no errors object.
    """
    log = _log()
    try:
        shapes = page.evaluate(
            """() => {
                const button = Array.from(document.querySelectorAll('button'))
                    .find(b => (b.textContent || '').trim() === 'Confirm');
                if (!button) return ['no-confirm-button'];
                const key = Object.keys(button).find(k => k.startsWith('__reactFiber$')
                    || k.startsWith('__reactInternalInstance$'));
                if (!key) return ['no-react-fiber'];
                const describe = (v) => typeof v === 'boolean' ? String(v)
                    : v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v;
                const shape = (obj, limit) => Object.keys(obj).slice(0, limit)
                    .map(k => k + ':' + describe(obj[k])).join(',');
                const out = [];
                let fiber = button[key];
                for (let depth = 0; fiber && depth < 18; depth++, fiber = fiber.return) {
                    if (typeof fiber.type === 'string') continue;  // host elements
                    const name = (fiber.type && (fiber.type.displayName || fiber.type.name)) || '?';
                    const props = fiber.memoizedProps;
                    if (props && typeof props === 'object')
                        out.push(depth + ':' + name + ' props{' + shape(props, 40) + '}');
                    let hook = fiber.memoizedState;
                    for (let i = 0; hook && typeof hook === 'object' && 'next' in hook && i < 40; i++, hook = hook.next) {
                        const v = hook.memoizedState;
                        if (typeof v === 'boolean') out.push(depth + ':' + name + ' hook' + i + '=' + v);
                        else if (v && typeof v === 'object' && !Array.isArray(v) && !('current' in v)
                                 && !('deps' in v) && Object.keys(v).length <= 60)
                            out.push(depth + ':' + name + ' hook' + i + '{' + shape(v, 60) + '}');
                    }
                    if (out.length >= 60) break;
                }
                return out.slice(0, 60);
            }""")
    except Exception as exc:
        log.error("react_component_keys", stage or "report", exc)
        return
    for item in (shapes if isinstance(shapes, list) else [])[:60]:
        log.event("react_component_keys", "shape", stage, str(item))


def _log_form_field_errors(page: Any, stage: str = "") -> None:
    """Log the Add Account component's own per-field error slots, without values.

    Live 2026-09-30: the Add Account component keeps the whole form in one
    hook state object with a "<field>Error" key beside each field. For every
    such object above the Confirm button this logs (a) each error slot that
    is set, with its message (domain-redacted), and (b) the names of fields
    whose value is empty (never the values themselves). One event per item
    so nothing is truncated.
    """
    log = _log()
    try:
        found = page.evaluate(
            """() => {
                const button = Array.from(document.querySelectorAll('button'))
                    .find(b => (b.textContent || '').trim() === 'Confirm');
                if (!button) return {note: 'no-confirm-button'};
                const key = Object.keys(button).find(k => k.startsWith('__reactFiber$')
                    || k.startsWith('__reactInternalInstance$'));
                if (!key) return {note: 'no-react-fiber'};
                const isEmpty = (v) => v === undefined || v === null || v === ''
                    || (Array.isArray(v) && v.length === 0);
                const states = [];
                let fiber = button[key];
                for (let depth = 0; fiber && depth < 30; depth++, fiber = fiber.return) {
                    let hook = fiber.memoizedState;
                    for (let i = 0; hook && typeof hook === 'object' && 'next' in hook && i < 60; i++, hook = hook.next) {
                        const v = hook.memoizedState;
                        if (!v || typeof v !== 'object' || Array.isArray(v)) continue;
                        const keys = Object.keys(v);
                        const errorKeys = keys.filter(k => /Error$/.test(k));
                        if (!errorKeys.length) continue;
                        const set = errorKeys.filter(k => !isEmpty(v[k]) && v[k] !== false).map(k => {
                            const e = v[k];
                            const msg = typeof e === 'string' ? e
                                : (e && typeof e.message === 'string') ? e.message
                                : (typeof e === 'boolean' ? String(e) : typeof e);
                            return k + ': ' + String(msg).slice(0, 120);
                        });
                        const empty = keys.filter(k => !/Error$/.test(k) && isEmpty(v[k]));
                        states.push({where: depth + ':hook' + i, fields: keys.length - errorKeys.length,
                                     errorSlots: errorKeys.length, set: set, empty: empty.slice(0, 80)});
                    }
                }
                return {states: states.slice(0, 5)};
            }""")
    except Exception as exc:
        log.error("form_field_errors", stage or "report", exc)
        return
    if not isinstance(found, dict):
        log.event("form_field_errors", "unreadable", stage)
        return
    if found.get("note"):
        log.event("form_field_errors", "unavailable", stage, str(found["note"]))
        return
    states = found.get("states") if isinstance(found.get("states"), list) else []
    if not states:
        log.event("form_field_errors", "no_error_state", stage)
    for state in states:
        if not isinstance(state, dict):
            continue
        where = str(state.get("where", ""))
        log.event("form_field_errors", "state", stage,
                  f"{where} fields={state.get('fields')} error_slots={state.get('errorSlots')} "
                  f"set={len(state.get('set') or [])} empty={len(state.get('empty') or [])}")
        for item in state.get("set") or []:
            text = str(item)
            name, _sep, message = text.partition(": ")
            log.event("form_field_errors", "error_set", stage, f"{where} {name}: {_redact_domains(message)}")
        for name in state.get("empty") or []:
            log.event("form_field_errors", "empty_field", stage, f"{where} {name}")


def _diagnose_alternate_domain_searches(page: Any, search: Any, source: Any, tenant_name: str) -> None:
    """Read-only tenant search for each alternate domain (diagnose only).

    Logs, per alternate domain by position (never the value), the server's
    row count and whether a row matches that domain exactly. It never blocks
    the run: the main duplicate check is unchanged.
    """
    log = _log()
    for index, domain in enumerate(getattr(source, "alternate_domains", ()) or (), start=1):
        name = f"alternate_{index}"
        searched = _search_tenants(page, search, domain)
        if searched is None:
            log.event("diagnose_alt_domain_search", "unavailable", name)
            continue
        verdict = _api_duplicate(searched, tenant_name, domain)
        log.event("diagnose_alt_domain_search", verdict, name, f"rows={len(searched.rows)} total={searched.total_count}")


def _diagnose_cumulative(page: Any, confirm: Any, plan: dict[str, Any]) -> list[str]:
    """Apply CE-like changes in cumulative groups (never Leaked Credentials or
    Phishing), checking Confirm after each group. Not undone: the form is
    cancelled right after. Returns the group names that left Confirm enabled."""
    log = _log()
    texts = plan.get("texts", {})
    enabled_after: list[str] = []

    def group(name: str, steps: list[Any]) -> None:
        applied = 0
        for step in steps:
            try:
                applied += 1 if step() else 0
            except Exception as exc:
                log.error("diagnose_group", name, exc)
        enabled = _confirm_is_enabled_now(page, confirm)
        log.event("diagnose_group", "enables_confirm" if enabled else "no_change", name, f"applied={applied}/{len(steps)}")
        if enabled:
            enabled_after.append(name)

    numbers = [lambda label=label: _set_text_value(page, label, "1")
               for label in ("Number of assets", "Number of domains", "Number of subdomains") if label in texts]
    if SURFACE_MAX_SCAN_DURATION_LABEL in plan.get("advanced_texts", {}):
        numbers.append(lambda: _set_text_value(page, SURFACE_MAX_SCAN_DURATION_LABEL, "24", advanced=True))
    group("numbers_ce_like", numbers)
    keep_on = {"mfaRequired", "provisioningEnabled", "subDomainsNumberAllowed"}  # ON in the CE plan too
    group("surface_toggles_off", [lambda key=key: _set_checkbox(page, key, False)
                                  for key, target in plan.get("checkboxes", {}).items()
                                  if target is True and key not in keep_on and key not in DIAGNOSE_NEVER_ENABLE])
    if ALTERNATE_DOMAINS_LABEL in texts:
        group("alternate_domains_blank", [lambda: _set_text_value(page, ALTERNATE_DOMAINS_LABEL, "")])
    if plan.get("selects", {}).get("Scanning interval") not in (None, "None"):
        group("interval_none_scan_now_off", [lambda: _fill_select(page, "Scanning interval", "None") is None,
                                             lambda: _set_checkbox(page, "scan_now", False)])
    log.event("diagnose_cumulative", "enabled" if enabled_after else "still_disabled", detail=",".join(enabled_after))
    return enabled_after


def _confirm_is_enabled_now(page: Any, confirm: Any) -> bool:
    page.wait_for_timeout(DIAGNOSE_SETTLE_MS)
    try:
        return bool(confirm.first.is_enabled())
    except Exception:
        return False


def _set_text_value(page: Any, label: str, value: str, *, advanced: bool = False) -> bool:
    control = _locate_advanced_text(page, label) if advanced else _locate_form_field(page, label, "")
    if control is None:
        return False
    try:
        control.first.fill(value, timeout=FIELD_TIMEOUT_MS)
        return control.first.input_value() == value
    except Exception:
        return False


def _touch_form_fields(page: Any) -> bool:
    """Focus and blur each editable control once (the form's own touched validation).

    Read-only date inputs are skipped so no picker opens; nothing is typed.
    """
    try:
        page.evaluate(
            """(modalSelector) => {
                const root = document.querySelector(modalSelector) || document;
                for (const el of root.querySelectorAll('input:not([type=hidden]):not([readonly]), select, textarea')) {
                    try { el.focus(); el.blur(); } catch (e) {}
                }
            }""",
            ADD_ACCOUNT_MODAL_SELECTOR)
        return True
    except Exception:
        return False


LC_SCANNED_DOMAINS_LABEL = "Leaked Credentials scanned domains (Comma Separated Values)"


def _prefill_dormant_lc_domains(page: Any, domain: str, step: str = "lc_domains_prefill") -> bool:
    """Set the dormant Leaked Credentials domain while keeping LC OFF.

    Live 2026-09-30 (CO-0649): Leonardo's Confirm check requires
    leakedCredentialsScannedDomains even with Leaked Credentials OFF, and the
    field is disabled while LC is OFF. The toggle is switched ON only inside
    the unsaved form to make the field editable, the primary domain is
    entered, and the toggle is switched back OFF and re-verified OFF. Returns
    True only when LC is OFF again and the field kept the domain; whatever
    happens, it tries to leave LC OFF.
    """
    log = _log()
    ok = False
    try:
        if not _set_checkbox(page, "leakedCredentialsAllowed", True):
            log.event(step, "lc_toggle_unavailable")
            return False
        filled = _set_text_value(page, LC_SCANNED_DOMAINS_LABEL, domain)
        log.event(step, "domain_set" if filled else "domain_not_set")
        ok = filled
    except Exception as exc:
        log.error(step, "fill", exc)
    finally:
        off = _set_checkbox(page, "leakedCredentialsAllowed", False)
        control = _locate_checkbox(page, "leakedCredentialsAllowed")
        try:
            off = off and control is not None and not control.first.is_checked()
        except Exception:
            off = False
        log.event(step, "lc_off_verified" if off else "lc_off_failed")
    kept = _text_control_value(page, LC_SCANNED_DOMAINS_LABEL) == domain
    log.event(step, "domain_kept" if kept else "domain_lost")
    return ok and off and kept


def _diagnose_confirm(page: Any, confirm: Any, plan: dict[str, Any], lc_prefill_probe: bool = False) -> str:
    """No-submit probes for a disabled Confirm: try one change at a time.

    Each probe changes one control, checks whether Confirm enables, and puts
    the planned value back before the next probe; the runner then cancels the
    form. Confirm is never clicked. Probe names and outcomes are logged (no
    values). Returns diagnose_confirm_blocker_found when some single change
    enabled Confirm, else diagnose_confirm_blocker_unknown.
    """
    log = _log()
    texts = plan.get("texts", {})
    advanced = plan.get("advanced_texts", {})
    selects = plan.get("selects", {})
    found: list[str] = []

    def probe(name: str, apply: Any, undo: Any) -> None:
        try:
            applied = apply()
        except Exception as exc:
            log.error("diagnose_probe", name, exc)
            applied = False
        if not applied:
            log.event("diagnose_probe", "skipped", name)
            return
        enabled = _confirm_is_enabled_now(page, confirm)
        log.event("diagnose_probe", "enables_confirm" if enabled else "no_change", name)
        if enabled:
            found.append(name)
        try:
            restored = undo()
        except Exception as exc:
            log.error("diagnose_probe", name + ":undo", exc)
            restored = False
        if not restored:
            log.event("diagnose_probe", "undo_failed", name)

    _log_form_field_errors(page)
    _log_react_form_state(page)
    _log_react_component_keys(page)
    if lc_prefill_probe:
        # Owner-approved (2026-09-30, option A) single no-submit probe: the
        # dormant LC domain is set while LC ends OFF; nothing is submitted.
        primary = texts.get("Company primary domain", "")
        prefilled = bool(primary) and _prefill_dormant_lc_domains(page, primary, "diagnose_lc_prefill")
        enabled = prefilled and _confirm_is_enabled_now(page, confirm)
        log.event("diagnose_probe", "enables_confirm" if enabled else ("no_change" if prefilled else "skipped"),
                  "lc_domains_prefill_lc_off")
        _log_form_field_errors(page, "after_lc_prefill")
        if enabled:
            log.event("diagnose_confirm", "found", detail="lc_domains_prefill_lc_off")
            return "diagnose_confirm_blocker_found"
    # 1. Touch every field: surfaces the form's own per-field errors, if any.
    touched = _touch_form_fields(page)
    enabled = touched and _confirm_is_enabled_now(page, confirm)
    log.event("diagnose_probe", "enables_confirm" if enabled else ("no_change" if touched else "skipped"), "touch_fields")
    if enabled:
        found.append("touch_fields")
    _log_form_validation(page, "after_touch")
    duration_label = SURFACE_MAX_SCAN_DURATION_LABEL
    if duration_label in advanced:
        probe("max_scan_duration_24",
              lambda: _set_text_value(page, duration_label, "24", advanced=True),
              lambda: _set_text_value(page, duration_label, advanced[duration_label], advanced=True))
    alternates = texts.get(ALTERNATE_DOMAINS_LABEL)
    if alternates:
        compact = ",".join(part.strip() for part in alternates.split(","))
        if compact != alternates:
            probe("alternate_domains_no_spaces",
                  lambda: _set_text_value(page, ALTERNATE_DOMAINS_LABEL, compact),
                  lambda: _set_text_value(page, ALTERNATE_DOMAINS_LABEL, alternates))
        probe("alternate_domains_blank",
              lambda: _set_text_value(page, ALTERNATE_DOMAINS_LABEL, ""),
              lambda: _set_text_value(page, ALTERNATE_DOMAINS_LABEL, alternates))
    subdomains = texts.get("Number of subdomains")
    if subdomains and subdomains != "50000":
        probe("number_of_subdomains_50000",
              lambda: _set_text_value(page, "Number of subdomains", "50000"),
              lambda: _set_text_value(page, "Number of subdomains", subdomains))
    domains = texts.get("Number of domains")
    if domains and domains != "1":
        probe("number_of_domains_1",
              lambda: _set_text_value(page, "Number of domains", "1"),
              lambda: _set_text_value(page, "Number of domains", domains))
    assets = texts.get("Number of assets")
    if assets and assets != "1":
        probe("number_of_assets_1",
              lambda: _set_text_value(page, "Number of assets", "1"),
              lambda: _set_text_value(page, "Number of assets", assets))
    company = texts.get("Company name")
    if company:
        probe("company_name_plain",
              lambda: _set_text_value(page, "Company name", "Diagnostic Probe Company"),
              lambda: _set_text_value(page, "Company name", company))
    email = texts.get("Organization Email")
    plain_email = f"{CE_PRIMARY_USER_EMAIL_LOCAL}@{CE_USER_EMAIL_DOMAIN}"
    if email and email != plain_email:
        probe("organization_email_plain",
              lambda: _set_text_value(page, "Organization Email", plain_email),
              lambda: _set_text_value(page, "Organization Email", email))
    # Each planned-ON toggle turned OFF, one at a time. Leaked Credentials and
    # Phishing are never turned ON (owner decision: no CE on a Surface-only tenant).
    for key, target in plan.get("checkboxes", {}).items():
        if target is True and key not in DIAGNOSE_NEVER_ENABLE:
            probe(f"toggle_off:{key}",
                  lambda key=key: _set_checkbox(page, key, False),
                  lambda key=key: _set_checkbox(page, key, True))
    # Last: switching the interval can reset dependent controls; the form is
    # cancelled right after, so no later probe depends on it.
    interval = selects.get("Scanning interval")
    if interval and interval != "None":
        probe("scanning_interval_none",
              lambda: _fill_select(page, "Scanning interval", "None") is None,
              lambda: _fill_select(page, "Scanning interval", interval) is None)
    log.event("diagnose_confirm", "found" if found else "unknown", detail=",".join(found))
    _log_form_field_errors(page, "after_probes")
    if found:
        return "diagnose_confirm_blocker_found"
    # No single change helped: try cumulative CE-like groups (last; the form
    # is cancelled right after, so nothing is undone).
    if _diagnose_cumulative(page, confirm, plan):
        return "diagnose_confirm_blocker_combined"
    return "diagnose_confirm_blocker_unknown"


def _fill_select(page: Any, label: str, option: str) -> str | None:
    """Select one option by its visible label; return a failure code or None."""
    log = _log()
    control = _locate_select(page, label)
    if control is None:
        log.event("fill_select", "not_found", label)
        return "fill_form_schema_unavailable"
    try:
        if not control.first.is_enabled():
            log.event("fill_select", "disabled", label)
            return "fill_form_schema_unavailable"
        control.first.select_option(label=option, timeout=FIELD_TIMEOUT_MS)
        selected = _selected_option_text(control.first)
    except Exception as exc:
        log.error("fill_select", label, exc)
        return "fill_form_schema_unavailable"
    if selected != option:
        log.event("fill_select", "mismatch", label)
        return "fill_value_mismatch"
    log.event("fill_select", "ok", label)
    return None


def _search_tenants(page: Any, search: Any, lookup: str) -> "TenantSearchResult | None":
    """Run one server-side tenant search and wait for its own response.

    The tenant search is server-side (a getAllDetailedAccounts POST per edit,
    HAR-verified 2026-09-29), so a fixed sleep can read the previous lookup's
    table and false-clear. The box is cleared first so re-searching the same
    text still issues a request, and only a response whose request carries
    this lookup counts. Returns the parsed rows, or None (the caller fails
    closed) when no such response arrives in time or its schema is unexpected.
    """
    needle = lookup.casefold()

    def matches(response: Any) -> bool:
        try:
            if TENANT_SEARCH_API not in response.url:
                return False
            body = response.request.post_data or ""
            return needle in body.casefold()
        except Exception:
            return False

    try:
        search.fill("", timeout=FIELD_TIMEOUT_MS)
        with page.expect_response(matches, timeout=SEARCH_RESPONSE_TIMEOUT_MS) as info:
            search.fill(lookup, timeout=FIELD_TIMEOUT_MS)
            search.press("Enter")
        status = info.value.status
    except Exception as exc:
        _log().error("tenant_search", "response", exc)
        return None
    if status in (401, 403):
        # The tab can still show Tenant Management while its API token has
        # expired; say so instead of reporting a table/schema problem.
        _log().event("tenant_search", str(status), detail="leonardo session expired")
        raise LeonardoSessionExpired()
    if not 200 <= status < 300:
        _log().event("tenant_search", str(status))
        return None
    try:
        body = info.value.json()
        paged = body.get("pagination_response") if isinstance(body, dict) else None
        rows = paged.get("table_data") if isinstance(paged, dict) else None
        total = paged.get("total_count") if isinstance(paged, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("table_data")
        if not isinstance(total, int):
            raise ValueError("total_count")
    except Exception as exc:
        _log().error("tenant_search", "response_schema", exc)
        return None
    _log().event("tenant_search", str(status), detail=f"rows={len(rows)} total={total}")
    return TenantSearchResult(rows, total)


class LeonardoSessionExpired(RuntimeError):
    """The Leonardo API rejected the session (401/403); nothing was changed.

    A RuntimeError so every runner mode reports it as its result code.
    """

    def __init__(self) -> None:
        super().__init__("leonardo_session_expired")


@dataclass(frozen=True, slots=True)
class TenantSearchResult:
    """The server's rows for one tenant search (in memory only; never persisted)."""
    rows: list[dict[str, Any]]
    total_count: int


def _api_duplicate(result: TenantSearchResult, expected_name: str, expected_domain: str) -> str:
    """Classify the server search response (second, independent duplicate check).

    Any exact company-name or primary-domain match is a duplicate. A response
    that reports more matches than it returned cannot be fully checked and is
    ambiguous.
    """
    if result.total_count > len(result.rows):
        return "duplicate_ambiguous"
    name = " ".join(expected_name.casefold().split())
    for row in result.rows:
        row_name = row.get("accountName")
        row_domain = row.get("accountDomain")
        if isinstance(row_name, str) and " ".join(row_name.casefold().split()) == name:
            return "duplicate_found"
        if isinstance(row_domain, str) and row_domain.casefold().strip().rstrip(".") == expected_domain:
            return "duplicate_found"
    return "duplicate_clear"


def _api_readback(result: TenantSearchResult, tenant_name: str,
                  expected_domain: str | None, *, allow_scan_started: bool = False) -> tuple[str, str, str] | None:
    """Read Surface Account ID, Account UUID and scan state from the search response.

    Requires exactly one row whose accountName equals the tenant name (and,
    when given, whose accountDomain equals the expected domain), with a
    well-formed id and accountUuid. A tenant that has never scanned
    (lastReconScan null) is "No scan started". A tenant with a scan state
    returns None so the caller falls back to the details view, unless
    ``allow_scan_started`` (the Surface route, whose contract turns Scan now
    ON, so the tenant can be scanning right after Confirm): then any set
    lastReconScan is "Account Scanning".
    """
    name = " ".join(tenant_name.casefold().split())
    exact = [row for row in result.rows
             if isinstance(row.get("accountName"), str)
             and " ".join(row["accountName"].casefold().split()) == name]
    if len(exact) != 1:
        return None
    row = exact[0]
    if expected_domain is not None:
        domain = row.get("accountDomain")
        if not isinstance(domain, str) or domain.casefold().strip().rstrip(".") != expected_domain:
            return None
    surface_account_id, account_uuid = row.get("id"), row.get("accountUuid")
    if not (isinstance(surface_account_id, str) and re.fullmatch(SURFACE_ACCOUNT_ID_PATTERN, surface_account_id)
            and isinstance(account_uuid, str) and re.fullmatch(ACCOUNT_UUID_PATTERN, account_uuid)):
        return None
    if row.get("lastReconScan") is not None:
        if allow_scan_started:
            return surface_account_id, account_uuid, "Account Scanning"
        return None
    return surface_account_id, account_uuid, "No scan started"


def run(reference: str, acknowledged_revision: str, *, review_wait_seconds: float = MAX_WAIT_SECONDS,
        dry_run: bool = False, route: str = CE_ENGINE, diagnose: bool = False,
        lc_prefill_probe: bool = False) -> str:
    """Run one attended auto-confirm session.  Never writes back to Salesforce.

    ``route`` selects the route contract (ROUTES); the default is the CE-only
    route so existing launches are unchanged. An unknown route fails closed
    before any read. ``dry_run`` runs every check and fills and re-verifies
    the full form, requires Confirm to be enabled (never clicking it), then
    cancels. It creates nothing and never records to the one-time create gate
    (only the run log is written). ``diagnose`` implies ``dry_run`` and, when
    Confirm stays disabled, runs the no-submit probes (_diagnose_confirm).
    """
    global _ACTIVE_RUN_LOG, _ENTERED_LICENSE
    dry_run = dry_run or diagnose
    _ENTERED_LICENSE = None
    _ACTIVE_RUN_LOG = RunLog(reference, DRY_RUN_MODE if dry_run else "create", route=route)
    _ACTIVE_RUN_LOG.timeline = diagnose
    try:
        contract = ROUTES.get(route)
        if contract is None:
            return _finish(reference, acknowledged_revision, "route_unsupported")
        try:
            return _run(reference, acknowledged_revision, review_wait_seconds=review_wait_seconds, dry_run=dry_run,
                        contract=contract, diagnose=diagnose, lc_prefill_probe=diagnose and lc_prefill_probe)
        except Exception as error:  # noqa: BLE001 - the record must never stay "Running"
            try:
                _ACTIVE_RUN_LOG.error("run", "crashed", error)
            except Exception:  # noqa: BLE001
                pass
            return _finish(reference, acknowledged_revision, "runner_crashed")
    finally:
        _ACTIVE_RUN_LOG = None


def _cancel_add_account(page: Any) -> bool:
    """Cancel the open Add Account form (its own Cancel, never a picker's)."""
    try:
        cancel = page.locator(ADD_ACCOUNT_MODAL_SELECTOR).get_by_role("button", name="Cancel", exact=True)
        if cancel.count() != 1:
            _log().event("dry_run_cancel", "cancel_unavailable", detail=f"count={cancel.count()}")
            return False
        cancel.first.click(timeout=FIELD_TIMEOUT_MS)
        for _poll in range(20):
            if page.get_by_label("Company name", exact=True).count() == 0:
                return True
            page.wait_for_timeout(100)
    except Exception as exc:
        _log().error("dry_run_cancel", "Cancel", exc)
    return False


def _run(reference: str, acknowledged_revision: str, *, review_wait_seconds: float, dry_run: bool = False,
         contract: RouteContract = CE_ROUTE, diagnose: bool = False, lc_prefill_probe: bool = False) -> str:
    global _ENTERED_LICENSE
    log = _log()
    log.event("source_read", "start")
    try:
        source = contract.load_source(reference)
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return _finish(reference, acknowledged_revision, "playwright_runtime_unavailable")
    except RuntimeError as error:
        return _finish(reference, acknowledged_revision, str(error))
    log.add_redactions(*contract.redactions(source))
    log.event("source_read", "ok")
    # Owner decision 2026-10-01: a CO whose mapped Salesforce ID field is already
    # set was onboarded before (usually in production). Never create or dry-run it.
    if source.salesforce_id_present:
        return _finish(reference, acknowledged_revision, "salesforce_id_already_present")
    if source.source_revision != acknowledged_revision:
        return _finish(reference, acknowledged_revision, "source_revision_drift")
    try:
        # Validate the license dates before any browser work.
        license_start, license_end = contract.license_dates(source)
    except ValueError as error:
        return _finish(reference, acknowledged_revision, str(error))
    log.event("license_dates", "ok", detail=f"start={license_start.isoformat()} end={license_end.isoformat()}")
    # Duplicate pre-check against the latest DEV tenant inventory, before any browser
    # work (owner decision 2026-10-04). It can only stop a run; the live check below
    # still decides every run it lets through.
    if _inventory_precheck(contract, source, log)["result"] == "inventory_match":
        return _finish(reference, acknowledged_revision, "duplicate_inventory_match")
    tenant_name, main_domain = source.tenant_name, contract.primary_domain(source)
    with sync_playwright() as playwright:
        try:
            # The login wait happens inside _attach_attended_browser (CDP /json
            # polling) and attaches only after the operator reaches
            # tenant-management, so `page` is a fresh, valid reference. When the
            # persisted session is still valid this skips SSO/MFA entirely.
            log.event("browser_attach", "start")
            with _attended_page(playwright) as page:
                log.attach(page)
                if log.timeline:
                    log.timeline_page = page
                log.event("browser_attach", "ok")
                search = _open_search(page)
                if search is None:
                    _capture_search_diagnostics(page)
                    return _finish(reference, acknowledged_revision, "duplicate_search_schema_unavailable")
                if diagnose:
                    _diagnose_alternate_domain_searches(page, search, source, tenant_name)
                for lookup_name, lookup in contract.duplicate_lookups(source):
                    lookup_domain = contract.lookup_domain(source, lookup_name)
                    searched = _search_tenants(page, search, lookup)
                    if searched is None:
                        duplicate = "duplicate_schema_unavailable"
                    else:
                        duplicate = _settled_tenant_rows(page, tenant_name, lookup_domain)
                        if duplicate == "duplicate_clear":
                            # Independent check against the server's own rows.
                            duplicate = _api_duplicate(searched, tenant_name, lookup_domain)
                    log.event("duplicate_check", duplicate, lookup_name)
                    if duplicate != "duplicate_clear":
                        if duplicate in ("duplicate_schema_unavailable", "duplicate_ambiguous"):
                            _capture_search_diagnostics(page)
                        return _finish(reference, acknowledged_revision, duplicate)
                if contract.load_source(reference) != source:
                    return _finish(reference, acknowledged_revision, "source_revision_drift")
                add_account = page.get_by_role("button", name="Add Account", exact=True)
                if add_account.count() != 1:
                    _capture_search_diagnostics(page)
                    return _finish(reference, acknowledged_revision, "add_account_schema_unavailable")
                add_account.click()
                log.event("add_account_open", "clicked")
                # Give the Add Account form a bounded moment to render before the
                # first field lookup. This is defensive only: the lookup below
                # still fails closed if the form (or a field) is not present.
                try:
                    page.get_by_label("Company name", exact=True).first.wait_for(state="attached", timeout=5_000)
                except Exception:
                    pass
                # Fill the full CE-only contract. Every control must resolve to
                # exactly one element and keep the value the runner set
                # (re-read verification); any ambiguity or mismatch stops the
                # runner before the form is submitted.
                plan = contract.build_fill(source, license_start)
                failure, account_name_control = _fill_add_account_form(page, plan)
                if failure is not None:
                    _capture_search_diagnostics(page)
                    if dry_run:
                        _cancel_add_account(page)
                    return _finish(reference, acknowledged_revision, failure)
                if dry_run:
                    # Everything up to Confirm passed. A dry run also requires
                    # Confirm to be enabled (it is never clicked), then cancels.
                    # With diagnose, a disabled Confirm runs the no-submit probes.
                    confirm = _locate_confirm_button(page)
                    if confirm is None:
                        _capture_search_diagnostics(page)
                        result = "confirm_button_schema_unavailable"
                    elif _ensure_confirm_enabled(page, confirm, plan):
                        log.event("confirm_enable", "enabled")
                        result = "dry_run_fill_verified"
                    elif diagnose:
                        result = _diagnose_confirm(page, confirm, plan, lc_prefill_probe=lc_prefill_probe)
                    else:
                        _capture_search_diagnostics(page)
                        result = "dry_run_confirm_not_enabled"
                    cancelled = _cancel_add_account(page)
                    return _finish(reference, acknowledged_revision,
                                   result if cancelled else "dry_run_cancel_unavailable")
                # Auto-confirm: the operator triggered this attended run and the
                # source passed a clear duplicate check and a validated fill, so
                # the runner submits the form by clicking the single Confirm
                # control. It clicks exactly once and never retries an uncertain
                # submit; if the form does not close it fails closed without
                # re-clicking.
                confirm = _locate_confirm_button(page)
                if confirm is None:
                    _capture_search_diagnostics(page)
                    return _finish(reference, acknowledged_revision, "confirm_button_schema_unavailable")
                if not _ensure_confirm_enabled(page, confirm, plan):
                    _capture_search_diagnostics(page)
                    return _finish(reference, acknowledged_revision, "confirm_button_not_enabled")
                _ENTERED_LICENSE = (plan["license_start"], plan["license_end"])
                create_status = None
                try:
                    with page.expect_response(lambda r: ACCOUNT_ADD_API in r.url, timeout=30_000) as created:
                        confirm.first.click(timeout=5_000)
                    create_status = created.value.status
                except Exception as exc:
                    # Uncertain: the click may or may not have submitted. Never
                    # re-click; the read-only form-close/search below reconciles.
                    log.error("confirm_click", "Confirm", exc)
                log.event("confirm_click", "clicked", detail=f"account_add_status={create_status}")
                # Wait for the Add Account form to close (submission) within a
                # bounded window. A lookup error is "unknown", not "closed".
                deadline = monotonic() + min(review_wait_seconds, 60.0)
                form_closed = False
                while monotonic() < deadline:
                    try:
                        if account_name_control.count() == 0:
                            form_closed = True
                            break
                    except Exception:
                        pass
                    sleep(FORM_CLOSE_POLL_SECONDS)
                log.event("form_close_wait", "closed" if form_closed else "still_open")
                created_ok = isinstance(create_status, int) and 200 <= create_status < 300
                if not form_closed and not created_ok:
                    _capture_search_diagnostics(page)
                    return _finish(reference, acknowledged_revision, "confirm_no_create")
                # Re-search (read-only) for the created tenant. A short bounded
                # retry lets an in-progress creation settle; it never re-clicks
                # Confirm, so an uncertain mutation is never repeated.
                search = _open_search(page)
                if search is None:
                    return _finish(reference, acknowledged_revision, "duplicate_search_schema_unavailable")
                duplicate = "duplicate_clear"
                for attempt in range(3):
                    if attempt:
                        page.wait_for_timeout(2_000)
                    # Clear-and-refill so each retry issues a fresh server search.
                    searched = _search_tenants(page, search, tenant_name)
                    if searched is None:
                        duplicate = "duplicate_schema_unavailable"
                        continue
                    duplicate = _settled_tenant_rows(page, tenant_name, main_domain)
                    log.event("post_create_search", duplicate, f"attempt={attempt + 1}")
                    if duplicate == "duplicate_found":
                        break
                if duplicate != "duplicate_found":
                    if duplicate == "duplicate_schema_unavailable":
                        _capture_search_diagnostics(page)
                    return _finish(reference, acknowledged_revision,
                                   "confirm_no_create" if duplicate == "duplicate_clear" else duplicate)
                # The CE-only contract disables scanning and "Scan now", so a
                # freshly created tenant has no scan state yet; record it as
                # "No scan started" (same rule as the readback-only mode). The
                # Surface contract turns Scan now ON, so its tenant may already
                # be "Account Scanning" (allow_scan_started).
                # Primary: the server's own search row; fallback: details view.
                details = (_api_readback(searched, tenant_name, main_domain,
                                         allow_scan_started=contract.allow_scan_started)
                           if searched is not None else None)
                log.event("readback", "api" if details else "api_unavailable")
                if details is None:
                    details, failure = _details_fallback(page, searched, tenant_name, main_domain)
                    if failure is not None:
                        return _finish(reference, acknowledged_revision, failure)
                surface_account_id, account_uuid, state = details
                observed_state = state if state else "No scan started"
                if not (re.fullmatch(SURFACE_ACCOUNT_ID_PATTERN, surface_account_id)
                        and re.fullmatch(ACCOUNT_UUID_PATTERN, account_uuid)
                        and observed_state in READBACK_STATES):
                    return _finish(reference, acknowledged_revision, "readback_value_mismatch")
                try:
                    write_readback_evidence(reference, surface_account_id, account_uuid,
                                            date.today().isoformat(), observed_state)
                except ReadbackConflict:
                    return _finish(reference, acknowledged_revision, "readback_id_conflict")
                except (OSError, ValueError):
                    return _finish(reference, acknowledged_revision, "readback_write_unavailable")
                return _finish(reference, acknowledged_revision, "readback_verified")
        except LoginTimeout:
            return _finish(reference, acknowledged_revision, "development_login_timeout")
        except RuntimeError as error:
            # Preserve the specific browser/profile/CDP failure codes raised by
            # _attach_attended_browser instead of collapsing them.
            return _finish(reference, acknowledged_revision, str(error))
        except Exception as exc:
            log.error("runner", "unexpected", exc)
            return _finish(reference, acknowledged_revision, "attended_ce_runner_unavailable")


def run_readback(reference: str, tenant_name_override: str | None = None, route: str = CE_ENGINE) -> str:
    """Read-only verification of an existing Leonardo Development tenant.

    Searches for the exact tenant name, requires exactly one exact row, reads
    the Surface Account ID, Account UUID, and observed Account Scanning state
    from the details page, and records the readback evidence (an empty state
    is recorded as "No scan started"). It never fills, confirms, creates, or
    updates anything, and it does not consume the attended create gate or
    touch the runner state file.

    ``tenant_name_override`` targets a live tenant whose name intentionally
    deviates from the contract name (for example a dev tenant created with a
    " test" suffix). It replaces the computed tenant name for the search, the
    row classification, and the details lookup; the email domain is still
    taken from the Salesforce source. ``route`` selects the route contract
    (default CE-only); the Surface route accepts a tenant that is scanning.
    """
    contract = ROUTES.get(route)
    if contract is None:
        return "route_unsupported"
    try:
        source = contract.load_source(reference)
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    except RuntimeError as error:
        return str(error)
    primary_domain = contract.primary_domain(source)
    tenant_name = tenant_name_override.strip() if tenant_name_override and tenant_name_override.strip() else source.tenant_name
    with sync_playwright() as playwright:
        try:
            with _attended_page(playwright) as page:
                search = _open_search(page)
                if search is None:
                    _capture_search_diagnostics(page)
                    return "duplicate_search_schema_unavailable"
                searched = _search_tenants(page, search, tenant_name)
                if searched is not None:
                    classification = _settled_tenant_rows(page, tenant_name, primary_domain)
                else:
                    classification = "duplicate_schema_unavailable"
                if classification == "duplicate_clear":
                    return "readback_only_tenant_not_found"
                if classification != "duplicate_found":
                    _capture_search_diagnostics(page)
                    return classification
                # Primary: the server's own search row. An overridden (deviating)
                # tenant name may carry a deviating domain too, so the domain is
                # only enforced for the contract name. Fallback: details view.
                expected_domain = None if tenant_name != source.tenant_name else primary_domain
                details = _api_readback(searched, tenant_name, expected_domain,
                                        allow_scan_started=contract.allow_scan_started)
                if details is None:
                    details, failure = _details_fallback(page, searched, tenant_name, expected_domain)
                    if failure is not None:
                        return failure
                surface_account_id, account_uuid, state = details
                if not (re.fullmatch(SURFACE_ACCOUNT_ID_PATTERN, surface_account_id)
                        and re.fullmatch(ACCOUNT_UUID_PATTERN, account_uuid)):
                    return "readback_value_mismatch"
                observed_state = state if state else "No scan started"
                if observed_state not in READBACK_STATES:
                    return "readback_only_state_unrecognized"
                try:
                    write_readback_evidence(reference, surface_account_id, account_uuid,
                                            date.today().isoformat(), state=observed_state)
                except ReadbackConflict:
                    return "readback_id_conflict"
                except (OSError, ValueError):
                    return "readback_write_unavailable"
                return "readback_only_verified"
        except LoginTimeout:
            return "development_login_timeout"
        except RuntimeError as error:
            return str(error)
        except Exception:
            return "attended_ce_runner_unavailable"


def _scan_time(value: Any) -> str | None:
    """Normalize lastReconScan (ISO text or epoch milliseconds) to ISO seconds, else None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="seconds")
        if isinstance(value, str) and value.strip():
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None
    return None


def scan_status_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Derive a minimal, value-checked scan observation from one tenant search row.

    state is "no_scan", "scan_started", "scan_completed", "scan_failed", or
    "unrecognized". Only statuses in SCAN_STATUS_COMPLETED/FAILED (confirmed
    from a live read) are interpreted; any other set status is "scan_started"
    with the raw value kept for review. A malformed value is "unrecognized".
    """
    raw_last, raw_status = row.get("lastReconScan"), row.get("lastScanStatusEnum")
    raw_duration = row.get("lastReconScanDurationMilliseconds")
    last = _scan_time(raw_last)
    status = raw_status if isinstance(raw_status, str) and re.fullmatch(SCAN_STATUS_ENUM_PATTERN, raw_status) else None
    duration = (raw_duration if isinstance(raw_duration, int) and not isinstance(raw_duration, bool)
                and raw_duration >= 0 else None)
    observation: dict[str, Any] = {"last_recon_scan": last, "status_enum": status, "duration_ms": duration}
    if (raw_last is not None and last is None) or (raw_status is not None and status is None):
        observation["state"] = "unrecognized"
    elif status in SCAN_STATUS_COMPLETED:
        observation["state"] = "scan_completed"
    elif status in SCAN_STATUS_FAILED:
        observation["state"] = "scan_failed"
    elif last is None and status is None:
        observation["state"] = "no_scan"
    else:
        observation["state"] = "scan_started"
    return observation


def write_scan_status(reference: str, observation: dict[str, Any], observed_at: datetime) -> None:
    """Store one CO's latest observation with observed_at/expires_at (replaces its previous one)."""
    if not REFERENCE.fullmatch(reference):
        raise ValueError("invalid_scan_status_reference")
    try:
        state = json.loads(SCAN_STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state[reference] = {**observation, "observed_at": observed_at.isoformat(timespec="seconds"),
                        "expires_at": (observed_at + SCAN_STATUS_TTL).isoformat(timespec="seconds")}
    _write_json_atomic(SCAN_STATUS_PATH, state)


def _scan_status_tenant_name(reference: str, surface_only: bool = False) -> str:
    """Tenant name for the search, from one fixed, minimal Salesforce read (values stay in memory)."""
    rows = _sf_records(
        "SELECT Name, Account_Name__c, Onboarding_Product__c, Onboarding_Type__c "
        "FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2")
    if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("Name") != reference:
        raise ValueError()
    row = rows[0]
    account_name = row.get("Account_Name__c")
    if not isinstance(account_name, str) or not " ".join(account_name.split()):
        raise ValueError()
    product, onboarding_type = row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")
    if product == SURFACE_ROUTE_PRODUCT and onboarding_type == SURFACE_ROUTE_TYPE:
        return surface_names(account_name).tenant_name
    if product == CASE3_ROUTE_PRODUCT and onboarding_type == CASE3_ROUTE_TYPE:
        return surface_names(account_name).tenant_name
    if product == CE_ROUTE_PRODUCT and onboarding_type == CE_ROUTE_TYPE:
        if surface_only:
            raise SurfaceSourceError("scan_status_not_applicable")  # CE-only tenants never scan
        return ce_only_names(account_name).tenant_name
    raise SurfaceSourceError("scan_status_route_unsupported")


def run_scan_status(reference: str, surface_only: bool = False) -> str:
    """Read-only scan-status sweep for one onboarded CO (never fills, submits, or creates).

    Requires a local readback (the captured Surface Account ID and Account
    UUID). Searches Leonardo Development for the tenant name and accepts
    exactly one row whose id AND accountUuid equal the captured values, then
    stores a minimal scan observation. It does not touch the create gate,
    the runner state, the readback evidence, or Salesforce.
    """
    if not REFERENCE.fullmatch(reference):
        return "invalid_co_reference"
    try:
        readback = json.loads(READBACK_PATH.read_text(encoding="utf-8")).get(reference)
    except (OSError, ValueError, AttributeError):
        readback = None
    if (not isinstance(readback, dict) or not isinstance(readback.get("surface_account_id"), str)
            or not isinstance(readback.get("account_uuid"), str)):
        return "scan_status_not_onboarded"
    try:
        tenant_name = _scan_status_tenant_name(reference, surface_only)
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    except SurfaceSourceError as error:
        return str(error)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError):
        return "scan_status_source_unavailable"
    with sync_playwright() as playwright:
        try:
            with _attended_page(playwright) as page:
                search = _open_search(page)
                if search is None:
                    return "duplicate_search_schema_unavailable"
                searched = _search_tenants(page, search, tenant_name)
                if searched is None:
                    return "scan_status_schema_unavailable"
                matches = [row for row in searched.rows
                           if row.get("id") == readback["surface_account_id"]
                           and isinstance(row.get("accountUuid"), str)
                           and row["accountUuid"].casefold() == readback["account_uuid"].casefold()]
                if len(matches) != 1:
                    return "scan_status_tenant_not_found"
                observation = scan_status_from_row(matches[0])
                try:
                    write_scan_status(reference, observation, datetime.now())
                except (OSError, ValueError):
                    return "scan_status_write_unavailable"
                return "scan_status_recorded"
        except LoginTimeout:
            return "development_login_timeout"
        except RuntimeError as error:
            return str(error)
        except Exception:
            return "attended_ce_runner_unavailable"


def run_scan_status_all() -> dict[str, str]:
    """Read-only scan-status read for every onboarded Surface / Case 3 CO, one at a time.

    CE-only tenants are skipped (scan_status_not_applicable). A Leonardo
    session problem stops the sweep, since every later CO would fail the
    same way. Returns {CO: result}; each CO's result is also recorded as its
    latest read-only check.
    """
    try:
        references = sorted(reference for reference in json.loads(READBACK_PATH.read_text(encoding="utf-8"))
                            if isinstance(reference, str) and REFERENCE.fullmatch(reference))
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    results: dict[str, str] = {}
    for reference in references:
        result = run_scan_status(reference, surface_only=True)
        results[reference] = result
        if result != "scan_status_not_applicable":
            _record_check(reference, "scan_status", result)
        if result in ("leonardo_session_expired", "development_login_timeout", "playwright_runtime_unavailable"):
            break
    return results


# Plan control -> path in the tenant search row (schema probe 2026-10-02, CO-0649).
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
VALIDATION_TOGGLE_LABELS = {
    "mfaRequired": "MFA required", "automatedDiscoveryEnabled": "Automated discovery",
    "subDomainsReconEnabled": "Recon Subdomains", "webDictionaryBruteForceEnabled": "Web dictionary brute force",
    "webDorkingEnabled": "Web dorking", "fullNucleiScanEnabled": "Nuclei",
    "authenticatedTestingEnabled": "Authenticated testing", "staticOutboundIpEnabled": "Static outbound IP",
    "aiEnabled": "AI", "multipleAttackStacksEnabled": "Multiple attack stacks",
    "notificationsAllowed": "Notifications", "multipleUsersAllowed": "Multiple users", "apiAccessAllowed": "API access",
    "phishingEnabled": "Phishing", "leakedCredentialsAllowed": "Leaked Credentials", "provisioningEnabled": "Provisioning",
}
VALIDATION_SELECT_PATHS = {
    "Account Type": "accountType", "Scanning interval": "scanningInterval",
    "Leaked Credentials scanning interval": "leakedCredentialsScanningInterval", "Type": "accountLicense.licenseType",
}
VALIDATION_NUMBER_PATHS = {
    "Number of assets": "accountLicense.assetsNumber", "Number of domains": "accountLicense.domainsNumber",
    "Number of subdomains": "accountLicense.subDomainsNumber",
}
VALIDATION_LIST_PATHS = {
    "Alternate Domains (Comma Separated Values)": ("alternateDomains", "Alternate domains"),
    "SubDomains (Comma Separated Values)": ("subDomains", "Subdomains"),
    "Leaked Credentials scanned domains (Comma Separated Values)": ("leakedCredentialsScannedDomains",
                                                                   "Leaked Credentials domains"),
    "Networks (Comma Separated Values)": ("additionalNetworks", "Networks"),
}
VALIDATION_IDENTITY_PATHS = {
    "Company name": ("accountName", "Company name"), "Company primary domain": ("accountDomain", "Primary domain"),
    "First name": ("primaryUser.firstName", "Primary user first name"),
    "Last name": ("primaryUser.lastName", "Primary user last name"),
    "Organization Email": ("primaryUser.email", "Primary user email"),
}
VALIDATION_BLANK_PATHS = {"Phone number": "primaryUser.phoneNumber", "Job title": "primaryUser.jobTitle"}


def _row_value(row: dict[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


# Leonardo's stored spelling of a form option, where it differs beyond case and
# separators (validate-all 2026-10-02: the form's "None" interval is NO_SCHEDULE).
_ENUM_ALIASES = {"noschedule": "none"}


def _norm_enum(value: Any) -> str:
    """'Prepaid annual subscription' == 'PREPAID_ANNUAL_SUBSCRIPTION'; None/''/'NO_SCHEDULE' == 'none'."""
    text = re.sub(r"[^a-z0-9]", "", str(value).casefold()) if value not in (None, "") else ""
    text = text or "none"
    return _ENUM_ALIASES.get(text, text)


def _norm_text(value: Any) -> str:
    return " ".join(str(value).casefold().split()).rstrip(".") if value is not None else ""


def _epoch_dates(value: Any) -> set[str]:
    """An epoch-ms licence date as ISO dates in UTC and local time (a day boundary may fall between)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return set()
    try:
        return {datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat(),
                datetime.fromtimestamp(value / 1000).date().isoformat()}
    except (OverflowError, OSError, ValueError):
        return set()


def validate_row(row: dict[str, Any], plan: dict[str, Any] | None,
                 entered: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    """Compare one tenant search row with the route's fill plan (read-only, pure).

    Each check is {"check", "group", "status"} with status ok / drift /
    unknown / info, plus "expected"/"found" for booleans, enums, numbers and
    dates only. Without a plan (source unavailable) only the account status,
    scan, and people checks run.
    """
    checks: list[dict[str, Any]] = []

    def add(group: str, name: str, status: str, expected: Any = None, found: Any = None, safe: bool = True) -> None:
        item: dict[str, Any] = {"group": group, "check": name, "status": status}
        if safe and (expected is not None or found is not None):
            item["expected"], item["found"] = expected, found
        checks.append(item)

    # Account status.
    for name, path, want in (("Account enabled", "enabled", True), ("Not deleted", "isDeleted", False),
                             ("Licence enabled", "accountLicense.enabled", True)):
        found = _row_value(row, path)
        add("Account", name, "ok" if found is want else "drift", want, found)
    if plan is not None:
        for label, key in VALIDATION_SELECT_PATHS.items():
            if label not in plan.get("selects", {}):
                continue
            want, found = plan["selects"][label], _row_value(row, key)
            add("Licence" if label == "Type" else "Settings", label if label != "Type" else "Licence type",
                "ok" if _norm_enum(want) == _norm_enum(found) else "drift", want, found)
        for label, key in VALIDATION_NUMBER_PATHS.items():
            if label not in plan.get("texts", {}):
                continue
            want, found = int(plan["texts"][label]), _row_value(row, key)
            add("Licence", label, "ok" if found == want else "drift", want, found)
        start, end = plan.get("license_start"), plan.get("license_end")
        if end is not None:
            found = sorted(_epoch_dates(_row_value(row, "accountLicense.expirationDate")))
            add("Licence", "Expiration date", "ok" if end.isoformat() in found else "drift",
                end.isoformat(), found[0] if found else None)
        found_start = sorted(_epoch_dates(_row_value(row, "accountLicense.startDate")))
        if entered is not None:
            add("Licence", "Start date", "ok" if entered[0] in found_start else "drift",
                entered[0], found_start[0] if found_start else None)
        else:
            add("Licence", "Start date", "info", None, found_start[0] if found_start else None)
        for key, target in plan.get("checkboxes", {}).items():
            path = VALIDATION_TOGGLE_PATHS.get(key)
            if path is None:
                continue  # not exposed in the search row (e.g. Scan now, Include subdomains)
            found = _row_value(row, path)
            add("People" if key == "mfaRequired" else "Settings", VALIDATION_TOGGLE_LABELS[key],
                "ok" if found is target else "drift", target, found)
        for label, (key, name) in VALIDATION_IDENTITY_PATHS.items():
            if label not in plan.get("texts", {}):
                continue
            same = _norm_text(plan["texts"][label]) == _norm_text(_row_value(row, key))
            add("People" if key.startswith("primaryUser") else "Account", name, "ok" if same else "drift", safe=False)
        for label, (key, name) in VALIDATION_LIST_PATHS.items():
            if label in plan.get("texts", {}):
                want = {_norm_text(v) for v in str(plan["texts"][label]).split(",") if v.strip()}
            elif label in (plan.get("blank_texts") or ()):
                want = set()
            else:
                continue
            found_list = _row_value(row, key)
            found_set = {_norm_text(v) for v in found_list} if isinstance(found_list, list) else None
            add("Domains", name, "ok" if found_set == want else "drift",
                f"{len(want)} item(s)", f"{len(found_set)} item(s)" if found_set is not None else None)
        if "User email domains  (Comma Separated Values)" in plan.get("texts", {}):
            want = {_norm_text(v) for v in plan["texts"]["User email domains  (Comma Separated Values)"].split(",")}
            found_list = _row_value(row, "userEmailDomains")
            found_set = {_norm_text(v) for v in found_list} if isinstance(found_list, list) else set()
            # The customer's domain is added later with the customer user, so "includes" is enough.
            add("People", "User email domains include the plan", "ok" if want <= found_set else "drift",
                f"{len(want)} required", f"{len(found_set)} present")
        for label, key in VALIDATION_BLANK_PATHS.items():
            if label in (plan.get("blank_texts") or ()):
                add("People", f"{label} empty", "ok" if _row_value(row, key) in (None, "") else "drift", safe=False)
        if plan.get("advanced_texts", {}).get("Maximum scan Duration (hours)"):
            want, found = int(plan["advanced_texts"]["Maximum scan Duration (hours)"]), _row_value(row, "campaignsTimeoutInHours")
            if found is None:
                # Not in the search data: CO-0649's row has campaignsTimeoutInHours = null while the
                # Edit form shows 90 h (operator-confirmed 2026-10-02), so it is reported, not judged.
                add("Settings", "Maximum scan duration (h)", "info", None, "not in search data (check the Edit form)")
            else:
                add("Settings", "Maximum scan duration (h)", "ok" if found == want else "drift", want, found)
    operators = _row_value(row, "operatorAccounts")
    add("People", "Operator Account", "info", None, "assigned" if operators else "not assigned")
    add("People", "Customer accepted terms of use", "info", None,
        "yes" if _row_value(row, "termsOfUseApproval") else "not yet")
    scan = scan_status_from_row(row)
    add("Scan", "Scan status", "info", None, scan["state"])
    timed_out = _row_value(row, "lastReconExecutionData.timedOutActions")
    if isinstance(timed_out, list):
        add("Scan", "Timed-out scan actions", "ok" if not timed_out else "drift", 0, len(timed_out))
    pending = _row_value(row, "pendingValidationAssets")
    if isinstance(pending, int) and not isinstance(pending, bool):
        add("Scan", "Pending validation assets", "info", None, pending)
    return checks


def write_validation(reference: str, route: str, checks: list[dict[str, Any]], observed_at: datetime,
                     plan_note: str = "") -> None:
    if not REFERENCE.fullmatch(reference):
        raise ValueError("invalid_validation_reference")
    try:
        state = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state[reference] = {"route": route, "checks": checks, "plan_note": plan_note,
                        "observed_at": observed_at.isoformat(timespec="seconds"),
                        "expires_at": (observed_at + VALIDATION_TTL).isoformat(timespec="seconds")}
    _write_json_atomic(VALIDATION_PATH, state)


def _validation_route(reference: str) -> tuple[str, str]:
    """(route engine, tenant name) from one fixed, minimal Salesforce read."""
    rows = _sf_records(
        "SELECT Name, Account_Name__c, Onboarding_Product__c, Onboarding_Type__c "
        "FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2")
    if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("Name") != reference:
        raise ValueError()
    row = rows[0]
    account_name = row.get("Account_Name__c")
    if not isinstance(account_name, str) or not " ".join(account_name.split()):
        raise ValueError()
    pair = (row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    if pair == (SURFACE_ROUTE_PRODUCT, SURFACE_ROUTE_TYPE):
        return SURFACE_ENGINE, surface_names(account_name).tenant_name
    if pair == (CASE3_ROUTE_PRODUCT, CASE3_ROUTE_TYPE):
        return CASE3_ENGINE, surface_names(account_name).tenant_name
    if pair == (CE_ROUTE_PRODUCT, CE_ROUTE_TYPE):
        return CE_ENGINE, ce_only_names(account_name).tenant_name
    raise SurfaceSourceError("validation_route_unsupported")


def run_validate(reference: str) -> str:
    """Read-only Surface validation of one onboarded CO (never fills, submits, or creates).

    Matches the tenant by the captured id AND accountUuid, recomputes the
    route plan from a fresh Salesforce read, compares them with validate_row,
    and also refreshes the scan observation. A plan that cannot be rebuilt
    (source changed or unavailable) still records the account/scan checks.
    """
    prepared = _validation_inputs(reference)
    if isinstance(prepared, str):
        return prepared
    route, tenant_name, ids, plan, plan_note, entered = prepared
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    with sync_playwright() as playwright:
        try:
            with _attended_page(playwright) as page:
                search = _open_search(page)
                if search is None:
                    return "duplicate_search_schema_unavailable"
                searched = _search_tenants(page, search, tenant_name)
                if searched is None:
                    return "validation_schema_unavailable"
                matches = _match_captured_row(searched.rows, ids)
                if len(matches) != 1:
                    return "validation_tenant_not_found"
                return _record_validation(reference, route, matches[0], plan, plan_note, entered)
        except LoginTimeout:
            return "development_login_timeout"
        except RuntimeError as error:
            return str(error)
        except Exception:
            return "attended_ce_runner_unavailable"


def _validation_inputs(reference: str) -> "str | tuple[str, str, tuple[str, str], dict[str, Any] | None, str, tuple[str, str] | None]":
    """(route, tenant name, captured IDs, plan, plan note, entered licence dates), or a result code."""
    if not REFERENCE.fullmatch(reference):
        return "invalid_co_reference"
    ids = _readback_ids(reference)
    if ids is None:
        return "validation_not_onboarded"
    try:
        route, tenant_name = _validation_route(reference)
    except SurfaceSourceError as error:
        return str(error)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError):
        return "validation_source_unavailable"
    plan: dict[str, Any] | None = None
    plan_note = ""
    entered: tuple[str, str] | None = None
    try:
        record = load_runner_state().get(reference) or {}
        if record.get("license_start_entered") and record.get("license_end_entered"):
            entered = (record["license_start_entered"], record["license_end_entered"])
    except RunnerStateUnavailable:
        record = {}
    try:
        contract = ROUTES[route]
        source = contract.load_source(reference)
        run_day = date.fromisoformat(entered[0]) if entered else None
        plan = contract.build_fill(source, run_day)
        if entered is None:
            plan["license_start"] = None  # the creation day is not recorded for older runs
    except (RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        plan, plan_note = None, str(error) or "plan_unavailable"
    return route, tenant_name, ids, plan, plan_note, entered


def _record_validation(reference: str, route: str, row: dict[str, Any], plan: dict[str, Any] | None,
                       plan_note: str, entered: tuple[str, str] | None) -> str:
    """Compare one matched tenant row with its plan; store the checks and the scan observation."""
    checks = validate_row(row, plan, entered)
    try:
        now = datetime.now()
        write_validation(reference, route, checks, now, plan_note)
        write_scan_status(reference, scan_status_from_row(row), now)
    except (OSError, ValueError):
        return "validation_write_unavailable"
    drift = sum(1 for check in checks if check["status"] == "drift")
    return "validation_recorded" if not drift else "validation_drift_found"


def run_validate_all() -> dict[str, str]:
    """Read-only validation of every onboarded CO, one at a time; stops on a session problem."""
    try:
        references = sorted(reference for reference in json.loads(READBACK_PATH.read_text(encoding="utf-8"))
                            if isinstance(reference, str) and REFERENCE.fullmatch(reference))
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    results: dict[str, str] = {}
    for reference in references:
        results[reference] = result = run_validate(reference)
        _record_check(reference, "validation", result)
        if result in SESSION_STOP_RESULTS:
            break
    return results


# --- Leonardo tenant inventory (2026-10-03) ----------------------------------
# Read-only. The runner never builds its own API request: it reloads Tenant
# Management and clicks the table's Next button, and captures the
# getAllDetailedAccounts responses the page itself requests. Raw rows stay in
# memory; only the allow-listed snapshot (integration/onboarding/
# leonardo_inventory.py) is written, outside the repository.
INVENTORY_API_PATH = "/api/v1/backoffice/getAllDetailedAccounts"
INVENTORY_RESPONSE_TIMEOUT_MS = 20_000
INVENTORY_PAGE_DELAY_SECONDS = 1.0
# The page-level session check also proves the API accepts the session (a tab
# can show Tenant Management after its API session expired). Off until the
# read-only probe confirms that a reload of Tenant Management sends the
# unfiltered table request (assumption A1, 2026-10-03); then a reviewed commit
# turns it on.
SESSION_API_CHECK_ENABLED = False
SESSION_STOP_RESULTS = ("leonardo_session_expired", "development_login_timeout", "playwright_runtime_unavailable")


def _is_inventory_response(response: Any) -> bool:
    """Exactly the table POST on the Development origin; anything else is ignored."""
    try:
        return (response.request.method == "POST" and _url_origin(response.url) == DEVELOPMENT_ORIGIN
                and _url_path(response.url) == INVENTORY_API_PATH)
    except Exception:
        return False


def _capture_table_page(page: Any, trigger: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run `trigger` and return (request body, response body) of the table request it causes.

    Raises LeonardoSessionExpired on 401 (or a JSON 403), else RuntimeError
    with an inventory_* code. Bodies stay in memory; only status and counts
    are logged.
    """
    try:
        with page.expect_response(_is_inventory_response, timeout=INVENTORY_RESPONSE_TIMEOUT_MS) as info:
            trigger()
        response = info.value
    except Exception as exc:
        _log().error("inventory", "response", exc)
        raise RuntimeError("inventory_no_signal") from exc
    status = response.status
    try:
        content_type = (response.headers or {}).get("content-type", "")
    except Exception:
        content_type = ""
    json_reply = "json" in content_type.lower()
    _log().event("inventory", str(status))
    if status == 401 or (status == 403 and json_reply):
        raise LeonardoSessionExpired()
    if status == 429:
        raise RuntimeError("inventory_rate_limited")
    if status >= 500:
        raise RuntimeError("inventory_server_error")
    if not 200 <= status < 300 or not json_reply:
        # A WAF/CloudFront block or a login redirect answers with HTML.
        raise RuntimeError("inventory_waf_or_redirect")
    try:
        request_body = json.loads(response.request.post_data or "")
        body = response.json()
    except Exception as exc:
        # Live 2026-10-03: the reply body was once unreadable right after a reload
        # (intermittent); the export retries the whole sweep once on this code.
        _log().event("inventory", "body_unavailable", detail=type(exc).__name__)
        raise RuntimeError("inventory_body_unavailable") from exc
    if not isinstance(request_body, dict) or not isinstance(body, dict):
        raise RuntimeError("inventory_schema_unavailable")
    if not _is_tenant_management_url(page.url):
        raise RuntimeError("inventory_waf_or_redirect")
    return request_body, body


def _table_query(request_body: dict[str, Any]) -> dict[str, Any]:
    query = request_body.get("tableServerData")
    if not isinstance(query, dict):
        raise RuntimeError("inventory_schema_unavailable")
    return query


def _table_counts(body: dict[str, Any]) -> tuple[int, int]:
    paged = body.get("pagination_response")
    rows = paged.get("table_data") if isinstance(paged, dict) else None
    total = paged.get("total_count") if isinstance(paged, dict) else None
    if not isinstance(rows, list) or type(total) is not int:
        raise RuntimeError("inventory_schema_unavailable")
    return total, len(rows)


def _filter_present(filters: Any) -> bool:
    """True when the table request carries any filter (a search would hide tenants)."""
    if filters in (None, {}, []):
        return False
    if isinstance(filters, dict) and set(filters) <= {"and", "or"}:
        return any(filters.get(key) for key in filters)
    return True


def _filter_methods(filters: Any) -> list[str]:
    """Filter method names only (never their values), for the probe report."""
    methods: list[str] = []
    if isinstance(filters, dict):
        for clauses in filters.values():
            for clause in clauses if isinstance(clauses, list) else []:
                method = clause.get("method") if isinstance(clause, dict) else None
                if isinstance(method, str) and re.fullmatch(r"[A-Za-z_]{1,32}", method):
                    methods.append(method)
    return methods


def _next_page_button(page: Any) -> tuple[Any | None, str]:
    """The table's single Next-page control, and which locator found it."""
    candidates = (("role", page.get_by_role("button", name=re.compile(r"^next page$", re.IGNORECASE))),
                  ("id", page.locator("#pagination-next")),
                  ("testid", page.locator("[data-testid='pagination-next']")))
    for kind, locator in candidates:
        try:
            if locator.count() == 1:
                return locator, kind
        except Exception:
            continue
    return None, "none"


def _footer_numbers(page: Any) -> list[int]:
    """Numbers in the table pagination footer (e.g. "1-10 of 26"), nothing else."""
    try:
        texts = page.locator("[class*='MuiTablePagination']").all_inner_texts()
    except Exception:
        return []
    # Some pagination elements report no text (None, live 2026-10-03).
    return [int(n) for n in re.findall(r"\d{1,6}", " ".join(t for t in texts if isinstance(t, str)))][:8]


def _reload(page: Any) -> Any:
    return lambda: page.reload(wait_until="domcontentloaded")


def _collect_inventory_pages(page: Any) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
    """Page 1 from a reload, then Next until every page is captured (fail closed)."""
    from integration.onboarding import leonardo_inventory as inventory

    pages = [_capture_table_page(page, _reload(page))]
    query = _table_query(pages[0][0])
    page_size = query.get("items_per_page")
    if type(page_size) is not int or page_size < 1 or query.get("offset") != 0:
        raise RuntimeError("inventory_schema_unavailable")
    if _filter_present(query.get("filters")):
        raise RuntimeError("inventory_filtered")
    total, _rows = _table_counts(pages[0][1])
    needed = -(-total // page_size)
    if total > inventory.MAX_TOTAL or needed > inventory.MAX_PAGES:
        raise RuntimeError("inventory_too_large")
    while len(pages) < needed:
        sleep(INVENTORY_PAGE_DELAY_SECONDS)
        button, _kind = _next_page_button(page)
        if button is None:
            raise RuntimeError("inventory_pagination_unavailable")
        pages.append(_capture_table_page(page, lambda: button.click(timeout=FIELD_TIMEOUT_MS)))
    return pages, page_size


def _api_session_check(playwright: Any, tab: "_AutomationTab") -> str:
    """The tab is at Tenant Management: prove the API accepts the session too (read-only)."""
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{tab.port}")
    try:
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = _find_live_page(context, tab)
        if page is None:
            return "leonardo_session_unavailable"
        request_body, body = _capture_table_page(page, _reload(page))
        _table_query(request_body)
        _table_counts(body)
        return "leonardo_session_active"
    except LeonardoSessionExpired:
        return "leonardo_session_expired"
    except RuntimeError as error:
        return "leonardo_api_" + str(error).removeprefix("inventory_")
    finally:
        try:
            browser.close()
        except Exception:
            pass


def run_probe_inventory_shape() -> dict[str, Any]:
    """One read-only look at how the table pages (key names, counts, sort; never values).

    Reloads Tenant Management, records page 1's request shape and counts,
    clicks Next once if there is a second page, and records page 2's offset.
    Settles assumption A1 and the 10-vs-1000 page-size question before any
    export is trusted.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return {"result": "playwright_runtime_unavailable"}
    report: dict[str, Any] = {}
    with sync_playwright() as playwright:
        try:
            with _attended_page(playwright) as page:
                request_body, body = _capture_table_page(page, _reload(page))
                query = _table_query(request_body)
                total, rows = _table_counts(body)
                sort = query.get("sort") if isinstance(query.get("sort"), dict) else {}
                report["page1"] = {
                    "request_keys": sorted(str(key)[:40] for key in request_body),
                    "query_keys": sorted(str(key)[:40] for key in query),
                    "offset": query.get("offset") if type(query.get("offset")) is int else None,
                    "items_per_page": query.get("items_per_page") if type(query.get("items_per_page")) is int else None,
                    "sort": {key: sort.get(key) for key in ("direction", "key")
                             if isinstance(sort.get(key), str) and re.fullmatch(r"[A-Za-z_.]{1,40}", sort[key])},
                    "filter_present": _filter_present(query.get("filters")),
                    "filter_methods": _filter_methods(query.get("filters")),
                    "total_count": total, "rows_on_page": rows,
                }
                report["footer_numbers"] = _footer_numbers(page)
                button, kind = _next_page_button(page)
                report["next_button"] = kind
                report["page2"] = None
                if button is not None and total > rows:
                    sleep(INVENTORY_PAGE_DELAY_SECONDS)
                    request2, body2 = _capture_table_page(page, lambda: button.click(timeout=FIELD_TIMEOUT_MS))
                    query2 = _table_query(request2)
                    total2, rows2 = _table_counts(body2)
                    report["page2"] = {
                        "offset": query2.get("offset") if type(query2.get("offset")) is int else None,
                        "items_per_page": query2.get("items_per_page") if type(query2.get("items_per_page")) is int else None,
                        "same_sort": query2.get("sort") == query.get("sort"),
                        "same_filters": query2.get("filters") == query.get("filters"),
                        "total_count": total2, "rows_on_page": rows2,
                    }
                report["result"] = "inventory_probe_recorded"
        except LoginTimeout:
            report["result"] = "development_login_timeout"
        except RuntimeError as error:
            report["result"] = str(error)
        except Exception:
            report["result"] = "attended_ce_runner_unavailable"
    return report


def run_export_tenants(env_name: str = "dev", *, with_sweeps: bool = False, write_csv: bool = False,
                       root: Path | None = None) -> dict[str, Any]:
    """Read-only export of every tenant to the allow-listed local snapshot (stdout: counts only)."""
    from integration.onboarding import leonardo_inventory as inventory

    try:
        environment = inventory.require_environment(env_name)
    except inventory.InventoryError as error:
        return {"result": error.reason}
    if environment.origin != DEVELOPMENT_ORIGIN:
        return {"result": "inventory_environment_not_supported"}
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return {"result": "playwright_runtime_unavailable"}
    with sync_playwright() as playwright:
        try:
            with _attended_page(playwright) as page:
                assembled = None
                for attempt in range(2):
                    try:
                        pages, page_size = _collect_inventory_pages(page)
                        assembled = inventory.assemble_pages(pages, page_size=page_size)
                        break
                    except LeonardoSessionExpired:
                        raise
                    except RuntimeError as error:
                        # An unreadable reply body (intermittent) is retried once, from page 1.
                        if str(error) != "inventory_body_unavailable" or attempt:
                            raise
                        _log().event("inventory", "retry", detail="body unavailable")
                    except inventory.InventoryError as error:
                        # A scan finishing mid-export can reorder rows (sort by last scan): retry once.
                        if error.reason != "inventory_inconsistent" or attempt:
                            return {"result": error.reason}
                        _log().event("inventory", "retry", detail="inconsistent pages")
                payload = inventory.snapshot_payload(environment, assembled, datetime.now(timezone.utc))
                target = inventory.write_snapshot(payload, root or inventory.default_root())
                if write_csv:
                    target.with_suffix(".csv").write_text(inventory.to_csv(payload), encoding="utf-8", newline="")
                sweeps = _sweep_from_inventory(assembled.rows) if with_sweeps else {}
                drift = payload["schema_drift"]
                return {"result": "inventory_exported", "environment": environment.name,
                        "environment_label": environment.label,
                        "total_count": payload["total_count"], "row_count": payload["row_count"],
                        "deleted_count": payload["deleted_count"], "pages": payload["pages"],
                        "uuid_missing_count": payload["uuid_missing_count"],
                        "schema_drift": {key: len(drift.get(key, [])) for key in ("unknown", "missing", "type_changed")},
                        "file": target.name, "csv": bool(write_csv), "sweeps": sweeps}
        except inventory.InventoryError as error:
            return {"result": error.reason}
        except LoginTimeout:
            return {"result": "development_login_timeout"}
        except RuntimeError as error:
            return {"result": str(error)}
        except Exception:
            return {"result": "attended_ce_runner_unavailable"}


def _readback_ids(reference: str) -> tuple[str, str] | None:
    try:
        readback = json.loads(READBACK_PATH.read_text(encoding="utf-8")).get(reference)
    except (OSError, ValueError, AttributeError):
        return None
    if (not isinstance(readback, dict) or not isinstance(readback.get("surface_account_id"), str)
            or not isinstance(readback.get("account_uuid"), str)):
        return None
    return readback["surface_account_id"], readback["account_uuid"]


def _match_captured_row(rows: Any, ids: tuple[str, str]) -> list[dict[str, Any]]:
    """Rows whose id AND accountUuid equal the captured readback IDs."""
    return [row for row in rows if isinstance(row, dict) and row.get("id") == ids[0]
            and isinstance(row.get("accountUuid"), str) and row["accountUuid"].casefold() == ids[1].casefold()]


def _sweep_from_inventory(rows: tuple[dict[str, Any], ...]) -> dict[str, str]:
    """Validation (and its scan observation) for every onboarded CO from one paged read.

    Same checks and files as --validate-all; only the tenant row comes from
    the export instead of one live search per CO. The plan is still rebuilt
    from a fresh, read-only Salesforce read.
    """
    try:
        references = sorted(reference for reference in json.loads(READBACK_PATH.read_text(encoding="utf-8"))
                            if isinstance(reference, str) and REFERENCE.fullmatch(reference))
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    results: dict[str, str] = {}
    for reference in references:
        prepared = _validation_inputs(reference)
        if isinstance(prepared, str):
            result = prepared
        else:
            route, _tenant_name, ids, plan, plan_note, entered = prepared
            matches = _match_captured_row(rows, ids)
            result = (_record_validation(reference, route, matches[0], plan, plan_note, entered)
                      if len(matches) == 1 else "validation_tenant_not_found")
        results[reference] = result
        _record_check(reference, "validation", result)
    return results


# Any age: an old snapshot's match still stops a run; a fresh export (a live read) clears it.
INVENTORY_PRECHECK_MAX_AGE = timedelta(days=3650)


def inventory_root() -> Path:
    """Where the tenant inventory lives (tests point this at an empty folder)."""
    from integration.onboarding import leonardo_inventory as inventory

    return inventory.default_root()


def _co_domains(contract: RouteContract, source: Any) -> tuple[str, ...]:
    """Every domain the CO brings: its primary domain plus the route's extra lookups."""
    extras = contract.extra_lookups(source) if contract.extra_lookups else ()
    return (contract.primary_domain(source), *(domain for _name, _lookup, domain in extras))


def _inventory_precheck(contract: RouteContract, source: Any, log: "RunLog | None" = None) -> dict[str, Any]:
    """The duplicate pre-check for one loaded CO source; no usable inventory is "inventory_unavailable"."""
    from integration.onboarding import leonardo_inventory as inventory

    log = log or _log()
    try:
        payload = inventory.load_latest(inventory_root(), "dev", max_age=INVENTORY_PRECHECK_MAX_AGE,
                                        now=datetime.now(timezone.utc))
    except inventory.InventoryError as error:
        log.event("inventory_precheck", "inventory_unavailable", detail=error.reason)
        return {"result": "inventory_unavailable", "reason": error.reason, "matches": []}
    result = inventory.duplicate_precheck(payload, source.tenant_name, _co_domains(contract, source))
    result["captured_at"] = payload.get("captured_at")
    result["environment_label"] = payload.get("environment_label")
    log.event("inventory_precheck", result["result"],
              detail=f"tenants={result['tenants_checked']} matches={len(result['matches'])}")
    return result


def run_inventory_precheck(reference: str, route: str = CE_ENGINE) -> dict[str, Any]:
    """Read-only: one Salesforce source read and the local inventory; no Leonardo or browser."""
    if not REFERENCE.fullmatch(reference):
        return {"result": "invalid_co_reference", "matches": []}
    contract = ROUTES.get(route)
    if contract is None:
        return {"result": "route_unsupported", "matches": []}
    try:
        source = contract.load_source(reference)
    except RuntimeError as error:
        return {"result": str(error), "matches": []}
    return _inventory_precheck(contract, source)


def _combine_duplicate(ui: str, api: str) -> str:
    """Combine the table and server-row classifications (worst outcome wins)."""
    for outcome in ("duplicate_schema_unavailable", "duplicate_found", "duplicate_ambiguous"):
        if outcome in (ui, api):
            return outcome
    return "duplicate_clear"


def run_duplicate_check(reference: str, route: str = CE_ENGINE) -> str:
    """Read-only duplicate check for one CO (never fills, submits, or creates).

    Runs the same two lookups as a create run -- the contract tenant name and
    the primary domain -- and classifies each from both the tenant table and
    the server's own search rows. Returns duplicate_check_clear,
    duplicate_check_found, duplicate_check_ambiguous, or a failure code. It
    does not touch the one-time create gate; only the run log is written.
    """
    global _ACTIVE_RUN_LOG
    _ACTIVE_RUN_LOG = RunLog(reference, "duplicate_check", route=route)
    log = _ACTIVE_RUN_LOG
    result = "attended_ce_runner_unavailable"
    try:
        contract = ROUTES.get(route)
        result = "route_unsupported" if contract is None else _duplicate_check(reference, log, contract)
        return result
    finally:
        log.event("finish", result)
        log.write(result)
        _ACTIVE_RUN_LOG = None


def _duplicate_check(reference: str, log: RunLog, contract: RouteContract = CE_ROUTE) -> str:
    try:
        source = contract.load_source(reference)
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return "playwright_runtime_unavailable"
    except RuntimeError as error:
        return str(error)
    log.add_redactions(*contract.redactions(source))
    log.event("source_read", "ok")
    primary_domain = contract.primary_domain(source)
    with sync_playwright() as playwright:
        try:
            with _attended_page(playwright) as page:
                log.attach(page)
                search = _open_search(page)
                if search is None:
                    _capture_search_diagnostics(page)
                    return "duplicate_search_schema_unavailable"
                for lookup_name, lookup in contract.duplicate_lookups(source):
                    searched = _search_tenants(page, search, lookup)
                    if searched is None:
                        duplicate = "duplicate_schema_unavailable"
                    else:
                        duplicate = _combine_duplicate(
                            _settled_tenant_rows(page, source.tenant_name, primary_domain),
                            _api_duplicate(searched, source.tenant_name, primary_domain))
                    log.event("duplicate_check", duplicate, lookup_name)
                    if duplicate == "duplicate_schema_unavailable":
                        _capture_search_diagnostics(page)
                        return duplicate
                    if duplicate != "duplicate_clear":
                        return {"duplicate_found": "duplicate_check_found",
                                "duplicate_ambiguous": "duplicate_check_ambiguous"}[duplicate]
                return "duplicate_check_clear"
        except LoginTimeout:
            return "development_login_timeout"
        except RuntimeError as error:
            return str(error)
        except Exception as exc:
            log.error("runner", "unexpected", exc)
            return "attended_ce_runner_unavailable"


def _record_check(reference: str, kind: str, result: str) -> None:
    """Best-effort: record a read-only check result for the dashboard."""
    try:
        record_check_result(reference, kind, result, datetime.now().isoformat(timespec="seconds"))
    except (OSError, ValueError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Attended Leonardo Development CE-only auto-confirm runner.")
    parser.add_argument("--co", required=False, help="Customer Onboarding reference (e.g. CO-0702).")
    parser.add_argument("--revision", required=False, help="Salesforce source revision acknowledged by the dashboard.")
    parser.add_argument("--check-session", action="store_true",
                        help="Read-only Leonardo Development session check (no fill, submit, or create).")
    parser.add_argument("--reset-profile", action="store_true",
                        help="Wipe the dedicated persisted Leonardo automation profile (forces fresh SSO/MFA).")
    parser.add_argument("--bootstrap-session", action="store_true",
                        help="Open the attended browser for the operator to complete SSO/MFA (no fill, submit, or create).")
    parser.add_argument("--close-browser", action="store_true",
                        help="Close the reused automation Chrome window (keeps the persisted profile; no fill, submit, or create).")
    parser.add_argument("--readback-only", action="store_true",
                        help="Read-only verification of an existing tenant (no fill, submit, or create; does not consume the create gate).")
    parser.add_argument("--tenant-name", required=False,
                        help="Override the expected tenant name for --readback-only (targets a live tenant whose name intentionally deviates from the contract name).")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --co/--revision: run every check and fill the full form, then Cancel instead of Confirm (no create; does not consume the create gate).")
    parser.add_argument("--diagnose-confirm", action="store_true",
                        help="With --co/--revision: a dry run that, if Confirm stays disabled, logs the form's value-free validation state and tries one no-submit change at a time to find the blocking field, then Cancels (no create; does not consume the create gate).")
    parser.add_argument("--probe-lc-prefill", action="store_true",
                        help="With --diagnose-confirm: first try the owner-approved no-submit probe that sets the dormant Leaked Credentials domain to the primary domain and leaves Leaked Credentials OFF.")
    parser.add_argument("--duplicate-check", action="store_true",
                        help="With --co: read-only duplicate check by CE tenant name and primary domain (no fill, submit, or create; does not consume the create gate).")
    parser.add_argument("--scan-status", action="store_true",
                        help="With --co: read-only scan-status read of an onboarded tenant (matches the captured IDs; no fill, submit, or create).")
    parser.add_argument("--validate", action="store_true",
                        help="With --co: read-only Surface validation of an onboarded tenant against its route plan.")
    parser.add_argument("--validate-all", action="store_true",
                        help="Read-only Surface validation of every onboarded CO (no fill, submit, or create).")
    parser.add_argument("--scan-status-all", action="store_true",
                        help="Read-only scan-status read for every onboarded Surface / Case 3 CO (no fill, submit, or create).")
    parser.add_argument("--duplicate-precheck", action="store_true",
                        help="With --co (and --route): read-only duplicate pre-check against the latest DEV tenant inventory (no Leonardo, no browser).")
    parser.add_argument("--open-dashboard", type=int, metavar="PORT",
                        help="Open (or bring to the front) the local dashboard's sign-in page as a tab of the automation window.")
    parser.add_argument("--probe-inventory-shape", action="store_true",
                        help="Read-only: reload Tenant Management and report how the tenant table pages (key names and counts only).")
    parser.add_argument("--export-tenants", action="store_true",
                        help="Read-only: page through Tenant Management and write the allow-listed tenant snapshot outside the repository.")
    parser.add_argument("--env", choices=("dev", "prod"), default="dev",
                        help="With --export-tenants: environment (prod is refused until separately approved).")
    parser.add_argument("--with-sweeps", action="store_true",
                        help="With --export-tenants: also validate every onboarded CO from the exported rows.")
    parser.add_argument("--csv", action="store_true",
                        help="With --export-tenants: also write a CSV of the allow-listed fields next to the snapshot.")
    parser.add_argument("--route", choices=sorted(ROUTES), default=CE_ENGINE,
                        help="Route contract for --co runs (default: the CE-only route, case_2_new_ce_only).")
    args = parser.parse_args()
    if args.duplicate_precheck:
        if not args.co:
            parser.error("--co is required with --duplicate-precheck")
        report = run_inventory_precheck(args.co, args.route)
        # Tenant ids and match reasons only; names stay in the local inventory.
        print(json.dumps({"result": report["result"], "tenants_checked": report.get("tenants_checked"),
                          "captured_at": report.get("captured_at"),
                          "alternate_domains_available": report.get("alternate_domains_available"),
                          "matches": [{"id": m["id"], "reasons": m["reasons"], "is_deleted": m["is_deleted"]}
                                      for m in report["matches"]],
                          "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.open_dashboard is not None:
        print(json.dumps({"result": open_dashboard_tab(args.open_dashboard)}, separators=(",", ":")))
        return 0
    if args.probe_inventory_shape:
        report = run_probe_inventory_shape()
        print(json.dumps({**report, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.export_tenants:
        summary = run_export_tenants(args.env, with_sweeps=args.with_sweeps, write_csv=args.csv)
        print(json.dumps({**summary, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.duplicate_check:
        if not args.co:
            parser.error("--co is required with --duplicate-check")
        result = run_duplicate_check(args.co, route=args.route)
        _record_check(args.co, "duplicate_check", result)
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.validate_all:
        results = run_validate_all()
        print(json.dumps({"result": "validation_all_finished", "cos": results,
                          "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.validate:
        if not args.co:
            parser.error("--co is required with --validate")
        result = run_validate(args.co)
        _record_check(args.co, "validation", result)
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.scan_status_all:
        results = run_scan_status_all()
        print(json.dumps({"result": "scan_status_all_finished", "cos": results,
                          "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.scan_status:
        if not args.co:
            parser.error("--co is required with --scan-status")
        result = run_scan_status(args.co)
        _record_check(args.co, "scan_status", result)
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.readback_only:
        if not args.co:
            parser.error("--co is required with --readback-only")
        result = run_readback(args.co, tenant_name_override=args.tenant_name, route=args.route)
        _record_check(args.co, "readback", result)
        if not args.tenant_name:
            try:
                settle_uncertain_create(args.co, result, datetime.now().isoformat(timespec="seconds"))
            except (OSError, ValueError, RunnerStateUnavailable):
                pass
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.check_session:
        result = check_leonardo_session()
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.reset_profile:
        ok = reset_leonardo_profile()
        result = "leonardo_profile_reset" if ok else "leonardo_profile_reset_unavailable"
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.bootstrap_session:
        result = bootstrap_leonardo_session()
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if args.close_browser:
        result = close_automation_browser()
        print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
        return 0
    if not args.co or not args.revision:
        parser.error("--co and --revision are required unless --check-session, --reset-profile, --bootstrap-session, --close-browser, or --readback-only is given")
    if args.probe_lc_prefill and not args.diagnose_confirm:
        parser.error("--probe-lc-prefill requires --diagnose-confirm")
    if args.probe_lc_prefill:
        result = run(args.co, args.revision, dry_run=args.dry_run, route=args.route, diagnose=True,
                     lc_prefill_probe=True)
    else:
        result = run(args.co, args.revision, dry_run=args.dry_run, route=args.route, diagnose=args.diagnose_confirm)
    print(json.dumps({"result": result, "salesforce_writeback": "not_performed"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
