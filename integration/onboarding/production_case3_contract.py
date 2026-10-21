"""The Case 3 (Surface & Credential Exposure, New) production create contract, learned from the owner's manual create of CO-0800.

Owner decision 2026-10-21 (path A): the tenant was created by hand and the HAR was read structure-only (``tools/har_contract_reader.py``).
This module is the pure expected-value side of that observation (docs/59): the ``account/add`` body and the create response. It builds
nothing, sends nothing and unlocks nothing; the guarded Case 3 create (path B) is a separate owner decision. Mismatches are reported as
field NAMES only, never values. Compared with the CE-only contract (``production_contract``) the differences are the ones the HAR showed.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from . import production_contract as pc

ACCOUNT_TYPE_CREATE = pc.ACCOUNT_TYPE_CREATE
LICENSE_TYPE = pc.LICENSE_TYPE
SCANNING_INTERVAL = "MONTHLY"          # observed for CO-0800; the tier rule (Monthly/Weekly) is the owner's, see docs/59
LC_INTERVAL = "WEEKLY"                 # owner 2026-10-21: the manual create sent NO_SCHEDULE by mistake; future creates run weekly
CAMPAIGN_TIMEOUT_HOURS = 90            # the form default is 24; the Guru card sets 90
LICENSE_ASSETS_TEXT = "10000"          # strings, as in the CE-only create
LICENSE_DOMAINS_TEXT = "10000"
LICENSE_SUBDOMAINS = 500               # a number; the tier rule 500/1000 is the owner's
OBSERVED_SCAN_NOW = True               # the UI sent scanNow true; whether the automation does too is an OPEN owner decision
RESPONSE_CARRIES_ACCOUNT_IDS = False   # the create response has no Account ID / UUID: read them back from the tenant row

FLAGS_OFF = ("automatedDiscoveryEnabled", "webDorkingEnabled", "staticOutboundIpEnabled", "aiEnabled", "webAiAttackerEnabled",
             "vodBetaEnabled")
FLAGS_ON = ("subDomainsReconEnabled", "webDictionaryBruteForceEnabled", "fullNucleiScanEnabled")
LICENSE_ALLOWS_ON = ("notificationsAllowed", "multipleUsersAllowed", "apiAccessAllowed", "enabled", "provisioningEnabled",
                     "leakedCredentialsAllowed")


def expected_case3_create_payload(intended: Mapping[str, Any]) -> dict[str, Any]:
    """Flat dotted field name -> expected value (or shape) for the ``account/add`` body of a Case 3 New. Raises ValueError (fail closed)."""
    name = pc._text(intended, "tenant_name")
    domain, email_domain, leaked = (pc._text(intended, key) for key in ("primary_domain", "email_domain", "leaked_domain"))
    if name.endswith(pc.NAME_SUFFIX):
        raise ValueError("tenant_name_has_ce_only_suffix")  # Case 3 uses the bare account name
    end = pc._day(intended.get("license_end"))
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
        "scanningInterval": SCANNING_INTERVAL, "scanNow": OBSERVED_SCAN_NOW,
        "leakedCredentialsScanningInterval": LC_INTERVAL, "leakedCredentialsScannedDomains": [leaked],
        "campaignsTimeoutInHours": CAMPAIGN_TIMEOUT_HOURS,
        "license.assetsNumber": LICENSE_ASSETS_TEXT, "license.domainsNumber": LICENSE_DOMAINS_TEXT,
        "license.subDomainsNumber": LICENSE_SUBDOMAINS, "license.licenseType": LICENSE_TYPE,
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


def expected_case3_response(scanning_frequency: str = SCANNING_INTERVAL) -> dict[str, Any]:
    """The create response summary the HAR showed (values that are not identifying)."""
    return {"allowedModules": [], "apiAccess": True, "assetsNumber": 10000, "domainsNumber": 10000,
            "elevatedFeaturesEnabled": False, "leakedCredentials": True, "leakedCredentialsScannedDomainsNumber": 1,
            "licenseStatus": None, "licenseType": LICENSE_TYPE, "multiUsers": True, "notifications": True,
            "phishingEnabled": False, "provisioningEnabled": True, "reconLevel": pc.RECON_LEVEL_ROW,
            "scanQuotaEnforcement": None, "scanningFrequency": scanning_frequency, "subDomainsNumber": LICENSE_SUBDOMAINS,
            "accountName": pc.SHAPE_TEXT, "organizationEmail": pc.SHAPE_TEXT,
            "expirationDate": pc.SHAPE_NUMBER, "startDate": pc.SHAPE_NUMBER}


def compare_case3_response(observed: Any, scanning_frequency: str = SCANNING_INTERVAL) -> list[str]:
    return pc.compare_payload(expected_case3_response(scanning_frequency), observed)


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
    country = intended.get("country_code")
    expected: dict[str, Any] = {
        "accountName": name, "accountDomain": domain, "accountType": pc.ACCOUNT_TYPE_ROW,
        "accountCountryCode": country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else pc.SHAPE_ISO2,
        "scanningInterval": SCANNING_INTERVAL, "leakedCredentialsScanningInterval": LC_INTERVAL,
        "scanningFrequency": SCANNING_INTERVAL, "reconLevel": pc.RECON_LEVEL_ROW,
        "accountLicense.assetsNumber": int(LICENSE_ASSETS_TEXT), "accountLicense.domainsNumber": int(LICENSE_DOMAINS_TEXT),
        "accountLicense.subDomainsNumber": LICENSE_SUBDOMAINS,
        "accountLicense.leakedCredentialsAllowed": True, "accountLicense.provisioningEnabled": True,
        "accountLicense.notificationsAllowed": True, "accountLicense.multipleUsersAllowed": True,
        "accountLicense.apiAccessAllowed": True, "accountLicense.phishingEnabled": False,
        "leakedCredentialsSettings.spyCloudSettings.enabled": True,
        "userEmailDomains": [email_domain],
    }
    for flag in pc.ROW_FLAG_PATHS:
        expected[flag] = flag in ("subDomainsReconEnabled", "webDictionaryBruteForceEnabled")
    return expected
