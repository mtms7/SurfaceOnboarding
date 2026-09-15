"""Offline readiness reporting for explicitly approved integration gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from .config import AppConfig, TargetEnvironment
from .mappings import MappingRegistration
from .models import EngineValue
from .secrets import SecretProvider, SecretProviderStatus


class ReadinessGate(StrEnum):
    DEVELOPMENT_TARGET_ONLY = "development_target_only"
    HOST_DEPLOYMENT_APPROVED = "host_deployment_approved"
    POLL_SCHEDULE_DEFINED = "poll_schedule_defined"
    WEB_IDENTITY_APPROVED = "web_identity_approved"
    PERSISTENCE_OPERATIONS_APPROVED = "persistence_operations_approved"
    SALESFORCE_READ_IDENTITY_APPROVED = "salesforce_read_identity_approved"
    LEONARDO_AUTH_APPROVED = "leonardo_auth_approved"
    SECRETS_PROVIDER_READY = "secrets_provider_ready"
    ALL_ROUTE_MAPPINGS_APPROVED = "all_route_mappings_approved"


@dataclass(frozen=True, slots=True)
class ApprovalGates:
    """Explicit owner decisions; defaults are deliberately not approved."""

    host_deployment_approved: bool = False
    web_identity_approved: bool = False
    persistence_operations_approved: bool = False
    salesforce_read_identity_approved: bool = False
    leonardo_auth_approved: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    gate: ReadinessGate
    ready: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Safe report of local configuration and explicit approval gaps."""

    items: tuple[ReadinessItem, ...]

    @property
    def ready(self) -> bool:
        return all(item.ready for item in self.items)

    @property
    def unmet_gates(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if not item.ready)


def readiness_report(config: AppConfig, *, approvals: ApprovalGates,
                     mappings: Mapping[EngineValue, MappingRegistration],
                     secret_provider: SecretProvider) -> ReadinessReport:
    """Report known gaps without probing an external system or resolving a secret."""
    all_mappings_ready = set(mappings) == set(EngineValue) and all(
        registration.enabled for registration in mappings.values()
    )
    secret_status = secret_provider.health()
    return ReadinessReport((
        ReadinessItem(ReadinessGate.DEVELOPMENT_TARGET_ONLY,
                      config.target_environment is TargetEnvironment.LEONARDO_DEVELOPMENT,
                      None if config.target_environment is TargetEnvironment.LEONARDO_DEVELOPMENT
                      else "production_and_unknown_targets_are_blocked"),
        ReadinessItem(ReadinessGate.HOST_DEPLOYMENT_APPROVED,
                      approvals.host_deployment_approved,
                      None if approvals.host_deployment_approved else "vm_and_secops_approval_required"),
        ReadinessItem(ReadinessGate.POLL_SCHEDULE_DEFINED, True),
        ReadinessItem(ReadinessGate.WEB_IDENTITY_APPROVED, approvals.web_identity_approved,
                      None if approvals.web_identity_approved else "owner_approval_required"),
        ReadinessItem(ReadinessGate.PERSISTENCE_OPERATIONS_APPROVED,
                      approvals.persistence_operations_approved,
                      None if approvals.persistence_operations_approved
                      else "database_backup_and_operations_approval_required"),
        ReadinessItem(ReadinessGate.SALESFORCE_READ_IDENTITY_APPROVED,
                      approvals.salesforce_read_identity_approved,
                      None if approvals.salesforce_read_identity_approved else "owner_approval_required"),
        ReadinessItem(ReadinessGate.LEONARDO_AUTH_APPROVED, approvals.leonardo_auth_approved,
                      None if approvals.leonardo_auth_approved else "owner_approval_required"),
        ReadinessItem(ReadinessGate.SECRETS_PROVIDER_READY,
                      secret_status is not SecretProviderStatus.NOT_CONFIGURED,
                      None if secret_status is not SecretProviderStatus.NOT_CONFIGURED
                      else "secret_provider_not_configured"),
        ReadinessItem(ReadinessGate.ALL_ROUTE_MAPPINGS_APPROVED, all_mappings_ready,
                      None if all_mappings_ready else "route_mapping_approval_required"),
    ))
