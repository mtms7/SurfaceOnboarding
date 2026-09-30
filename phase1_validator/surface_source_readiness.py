"""Commercial source-readiness evaluation for new Surface onboarding.

The evaluator is deliberately local and side-effect free.  It distinguishes
commercial readiness from a Salesforce approval or any Leonardo execution
permission.  Callers supply only the minimal, transient source fields needed
for the decision and receive a small, safe operational result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Mapping, Sequence

from phase1_validator.onboarding_comment_dates import extract_dealhub_dates


BASELINE_PATTERN = re.compile(
    r"^pentera surface(?: go| prime)?\s*-\s*(\d+) subdomains$", re.IGNORECASE
)
ADDON_PATTERN = re.compile(
    r"^pentera surface.*(?:add[- ]?on|additional).*?(\d+) subdomains$",
    re.IGNORECASE,
)
ELIGIBLE_STATUSES = frozenset({"active", "pending"})

# Tier-aware product taxonomy for the attended Surface-only route (Case 1).
# Product-name shapes observed in Salesforce DealHub rows (2026-09-29):
#   newer:  "Pentera Surface Go - 500 Subdomains", "Pentera Surface Prime - 1000 Subdomains"
#   older:  "Pentera Surface Software - Essentials - 150 Sub-Domains & 1 Domains",
#           "Pentera Surface Software Professional - 150 Sub-Domains & 3 Domains",
#           "Pentera Surface Software Enterprise - Up to 650 Sub-Domains & 5 Domains"
#   add-on: "Pentera Surface Software Enterprise - Sub-Domain Add-on - 1 Bulks of 400 Sub-Domains",
#           "Pentera Surface Add-on - Additional 250 Subdomains",
#           "... - Domain Add-on - 1 bulks of 10 Domains" (adds no subdomains)
# Anything else that starts with "Pentera Surface" is unrecognized (fail closed).
# Owner decision 2026-09-29: a Prime product without a number is 1000 subdomains.
PRIME_DEFAULT_SUBDOMAINS = 1000
SURFACE_TIER_SCANNING_INTERVALS = {
    "prime": "Weekly",
    "go": "Monthly",
    "enterprise": "Weekly",
    "essentials": "Monthly",
    "professional": "Monthly",
}
_NEW_BASELINE = re.compile(
    r"^pentera surface (go|prime)(?:\s*-\s*(\d+)\s*sub-?domains)?$", re.IGNORECASE)
_LEGACY_BASELINE = re.compile(
    r"^pentera surface software\s*(?:-\s*)?(essentials?|professional|enterprise)\s*-\s*"
    r"(?:up to\s+)?(\d+)\s*sub-?domains\s*&\s*(\d+)\s*domains?$", re.IGNORECASE)
_SUBDOMAIN_BULK_ADDON = re.compile(
    r"^pentera surface.*?\bsub-?domains?\s+add[- ]?on\s*-\s*(\d+)\s+bulks?\s+of\s+(\d+)\s*sub-?domains$",
    re.IGNORECASE)
_DOMAIN_BULK_ADDON = re.compile(
    r"^pentera surface.*?\bdomains?\s+add[- ]?on\s*-\s*(\d+)\s+bulks?\s+of\s+(\d+)\s*domains?$",
    re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SurfaceProduct:
    """One classified DealHub product name (no customer data).

    ``kind`` is "baseline", "subdomain_addon", "domain_addon" or
    "unrecognized". ``subdomains`` is the licensed subdomain quantity the row
    contributes; ``domains`` is the product-name domain count (older baseline
    and domain add-on rows only).
    """

    kind: str
    tier: str | None = None
    subdomains: int = 0
    domains: int | None = None


def classify_surface_product(name: object) -> SurfaceProduct | None:
    """Classify one product name; None when it is not a "Pentera Surface" row."""
    if not isinstance(name, str):
        return None
    text = " ".join(name.split())
    if not text.casefold().startswith("pentera surface"):
        return None
    match = _NEW_BASELINE.fullmatch(text)
    if match:
        tier = match.group(1).casefold()
        if match.group(2) is not None:
            return SurfaceProduct("baseline", tier, int(match.group(2)))
        if tier == "prime":
            return SurfaceProduct("baseline", tier, PRIME_DEFAULT_SUBDOMAINS)
        return SurfaceProduct("unrecognized")
    match = _LEGACY_BASELINE.fullmatch(text)
    if match:
        tier = match.group(1).casefold()
        tier = "essentials" if tier == "essential" else tier
        return SurfaceProduct("baseline", tier, int(match.group(2)), int(match.group(3)))
    match = _SUBDOMAIN_BULK_ADDON.fullmatch(text)
    if match:
        return SurfaceProduct("subdomain_addon", None, int(match.group(1)) * int(match.group(2)))
    match = ADDON_PATTERN.fullmatch(text)
    if match:
        return SurfaceProduct("subdomain_addon", None, int(match.group(1)))
    match = _DOMAIN_BULK_ADDON.fullmatch(text)
    if match:
        return SurfaceProduct("domain_addon", None, 0, int(match.group(1)) * int(match.group(2)))
    match = BASELINE_PATTERN.fullmatch(text)
    if match:
        # A tierless baseline ("Pentera Surface - 1000 Subdomains") has no
        # owner-approved scanning interval.
        return SurfaceProduct("baseline", None, int(match.group(1)))
    return SurfaceProduct("unrecognized")


def surface_scanning_interval(tier: str | None) -> str | None:
    """Owner-approved Scanning interval for a baseline tier; None when unknown."""
    return SURFACE_TIER_SCANNING_INTERVALS.get(tier or "")


def _manual_review(reason: str) -> dict[str, object]:
    return {
        "commercial_ready": False,
        "manual_review_required": True,
        "reason": reason,
        "baseline_subdomains": 0,
        "addon_subdomains": 0,
        "total_subdomains": 0,
    }


def _row_value(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    return value.strip() if isinstance(value, str) else ""


def evaluate_new_surface_source(
    *,
    onboarding_comments: object,
    main_domain: object,
    subscriptions: Sequence[Mapping[str, Any]],
    today: date | None = None,
) -> dict[str, object]:
    """Evaluate the owner-approved commercial readiness rule.

    A pending subscription is eligible when its start month is the current
    month or earlier.  This allows tenant preparation before access is granted
    around the contractual start date.  An existing comment is never treated
    as permission to overwrite Salesforce; it routes to manual review.
    """
    if not isinstance(main_domain, str) or not main_domain.strip():
        return _manual_review("main_domain_missing")

    current = today or date.today()
    baselines: list[tuple[int, date, date]] = []
    addons: list[int] = []
    for row in subscriptions:
        product = _row_value(row, "product_full_name")
        status = _row_value(row, "status").casefold()
        start_text = _row_value(row, "start_date")
        end_text = _row_value(row, "end_date")
        if not product:
            return _manual_review("subscription_product_missing")
        baseline = BASELINE_PATTERN.fullmatch(product)
        addon = ADDON_PATTERN.fullmatch(product)
        if baseline is None and addon is None:
            if "pentera surface" in product.casefold():
                return _manual_review("surface_product_unrecognized")
            continue
        if status not in ELIGIBLE_STATUSES:
            return _manual_review("surface_subscription_not_current")
        try:
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        except ValueError:
            return _manual_review("surface_subscription_dates_invalid")
        if end <= start or (start.year, start.month) > (current.year, current.month):
            return _manual_review("surface_subscription_not_current")
        if baseline is not None:
            baselines.append((int(baseline.group(1)), start, end))
        else:
            addons.append(int(addon.group(1)))

    if len(baselines) != 1:
        return _manual_review("surface_baseline_missing_or_ambiguous")

    baseline_subdomains, _start, _end = baselines[0]
    result: dict[str, object] = {
        "commercial_ready": True,
        "manual_review_required": False,
        "reason": "",
        "baseline_subdomains": baseline_subdomains,
        "addon_subdomains": sum(addons),
        "total_subdomains": baseline_subdomains + sum(addons),
    }
    parsed_comments = extract_dealhub_dates(onboarding_comments)
    if onboarding_comments not in (None, ""):
        result["manual_review_required"] = True
        result["reason"] = (
            "onboarding_comments_present"
            if parsed_comments["ready_for_cse_review"]
            else "onboarding_comments_require_review"
        )
    return result
