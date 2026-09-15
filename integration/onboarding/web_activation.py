"""Offline-only, fail-closed evidence contract for any future web activation.

This module validates non-sensitive approval references only. It cannot load a
secret, inspect a user, authenticate a request, start a listener, or change a
host. The current app factory remains independently blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re

from .config import TargetEnvironment
from .deployment import HostDeploymentProfile
from .secrets import SecretProviderStatus


class WebApprovalControl(StrEnum):
    HOST_SECOPS = "host_secops"
    TLS_SSO_MFA = "tls_sso_mfa"
    PILOT_ACCESS = "pilot_access"
    SECRET_PROVIDER = "secret_provider"
    OPERATIONS = "operations"
    DEVELOPMENT_SCOPE = "development_scope"


REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{5,63}$")
MAX_APPROVAL_LIFETIME = timedelta(days=31)
PILOT_OPERATOR_COUNT = 5


@dataclass(frozen=True, slots=True)
class ApprovalReference:
    """Opaque, non-personal approval metadata with a short validity period."""

    control: WebApprovalControl
    reference: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.control, WebApprovalControl):
            raise ValueError("invalid_web_approval_control")
        if not isinstance(self.reference, str) or not REFERENCE_PATTERN.fullmatch(self.reference):
            raise ValueError("invalid_web_approval_reference")
        if any(value.tzinfo is None or value.utcoffset() is None
               for value in (self.issued_at, self.expires_at)):
            raise ValueError("web_approval_time_must_be_timezone_aware")
        if self.expires_at <= self.issued_at or self.expires_at - self.issued_at > MAX_APPROVAL_LIFETIME:
            raise ValueError("invalid_web_approval_lifetime")

    def valid_at(self, when: datetime) -> bool:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("web_approval_time_must_be_timezone_aware")
        return self.issued_at <= when < self.expires_at


@dataclass(frozen=True, slots=True)
class WebActivationAttestation:
    """Future activation evidence; validation cannot override the listener block."""

    host: HostDeploymentProfile
    target_environment: TargetEnvironment
    pilot_group_reference: str
    pilot_operator_count: int
    secret_provider_status: SecretProviderStatus
    approvals: tuple[ApprovalReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.host, HostDeploymentProfile):
            raise ValueError("invalid_web_activation_host")
        if self.target_environment is not TargetEnvironment.LEONARDO_DEVELOPMENT:
            raise ValueError("web_activation_development_only")
        if not isinstance(self.pilot_group_reference, str) or not REFERENCE_PATTERN.fullmatch(self.pilot_group_reference):
            raise ValueError("invalid_pilot_group_reference")
        if self.pilot_operator_count != PILOT_OPERATOR_COUNT:
            raise ValueError("web_activation_requires_exact_pilot_count")
        if self.secret_provider_status is not SecretProviderStatus.READY:
            raise ValueError("web_activation_requires_ready_secret_provider")
        controls = tuple(approval.control for approval in self.approvals)
        if set(controls) != set(WebApprovalControl) or len(controls) != len(set(controls)):
            raise ValueError("web_activation_requires_each_current_approval")

    def validate_at(self, when: datetime) -> None:
        if not all(approval.valid_at(when) for approval in self.approvals):
            raise ValueError("web_activation_approval_expired_or_not_yet_valid")
