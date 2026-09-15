"""Commercial source-readiness evaluation for new Surface onboarding.

The evaluator is deliberately local and side-effect free.  It distinguishes
commercial readiness from a Salesforce approval or any Leonardo execution
permission.  Callers supply only the minimal, transient source fields needed
for the decision and receive a small, safe operational result.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any, Mapping, Sequence

from phase1_validator.onboarding_comment_dates import extract_dealhub_dates


BASELINE_PATTERN = re.compile(
    r"^pentera surface(?: go)?\s*-\s*(\d+) subdomains$", re.IGNORECASE
)
ADDON_PATTERN = re.compile(
    r"^pentera surface.*(?:add[- ]?on|additional).*?(\d+) subdomains$",
    re.IGNORECASE,
)
ELIGIBLE_STATUSES = frozenset({"active", "pending"})


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
