"""Large-scope policy operating only on counts, never domains or contacts."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ReasonCode

MANUAL_REVIEW_LIMIT = 60


@dataclass(frozen=True, slots=True)
class ScopeCounts:
    root_domains: int
    subdomains: int
    licensed_domains: int
    licensed_subdomains: int

    def __post_init__(self) -> None:
        if any(value < 0 for value in (self.root_domains, self.subdomains, self.licensed_domains, self.licensed_subdomains)):
            raise ValueError("scope_counts_must_be_nonnegative")


def manual_review_reason(counts: ScopeCounts, *, policy_flag: bool = False) -> ReasonCode | None:
    """Return a stable non-sensitive reason; equality with 60 remains eligible."""
    if policy_flag or any(value > MANUAL_REVIEW_LIMIT for value in (
        counts.root_domains,
        counts.subdomains,
        counts.root_domains + counts.subdomains,
        counts.licensed_domains,
        counts.licensed_subdomains,
    )):
        return ReasonCode.MANUAL_REVIEW_REQUIRED
    return None
