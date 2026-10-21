"""The Case 3 "Tenant creation summary": the few Salesforce facts the create needs and the values that go into the tenant.

Pure view model, no I/O (owner request 2026-10-21). It reads the SAME fill plan as the dry run and the values card, so the three
cannot disagree. It shows plan values as they are and never changes them; where an owner rule differs from the plan it adds an
"attention" line instead. A plan that is incomplete raises ValueError (fail closed).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OWNER_DOMAINS_RULE = "10000"  # owner 2026-10-21: assets 10000, domains 10000, subdomains from the Surface subscription
TIER_BY_INTERVAL = {"Monthly": "Go", "Weekly": "Prime"}
ON_NAMES = (("subDomainsReconEnabled", "Recon subdomains"), ("webDictionaryBruteForceEnabled", "Web dictionary brute force"),
            ("fullNucleiScanEnabled", "Nuclei"))
OFF_NAMES = (("automatedDiscoveryEnabled", "Automated discovery"), ("staticOutboundIpEnabled", "Static outbound IP"),
             ("webDorkingEnabled", "Web dorking"), ("aiEnabled", "AI"), ("authenticatedTestingEnabled", "Authenticated Testing"),
             ("multipleAttackStacksEnabled", "Multiple attack stacks"))
TA_TEXT = "Open the Account in Salesforce and read it there (not read by the dashboard yet)"


def _normalized(texts: Mapping[str, Any]) -> dict[str, str]:
    return {" ".join(str(key).split()): str(value) for key, value in texts.items()}


def _flag(boxes: Mapping[str, Any], key: str, want: bool) -> bool:
    value = boxes[key]
    if not isinstance(value, bool):
        raise ValueError("plan_shape_unexpected")
    return value is want


def build(plan: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return _build(plan)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("plan_shape_unexpected") from exc


def _build(plan: Mapping[str, Any]) -> dict[str, Any]:
    texts, selects, boxes = _normalized(plan["texts"]), plan["selects"], plan["checkboxes"]
    duration = _normalized(plan["advanced_texts"])["Maximum scan Duration (hours)"]
    interval, start, end = selects["Scanning interval"], plan["license_start"].isoformat(), plan["license_end"].isoformat()
    ce_domain = texts["Leaked Credentials scanned domains (Comma Separated Values)"]
    domains = texts["Number of domains"]
    on = [label for key, label in ON_NAMES if _flag(boxes, key, True)]
    off = [label for key, label in OFF_NAMES if _flag(boxes, key, False)]
    if len(on) != len(ON_NAMES) or len(off) != len(OFF_NAMES):
        raise ValueError("plan_switch_unexpected")  # a switch outside the Guru profile is never summarised as normal
    salesforce = [
        ("Account name", texts["Company name"]), ("Main domain", texts["Company primary domain"]),
        ("Credential Exposure email domain", ce_domain), ("Country", selects["Country"]),
        ("Surface plan", "%s (%s)" % (TIER_BY_INTERVAL.get(interval, "Unknown"), interval)),
        ("License end (Surface subscription end)", end), ("Technical Advisor", TA_TEXT)]
    tenant = [
        ("Tenant name", texts["Company name"]), ("Primary domain", texts["Company primary domain"]),
        ("Scanning interval", interval),
        ("Scan now", "Left as the form sets it (the manual create sent ON, so a scan starts at create)"),
        ("Maximum scan duration", duration + " hours"),
        ("Leaked Credentials", "On, %s, domain %s" % (selects["Leaked Credentials scanning interval"], ce_domain)),
        ("Licence", "%s, assets %s, domains %s, subdomains %s" % (selects["Type"], texts["Number of assets"], domains,
                                                                  texts["Number of subdomains"])),
        ("Licence dates", start + " to " + end),
        ("Advanced switches", "On: " + ", ".join(on) + ". Off: " + ", ".join(off)),
        ("Operator Account", "The Technical Advisor of the Account in Salesforce"),
        ("MFA", "On" if _flag(boxes, "mfaRequired", True) else "Off"),
        ("Primary user email", texts["Organization Email"])]
    attention = ["Operator Account: the Technical Advisor is read by hand from the Account page; confirm it before you click Confirm."]
    if domains != OWNER_DOMAINS_RULE:
        attention.append("Number of domains: the plan says %s but the owner rule of 2026-10-21 is %s; type %s and do not trust the "
                         "plan value until the runner rule is updated." % (domains, OWNER_DOMAINS_RULE, OWNER_DOMAINS_RULE))
    return {"salesforce": salesforce, "tenant": tenant, "attention": attention}
