"""Strict in-memory contract for one read-only CO-0717 Salesforce detail view.

This module has no Salesforce client, persistence, logging, listener, or
credential access. A future SSO-protected runtime may pass it the response of
the single approved source query. The static preview must never call it.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import re


CO_NUMBER = "CO-0717"
DETAIL_QUERY_FIELDS = (
    "Name",
    "LastModifiedDate",
    "Account_Name__c",
    "Onboarding_Product__c",
    "Onboarding_Type__c",
    "Primary_User_Name__c",
    "Main_Domain__c",
    "Alternate_Domains__c",
    "Email_Domains__c",
    "Onboarding_Comments__c",
    "Onboarding_Stage__c",
    "Surface_Account_ID__c",
    "Account_UUID__c",
)
DISPLAY_FIELDS = (
    ("Account", "Account_Name__c"),
    ("Onboarding Product", "Onboarding_Product__c"),
    ("Onboarding Type", "Onboarding_Type__c"),
    ("Primary User", "Primary_User_Name__c"),
    ("Main Domain", "Main_Domain__c"),
    ("Alternative Domains", "Alternate_Domains__c"),
    ("Email Domains", "Email_Domains__c"),
    ("Onboarding Comments", "Onboarding_Comments__c"),
    ("Onboarding Stage", "Onboarding_Stage__c"),
    ("Surface Account ID", "Surface_Account_ID__c"),
    ("Account UUID", "Account_UUID__c"),
)
_MAX_VALUE_LENGTH = 32_768
_REVISION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{4}$")


class DuplicateJsonKeyError(ValueError):
    """Raised before a duplicate source key can be silently accepted."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise DuplicateJsonKeyError("duplicate_source_key")
        record[key] = value
    return record


@dataclass(frozen=True, slots=True)
class CustomerOnboardingDetail:
    """Transient customer detail; callers must not log or persist this object."""

    source_revision: str
    values: tuple[tuple[str, str | None], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_revision, str) or not _REVISION_PATTERN.fullmatch(self.source_revision):
            raise ValueError("invalid_salesforce_detail_revision")
        if tuple(key for key, _ in self.values) != DETAIL_QUERY_FIELDS[2:]:
            raise ValueError("invalid_salesforce_detail_fields")
        for _, value in self.values:
            if value is not None and (not isinstance(value, str) or len(value) > _MAX_VALUE_LENGTH):
                raise ValueError("invalid_salesforce_detail_value")

    def value_for(self, source_field: str) -> str | None:
        return dict(self.values)[source_field]


def parse_co_0717_detail_document(document: str) -> CustomerOnboardingDetail:
    """Parse exactly one transient CO-0717 result and fail closed on any drift."""
    if not isinstance(document, str):
        raise ValueError("invalid_salesforce_detail_document")
    try:
        decoded = json.loads(document, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, DuplicateJsonKeyError) as error:
        raise ValueError("invalid_salesforce_detail_document") from error
    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
        raise ValueError("ambiguous_salesforce_detail_result")
    record = decoded[0]
    if tuple(record) != DETAIL_QUERY_FIELDS or record["Name"] != CO_NUMBER:
        raise ValueError("unexpected_salesforce_detail_schema")
    revision = record["LastModifiedDate"]
    values: list[tuple[str, str | None]] = []
    for field in DETAIL_QUERY_FIELDS[2:]:
        value = record[field]
        if value is not None and not isinstance(value, str):
            raise ValueError("unexpected_salesforce_detail_value")
        values.append((field, value))
    return CustomerOnboardingDetail(revision, tuple(values))  # type: ignore[arg-type]


def render_attended_co_0717_detail(detail: CustomerOnboardingDetail) -> str:
    """Render one transient detail response for the desktop loopback viewer only."""
    if not isinstance(detail, CustomerOnboardingDetail):
        raise ValueError("invalid_salesforce_detail")
    rows = "".join(
        "<dt>{label}</dt><dd>{value}</dd>".format(
            label=escape(label),
            value=escape(detail.value_for(source_field)) if detail.value_for(source_field) is not None
            else '<span class="empty">Not populated</span>',
        )
        for label, source_field in DISPLAY_FIELDS
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width, initial-scale=1"><title>CO-0717 — attended Salesforce view</title><style>:root{{color-scheme:dark}}body{{margin:0;background:#09111f;color:#e7eefb;font:16px/1.5 system-ui,sans-serif}}main{{max-width:1040px;margin:auto;padding:38px 26px}}.eyebrow{{color:#4fd1a4;font-weight:700;font-size:.78rem;letter-spacing:.08em;text-transform:uppercase}}h1{{margin:8px 0}}.notice,.panel{{border:1px solid #263755;border-radius:12px;background:#111c2e}}.notice{{margin:22px 0;padding:14px 16px;color:#cfe4ff}}.panel{{padding:20px}}dl{{display:grid;grid-template-columns:minmax(180px,30%) 1fr;margin:0}}dt{{color:#9db0ce;padding:13px 18px 13px 0;border-bottom:1px solid #263755;font-size:.84rem;text-transform:uppercase;letter-spacing:.05em}}dd{{margin:0;padding:13px 0;border-bottom:1px solid #263755;white-space:pre-wrap;overflow-wrap:anywhere}}dt:last-of-type,dd:last-child{{border-bottom:0}}.empty{{color:#9db0ce;font-style:italic}}footer{{color:#9db0ce;font-size:.85rem;margin-top:22px}}@media(max-width:650px){{dl{{grid-template-columns:1fr}}dt{{border-bottom:0;padding-bottom:0}}dd{{padding-top:3px}}}}</style></head><body><main><div class="eyebrow">Attended · read-only · localhost only</div><h1>Customer Onboarding CO-0717</h1><div class="notice">This page is fetched from Salesforce for this request only. It is not stored, cached, forwarded to the VM, or connected to Leonardo.</div><section class="panel"><dl>{rows}</dl></section><footer>Close the viewer when finished. Salesforce writes, queue access, search, and workflow actions are unavailable.</footer></main></body></html>"""
