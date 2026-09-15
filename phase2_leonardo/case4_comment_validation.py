"""Case 4 Onboarding Comments gate without retaining source comment content.

Case 4 is Renew Surface + New Credential Exposure.  This narrow local gate
does not perform the usual existing-account lookup, does not call Salesforce,
and cannot approve or execute onboarding.  Its only purpose is to classify an
available Onboarding Comments value as requiring manual review or as having a
single usable subscription term while the route remains unmapped.
"""
from __future__ import annotations

from phase1_validator.onboarding_comment_dates import extract_dealhub_dates


ENGINE_VALUE = "case_4_renew_surface_new_ce"


def validate_case4_onboarding_comments(onboarding_comments: object, *, source_read_available: bool) -> dict[str, object]:
    """Return a redacted, fail-closed Case-4 comment decision."""
    if source_read_available is not True:
        return {
            "engine_value": ENGINE_VALUE,
            "decision": "manual_review_required",
            "proceed": False,
            "manual_review_required": True,
            "comment_validation_status": "unavailable",
            "error_category": "onboarding_comments_source_unavailable",
        }

    parsed = extract_dealhub_dates(onboarding_comments)
    if parsed["result"] != "dealhub_dates_extracted":
        return {
            "engine_value": ENGINE_VALUE,
            "decision": "manual_review_required",
            "proceed": False,
            "manual_review_required": True,
            "comment_validation_status": "invalid_or_ambiguous",
            "error_category": parsed["error_category"],
        }

    return {
        "engine_value": ENGINE_VALUE,
        "decision": "case_not_mapped_yet",
        "proceed": False,
        "manual_review_required": False,
        "comment_validation_status": "single_date_range_present",
        "error_category": "",
    }
