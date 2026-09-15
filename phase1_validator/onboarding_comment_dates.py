"""Fail-closed extraction of DealHub subscription dates from an onboarding comment.

The function is intentionally local and side-effect free.  It returns only
normalised dates and a small operational state; it never returns, logs, or
persists the source comment.  A valid date range makes a CO ready for *CSE
manual validation*, not for automated provisioning or Salesforce writeback.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Final


MAX_COMMENT_CHARS: Final = 32_768
POLICY_VERSION: Final = "onboarding-comment-dates-v1"
DATE_RANGE_PATTERN: Final = re.compile(
    r"(?<!\d)(\d{4}-\d{2}-\d{2})\s*(?:-|–|—)\s*(\d{4}-\d{2}-\d{2})(?!\d)"
)


def _blocked(error_category: str) -> dict[str, object]:
    """Return a stable, non-sensitive fail-closed response."""
    return {
        "result": "dealhub_dates_unverified",
        "proceed": False,
        "eligible_for_automation": False,
        "queue_status": "blocked",
        "ready_for_cse_review": False,
        "action_required": "cse_manual_validation",
        "dealhub_start_date": "",
        "dealhub_end_date": "",
        "dealhub_date_parse_status": "unverified",
        "dealhub_validation_status": "not_confirmed",
        "error_category": error_category,
        "blocked_reason_masked": (
            "CSE manual validation required: DealHub subscription dates in "
            "Onboarding Comments are missing, ambiguous, or invalid."
        ),
        "policy_version": POLICY_VERSION,
    }


def extract_dealhub_dates(onboarding_comments: object) -> dict[str, object]:
    """Extract one valid ISO date range without returning the source comment.

    A single valid ``YYYY-MM-DD - YYYY-MM-DD`` range is treated as the
    business-approved evidence that the DealHub subscription-date validation
    has been completed.  The resulting CO is still held for CSE manual
    validation.  Missing, malformed, multiple, or reversed ranges block.
    """
    if onboarding_comments is None:
        return _blocked("onboarding_comments_missing")
    if not isinstance(onboarding_comments, str):
        return _blocked("onboarding_comments_invalid_type")
    if not onboarding_comments.strip():
        return _blocked("onboarding_comments_missing")
    if len(onboarding_comments) > MAX_COMMENT_CHARS:
        return _blocked("onboarding_comments_too_large")

    matches = DATE_RANGE_PATTERN.findall(onboarding_comments)
    if not matches:
        return _blocked("dealhub_date_range_missing")
    if len(matches) != 1:
        return _blocked("dealhub_date_range_ambiguous")

    start_date, end_date = matches[0]
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return _blocked("dealhub_date_range_invalid")
    if end <= start:
        return _blocked("dealhub_date_range_invalid")

    return {
        "result": "dealhub_dates_extracted",
        "proceed": False,
        "eligible_for_automation": False,
        "queue_status": "ready_for_cse_review",
        "ready_for_cse_review": True,
        "action_required": "cse_manual_validation",
        "dealhub_start_date": start.isoformat(),
        "dealhub_end_date": end.isoformat(),
        "dealhub_date_parse_status": "verified_from_comment",
        "dealhub_validation_status": "validated_from_onboarding_comment",
        "error_category": "",
        "blocked_reason_masked": "",
        "policy_version": POLICY_VERSION,
    }
