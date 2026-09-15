from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import unittest

from integration.onboarding.config import TargetEnvironment
from integration.onboarding.deployment import HostDeploymentProfile, HostPlatform
from integration.onboarding.secrets import SecretProviderStatus
from integration.onboarding.web_activation import (ApprovalReference, WebActivationAttestation,
                                                   WebApprovalControl)


NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


def approval(control: WebApprovalControl) -> ApprovalReference:
    return ApprovalReference(control, f"APPROVAL_{control.name}", NOW - timedelta(hours=1),
                             NOW + timedelta(days=1))


def attestation() -> WebActivationAttestation:
    return WebActivationAttestation(
        HostDeploymentProfile("workato-opa-01", HostPlatform.UBUNTU_2204),
        TargetEnvironment.LEONARDO_DEVELOPMENT,
        "PILOT_GROUP_01",
        5,
        SecretProviderStatus.READY,
        tuple(approval(control) for control in WebApprovalControl),
    )


class WebActivationAttestationTests(unittest.TestCase):
    def test_complete_current_development_attestation_validates(self):
        attestation().validate_at(NOW)

    def test_missing_or_duplicate_approval_fails_closed(self):
        complete = attestation()
        with self.assertRaisesRegex(ValueError, "requires_each"):
            replace(complete, approvals=complete.approvals[:-1])
        with self.assertRaisesRegex(ValueError, "requires_each"):
            replace(complete, approvals=complete.approvals + (complete.approvals[0],))

    def test_exact_pilot_count_ready_provider_and_development_target_are_required(self):
        complete = attestation()
        with self.assertRaisesRegex(ValueError, "exact_pilot"):
            replace(complete, pilot_operator_count=4)
        with self.assertRaisesRegex(ValueError, "ready_secret"):
            replace(complete, secret_provider_status=SecretProviderStatus.NOT_CONFIGURED)
        with self.assertRaisesRegex(ValueError, "development_only"):
            replace(complete, target_environment="production")  # type: ignore[arg-type]

    def test_expired_and_overlong_approvals_fail_closed(self):
        complete = attestation()
        expired = replace(complete.approvals[0], expires_at=NOW - timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "expired"):
            replace(complete, approvals=(expired,) + complete.approvals[1:]).validate_at(NOW)
        with self.assertRaisesRegex(ValueError, "lifetime"):
            ApprovalReference(WebApprovalControl.HOST_SECOPS, "APPROVAL_LONG",
                              NOW, NOW + timedelta(days=32))

    def test_references_are_opaque_and_non_personal(self):
        with self.assertRaisesRegex(ValueError, "invalid_web_approval_reference"):
            ApprovalReference(WebApprovalControl.HOST_SECOPS, "Alice Smith", NOW,
                              NOW + timedelta(days=1))
