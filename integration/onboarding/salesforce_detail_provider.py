"""Non-callable boundary for the future CO-0717-only Salesforce detail reader.

It intentionally exposes neither a generic query method nor any mutation.
Implementations are blocked until Salesforce, identity, and security approvals
are recorded outside the repository.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from .salesforce_detail import CustomerOnboardingDetail


class SalesforceDetailProviderStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    READY = "ready"


class SalesforceDetailProviderBlockedError(RuntimeError):
    """Raised without source data when the provider is not explicitly approved."""


class SalesforceDetailProvider(Protocol):
    """A future provider is limited to one fixed, read-only source scope."""

    def health(self) -> SalesforceDetailProviderStatus: ...

    def read_co_0717(self) -> CustomerOnboardingDetail: ...


class DisabledSalesforceDetailProvider:
    """Default provider: no token, connection, query, or source access exists."""

    def health(self) -> SalesforceDetailProviderStatus:
        return SalesforceDetailProviderStatus.NOT_CONFIGURED

    def read_co_0717(self) -> CustomerOnboardingDetail:
        raise SalesforceDetailProviderBlockedError("salesforce_detail_provider_not_configured")
