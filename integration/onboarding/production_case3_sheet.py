"""The Case 3 (Surface & Credential Exposure, New) "Add Account values": what to type in BackOffice production, in form order.

Pure view model, no I/O. Owner decision 2026-10-21 (path A: the owner creates the tenant by hand): the rows are derived from the SAME
fill plan (``build_case3_fill``) that the production dry run typed and verified on 2026-10-21 (32/32 fields), so this card and the dry
run cannot disagree. Settings follow the Guru cards "New Surface Account Only" and "Settings and Toggles" (Automated discovery OFF,
Nuclei ON, Maximum scan Duration 90 h, Static outbound IP only on an explicit request). A plan that is incomplete raises ValueError:
the caller shows "do not onboard" and never a guessed value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ENTER, LEAVE = "enter", "leave"
MAX_SCAN_LABEL = "Maximum scan Duration (hours)"
BACKOFFICE = "app.pentera.io"
STATIC_IP_NOTE = ("Off by default (Guru). Turn it ON only if Salesforce or the contract explicitly requests it; "
                  "the automation never sets it.")


def _row(label: str, value: str, note: str = "", status: str = ENTER) -> dict[str, str]:
    return {"label": label, "value": value, "note": note, "status": status}


def _normalized(texts: Mapping[str, Any]) -> dict[str, str]:
    return {" ".join(str(key).split()): str(value) for key, value in texts.items()}


def _on_off(boxes: Mapping[str, Any], key: str) -> str:
    value = boxes[key]
    if not isinstance(value, bool):
        raise ValueError("plan_shape_unexpected")
    return "On" if value else "Off"


def rows(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    """The ordered rows of the form. Raises ValueError for any plan that lacks a value the form needs (fail closed)."""
    try:
        return _rows(plan)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("plan_shape_unexpected") from exc


def _rows(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    texts, selects, boxes = _normalized(plan["texts"]), plan["selects"], plan["checkboxes"]
    blank = {" ".join(str(label).split()) for label in plan.get("blank_texts") or ()}
    duration = _normalized(plan.get("advanced_texts") or {})[MAX_SCAN_LABEL]

    def optional(label: str, full: str, note: str) -> dict[str, str]:
        if full in texts:
            return _row(label, texts[full], note)
        if full in blank:
            return _row(label, "Leave empty", "", LEAVE)
        raise KeyError(full)

    def toggle(label: str, key: str, note: str = "") -> dict[str, str]:
        return _row(label, _on_off(boxes, key), note)

    return [
        _row("Company name", texts["Company name"], "The account name as in Salesforce. Case 3 has no ' - CE Only' suffix."),
        _row("Account Type", selects["Account Type"]),
        _row("Company primary domain", texts["Company primary domain"], "The main domain: a registered domain, not a subdomain."),
        optional("Alternate Domains", "Alternate Domains (Comma Separated Values)", "Only domains listed on the CO."),
        optional("SubDomains", "SubDomains (Comma Separated Values)", "Only subdomains listed on the CO."),
        _row("User email domains", texts["User email domains (Comma Separated Values)"], "Internal domain, never the customer's."),
        _row("Networks", "Leave empty", "", LEAVE),
        _row("Country", selects["Country"], "As on the Salesforce Account: pick the same country."),
        _row("Primary user", "New User", "Pick New User, not an existing user."),
        _row("First name", texts["First name"]),
        _row("Last name", texts["Last name"]),
        _row("Organization Email", texts["Organization Email"], "Internal address, never a customer address."),
        _row("Phone number", "Leave empty", "", LEAVE),
        _row("Job title", "Leave empty", "", LEAVE),
        _row("MFA required", _on_off(boxes, "mfaRequired"), "Keep the form default ON, always.", LEAVE),
        _row("Operator Account", "The Technical Advisor", "Owner 2026-10-21: pick the Technical Advisor shown on the Salesforce "
             "Account page (the dashboard does not read it yet)."),
        _row("Scanning interval", selects["Scanning interval"], "Go = Monthly, Prime = Weekly."),
        _row("Scan now", "Leave as the form sets it", "Owner 2026-10-21: the manual create sent it ON and a scan started. The dry "
                                                       "run saw no control under a schedule; if it appears, leave it ON.", LEAVE),
        toggle("Notifications", "notificationsAllowed"),
        toggle("Multiple users", "multipleUsersAllowed"),
        toggle("API access", "apiAccessAllowed"),
        toggle("Phishing", "phishingEnabled"),
        toggle("Leaked Credentials", "leakedCredentialsAllowed", "Turn ON: the customer has Credential Exposure."),
        _row("Leaked Credentials scanning interval", selects["Leaked Credentials scanning interval"]),
        _row("Leaked Credentials scanned domains", texts["Leaked Credentials scanned domains (Comma Separated Values)"],
             "The one CE email domain from Salesforce."),
        toggle("Include Provisioning", "provisioningEnabled"),
        toggle("Include subdomains", "subDomainsNumberAllowed"),
        toggle("Automated discovery", "automatedDiscoveryEnabled", "Open Advanced options first. OFF by default (Guru)."),
        toggle("Recon Subdomains", "subDomainsReconEnabled"),
        toggle("Web dictionary brute force", "webDictionaryBruteForceEnabled"),
        toggle("Web dorking", "webDorkingEnabled", "Never enable (Guru: non-functional)."),
        toggle("Nuclei", "fullNucleiScanEnabled"),
        toggle("Authenticated Testing", "authenticatedTestingEnabled"),
        _row("Static outbound IP", _on_off(boxes, "staticOutboundIpEnabled"), STATIC_IP_NOTE),
        toggle("AI", "aiEnabled", "Do not enable: the customer opts in."),
        toggle("Multiple attack stacks", "multipleAttackStacksEnabled"),
        _row(MAX_SCAN_LABEL, duration, "The form starts at 24: change it to " + duration + "."),
        _row("License Type", selects["Type"]),
        _row("Number of assets", texts["Number of assets"]),
        _row("Number of domains", texts["Number of domains"], "Always 10000 for a Surface create (owner 2026-10-21)."),
        _row("Number of subdomains", texts["Number of subdomains"], "From the licence (Go 500, Prime 1000, plus add-ons)."),
        _row("Start date", plan["license_start"].isoformat(), "The day you enter it: if that is not this date, use that day."),
        _row("Expiration date", plan["license_end"].isoformat(), "The Surface subscription end."),
        _row("SpyCloud", "On", "Not on the form: the server sets it. Check it reads ON after the create.", LEAVE),
    ]


def after_steps(tenant_name: str, co: str) -> list[str]:
    """The manual steps around the create, including the HAR capture that teaches the automation the real Case 3 create."""
    return [
        "Before you click Confirm: in Chrome press F12, open the Network tab, tick Preserve log, and keep DevTools open.",
        "Click Confirm in BackOffice yourself, once. Do not click it again whatever happens; tell Claude instead.",
        "When the tenant is created: in the Network tab right-click any request, choose Save all as HAR with content, and save the "
        "file OUTSIDE the repository. It holds your live session: give Claude only the path, never paste it, and delete it afterwards.",
        "Search the new tenant by its exact name: " + tenant_name + ". Open its details and copy the Account ID (UID) and the "
        "Account UUID.",
        "In Salesforce on " + co + ": write the UID into Surface_Account_ID__c and the UUID into Account_UUID__c. Then move the "
        "Onboarding Stage to Account Scanning and Mark as Current Onboarding Stage.",
        "The Surface scan takes 3 hours or longer: the next day confirm it completed with real results (subdomains, findings) "
        "and that the Credential Exposure scan finished.",
        "Then create the primary user (tenant, user management, new admin user) and move the stage to User Created.",
    ]
