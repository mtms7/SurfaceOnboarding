"""Transient, fail-closed matching policy for Leonardo tenant discovery.

The caller supplies data only for the duration of one attended lookup.  This
module deliberately has no persistence, logging, or Leonardo client.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import unicodedata


class TenantMatchDisposition(StrEnum):
    NO_CANDIDATE = "no_candidate"
    BLOCK_CREATE = "block_create"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class TenantSearchCandidate:
    """One transient row returned by the attended Leonardo search."""

    company_name: str | None
    main_domain: str | None


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def canonical_domain(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip().casefold().rstrip(".")
    if not result or any(character.isspace() for character in result):
        return None
    return result


def normalized_company_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_folded = "".join(character for character in decomposed if not unicodedata.combining(character))
    tokens = _TOKEN_RE.findall(ascii_folded.casefold())
    return " ".join(tokens) or None


def _meaningful_prefix_match(left: str, right: str) -> bool:
    """A partial company name is never proof; it only requires human review."""
    left_tokens = left.split()
    right_tokens = right.split()
    shared = 0
    for left_token, right_token in zip(left_tokens, right_tokens):
        if left_token != right_token:
            break
        shared += 1
    return shared >= 2


def classify_tenant_candidate(
    source_company_name: str | None,
    source_main_domain: str | None,
    candidate: TenantSearchCandidate,
) -> TenantMatchDisposition:
    """Classify a discovered tenant candidate without authorizing creation.

    An exact main-domain or normalized company-name match blocks creation.
    A meaningful partial/abbreviated name is ambiguous and must be reviewed;
    a short initialism alone can never clear or block a create automatically.
    """
    source_domain = canonical_domain(source_main_domain)
    candidate_domain = canonical_domain(candidate.main_domain)
    if source_domain and candidate_domain and source_domain == candidate_domain:
        return TenantMatchDisposition.BLOCK_CREATE

    source_name = normalized_company_name(source_company_name)
    candidate_name = normalized_company_name(candidate.company_name)
    if source_name and candidate_name:
        if source_name == candidate_name:
            return TenantMatchDisposition.BLOCK_CREATE
        if _meaningful_prefix_match(source_name, candidate_name) or _meaningful_prefix_match(candidate_name, source_name):
            return TenantMatchDisposition.MANUAL_REVIEW
        initialism = "".join(token[0] for token in source_name.split())
        if len(initialism) >= 2 and candidate_name.replace(" ", "") == initialism:
            return TenantMatchDisposition.MANUAL_REVIEW
    return TenantMatchDisposition.NO_CANDIDATE


def evaluate_duplicate_preflight(
    *,
    exact_domain_searched: bool,
    full_name_searched: bool,
    normalized_name_searched: bool,
    candidate_dispositions: tuple[TenantMatchDisposition, ...],
) -> TenantMatchDisposition:
    """Aggregate a redacted attended search outcome.

    This accepts outcome labels rather than tenant rows, source values, or
    credentials so it can be safely passed to a higher-level local gate.
    Every required lookup must be completed before a no-candidate result can
    be returned. A malformed label fails closed as manual review.
    """
    if not all(
        type(flag) is bool and flag
        for flag in (exact_domain_searched, full_name_searched, normalized_name_searched)
    ):
        return TenantMatchDisposition.MANUAL_REVIEW
    if any(item is TenantMatchDisposition.BLOCK_CREATE for item in candidate_dispositions):
        return TenantMatchDisposition.BLOCK_CREATE
    if any(item is TenantMatchDisposition.MANUAL_REVIEW for item in candidate_dispositions):
        return TenantMatchDisposition.MANUAL_REVIEW
    if not all(isinstance(item, TenantMatchDisposition) for item in candidate_dispositions):
        return TenantMatchDisposition.MANUAL_REVIEW
    return TenantMatchDisposition.NO_CANDIDATE
