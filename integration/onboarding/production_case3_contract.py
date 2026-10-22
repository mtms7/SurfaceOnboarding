"""The Case 3 (Surface & Credential Exposure, New) production create contract, learned from the owner's manual create of CO-0800.

Owner decision 2026-10-21 (path A): the tenant was created by hand and the HAR was read structure-only (``tools/har_contract_reader.py``).
This module is the pure expected-value side of that observation (docs/59): the ``account/add`` body and the create response. It builds
nothing, sends nothing and unlocks nothing; the guarded Case 3 create (path B) is a separate owner decision. Mismatches are reported as
field NAMES only, never values. Compared with the CE-only contract (``production_contract``) the differences are the ones the HAR showed.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from . import production_contract as pc

ACCOUNT_TYPE_CREATE = pc.ACCOUNT_TYPE_CREATE
LICENSE_TYPE = pc.LICENSE_TYPE
SCANNING_INTERVAL = "MONTHLY"          # observed for CO-0800 (Go); the default when ``intended`` names no tier values
LC_INTERVAL = "WEEKLY"                 # owner 2026-10-21: the manual create sent NO_SCHEDULE by mistake; future creates run weekly
CAMPAIGN_TIMEOUT_HOURS = 90            # the form default is 24; the Guru card sets 90
LICENSE_ASSETS_TEXT = "10000"          # strings, as in the CE-only create
LICENSE_DOMAINS_TEXT = "10000"
LICENSE_SUBDOMAINS = 500               # a number (Go default)
# Owner decision 2026-10-22 (path B step c): Go = MONTHLY and 500 subdomains, Prime = WEEKLY and 1000; any other tier fails closed.
TIER_RULE = {"Go": ("MONTHLY", 500), "Prime": ("WEEKLY", 1000)}
OBSERVED_SCAN_NOW = True               # the UI sent scanNow true; whether the automation does too is an OPEN owner decision
RESPONSE_CARRIES_ACCOUNT_IDS = False   # the create response has no Account ID / UUID: read them back from the tenant row

FLAGS_OFF = ("automatedDiscoveryEnabled", "webDorkingEnabled", "staticOutboundIpEnabled", "aiEnabled", "webAiAttackerEnabled",
             "vodBetaEnabled")
FLAGS_ON = ("subDomainsReconEnabled", "webDictionaryBruteForceEnabled", "fullNucleiScanEnabled")
LICENSE_ALLOWS_ON = ("notificationsAllowed", "multipleUsersAllowed", "apiAccessAllowed", "enabled", "provisioningEnabled",
                     "leakedCredentialsAllowed")


_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _tier_values(intended: Mapping[str, Any]) -> tuple[str, int]:
    """The scanning interval and subdomain count of ``intended`` (defaults MONTHLY/500); only a pair of the tier rule passes."""
    interval = intended.get("scanning_interval", SCANNING_INTERVAL)
    subdomains = intended.get("subdomains_number", LICENSE_SUBDOMAINS)
    if isinstance(subdomains, bool) or (interval, subdomains) not in TIER_RULE.values():
        raise ValueError("intended_tier_values_invalid")
    return interval, subdomains


def _one_domain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().removeprefix("@").casefold()
    return cleaned if _DOMAIN.fullmatch(cleaned) else None


def _email_domain(raw: Any) -> str:
    if isinstance(raw, str):
        items = [part for part in re.split(r"[,;\s]+", raw.strip()) if part]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        items = list(raw)
    else:
        items = []
    found = [_one_domain(item) for item in items]
    if len(found) != 1 or found[0] is None:
        raise ValueError("email_domain_not_single")
    return found[0]


def _optional_text(value: Any, reason: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CONTROL.search(value):
        raise ValueError(reason)
    return " ".join(value.split()) or None


def case3_intended(*, tenant_name: Any, main_domain: Any, ce_email_domains: Any, user_email_domain: Any, license_end: Any, tier: Any,
                   country_code: Any = None, operator_name: Any = None, organization_email: Any = None,
                   first_name: Any = None, last_name: Any = None) -> dict[str, Any]:
    """The ``intended`` values of a Case 3 New create from plain facts. Pure (no I/O); raises ValueError(reason), never echoing a value.

    ``tenant_name`` is the name step (b) bound the approval to (bare or the owner-confirmed short name, never the alias).
    ``ce_email_domains`` is the CO's Salesforce CE e-mail domain (exactly one): it is the Leaked Credentials scanned domain.
    ``user_email_domain`` is the form's "User email domains" value (the internal domain the plan types, one domain).
    The tier becomes the interval and subdomain count (``TIER_RULE``).
    """
    if not isinstance(tier, str) or tier not in TIER_RULE:
        raise ValueError("tier_unknown")
    if (not isinstance(tenant_name, str) or not tenant_name or _CONTROL.search(tenant_name)
            or tenant_name != " ".join(tenant_name.split())):
        raise ValueError("tenant_name_invalid")
    if tenant_name.endswith(pc.NAME_SUFFIX):
        raise ValueError("tenant_name_has_ce_only_suffix")
    main = _one_domain(main_domain)
    if main is None:
        raise ValueError("main_domain_invalid")
    leaked = _email_domain(ce_email_domains)
    user_email = _one_domain(user_email_domain)
    if user_email is None:
        raise ValueError("user_email_domain_invalid")
    try:
        if not isinstance(license_end, str):
            raise ValueError
        date.fromisoformat(pc._day(license_end))
    except ValueError:
        raise ValueError("license_end_invalid") from None
    if country_code is not None and (not isinstance(country_code, str) or not re.fullmatch(r"[A-Z]{2}", country_code)):
        raise ValueError("country_code_invalid")
    interval, subdomains = TIER_RULE[tier]
    return {"tenant_name": tenant_name, "primary_domain": main, "email_domain": user_email, "leaked_domain": leaked,
            "license_end": license_end, "country_code": country_code,
            "operator_name": _optional_text(operator_name, "operator_name_invalid"),
            "organization_email": _optional_text(organization_email, "organization_email_invalid"),
            "first_name": _optional_text(first_name, "first_name_invalid"),
            "last_name": _optional_text(last_name, "last_name_invalid"),
            "scanning_interval": interval, "subdomains_number": subdomains}


def expected_case3_create_payload(intended: Mapping[str, Any]) -> dict[str, Any]:
    """Flat dotted field name -> expected value (or shape) for the ``account/add`` body of a Case 3 New. Raises ValueError (fail closed)."""
    name = pc._text(intended, "tenant_name")
    domain, email_domain, leaked = (pc._text(intended, key) for key in ("primary_domain", "email_domain", "leaked_domain"))
    if name.endswith(pc.NAME_SUFFIX):
        raise ValueError("tenant_name_has_ce_only_suffix")  # Case 3 uses the bare account name
    end = pc._day(intended.get("license_end"))
    interval, subdomains = _tier_values(intended)
    country = intended.get("country_code")
    operator = intended.get("operator_name")
    expected: dict[str, Any] = {
        "accountName": name, "accountDomain": domain, "accountType": ACCOUNT_TYPE_CREATE,
        "accountCountryCode": country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else pc.SHAPE_ISO2,
        "organizationEmail": pc._optional(intended, "organization_email"),
        "firstName": pc._optional(intended, "first_name"), "lastName": pc._optional(intended, "last_name"),
        "mfaRequired": True, "alternateDomains": [], "subDomains": [], "additionalNetworks": None,
        "userEmailDomains": [email_domain],
        "operatorAccountNames": [operator] if isinstance(operator, str) and operator.strip() else [],
        "scanningInterval": interval, "scanNow": OBSERVED_SCAN_NOW,
        "leakedCredentialsScanningInterval": LC_INTERVAL, "leakedCredentialsScannedDomains": [leaked],
        "campaignsTimeoutInHours": CAMPAIGN_TIMEOUT_HOURS,
        "license.assetsNumber": LICENSE_ASSETS_TEXT, "license.domainsNumber": LICENSE_DOMAINS_TEXT,
        "license.subDomainsNumber": subdomains, "license.licenseType": LICENSE_TYPE,
        "license.startDate": pc.SHAPE_ISO_UTC, "license.expirationDate": pc._iso_on(end),
        "license.phishingEnabled": False, "license.reconLevel": None,
        "campaignExecutionSettings.domainsMultiAttackStackSettings.enabled": False,
        "campaignExecutionSettings.domainsMultiAttackStackSettings.numOfAssetsPerStack": pc.SHAPE_NUMBER,
        "campaignExecutionSettings.subDomainsMultiAttackStackSettings.enabled": False,
        "campaignExecutionSettings.subDomainsMultiAttackStackSettings.numOfAssetsPerStack": pc.SHAPE_NUMBER,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
    }
    for flag in FLAGS_OFF:
        expected[flag] = False
    for flag in FLAGS_ON:
        expected[flag] = True
    for allowed in LICENSE_ALLOWS_ON:
        expected["license." + allowed] = True
    return expected


def compare_case3_payload(expected: Mapping[str, Any], observed: Any) -> list[str]:
    """Mismatching field NAMES (sorted): wrong, wrong-typed or missing; unknown keys as ``unexpected:<name>``."""
    return pc.compare_payload(expected, observed)


def expected_case3_response(scanning_frequency: str = SCANNING_INTERVAL, subdomains: int = LICENSE_SUBDOMAINS) -> dict[str, Any]:
    """The create response summary the HAR showed (values that are not identifying)."""
    return {"allowedModules": [], "apiAccess": True, "assetsNumber": 10000, "domainsNumber": 10000,
            "elevatedFeaturesEnabled": False, "leakedCredentials": True, "leakedCredentialsScannedDomainsNumber": 1,
            "licenseStatus": None, "licenseType": LICENSE_TYPE, "multiUsers": True, "notifications": True,
            "phishingEnabled": False, "provisioningEnabled": True, "reconLevel": pc.RECON_LEVEL_ROW,
            "scanQuotaEnforcement": None, "scanningFrequency": scanning_frequency, "subDomainsNumber": subdomains,
            "accountName": pc.SHAPE_TEXT, "organizationEmail": pc.SHAPE_TEXT,
            "expirationDate": pc.SHAPE_NUMBER, "startDate": pc.SHAPE_NUMBER}


def compare_case3_response(observed: Any, scanning_frequency: str = SCANNING_INTERVAL,
                           subdomains: int = LICENSE_SUBDOMAINS) -> list[str]:
    return pc.compare_payload(expected_case3_response(scanning_frequency, subdomains), observed)


def expected_case3_readback_row(intended: Mapping[str, Any]) -> dict[str, Any]:
    """Logical row field name -> expected value for the ``getAllDetailedAccounts`` row of the created Case 3 tenant.

    Same paths as the CE-only readback (``production_contract``, seen on the live full row 2026-10-18); only the Case 3 values differ.
    The name is the bare or the short name (docs/60), never a " - CE Only" name. Nuclei, the scan duration and the operator are
    not compared: their row paths are not verified yet.
    """
    name = pc._text(intended, "tenant_name")
    if name.endswith(pc.NAME_SUFFIX):
        raise ValueError("tenant_name_has_ce_only_suffix")
    domain, email_domain = pc._text(intended, "primary_domain"), pc._text(intended, "email_domain")
    interval, subdomains = _tier_values(intended)
    country = intended.get("country_code")
    expected: dict[str, Any] = {
        "accountName": name, "accountDomain": domain, "accountType": pc.ACCOUNT_TYPE_ROW,
        "accountCountryCode": country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else pc.SHAPE_ISO2,
        "scanningInterval": interval, "leakedCredentialsScanningInterval": LC_INTERVAL,
        "scanningFrequency": interval, "reconLevel": pc.RECON_LEVEL_ROW,
        "accountLicense.assetsNumber": int(LICENSE_ASSETS_TEXT), "accountLicense.domainsNumber": int(LICENSE_DOMAINS_TEXT),
        "accountLicense.subDomainsNumber": subdomains,
        "accountLicense.leakedCredentialsAllowed": True, "accountLicense.provisioningEnabled": True,
        "accountLicense.notificationsAllowed": True, "accountLicense.multipleUsersAllowed": True,
        "accountLicense.apiAccessAllowed": True, "accountLicense.phishingEnabled": False,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
        "userEmailDomains": [email_domain],
    }
    for flag in pc.ROW_FLAG_PATHS:
        expected[flag] = flag in ("subDomainsReconEnabled", "webDictionaryBruteForceEnabled")
    return expected
