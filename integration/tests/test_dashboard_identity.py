from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from integration.onboarding.authorization import AuthorizationError, Role
from integration.onboarding.dashboard_identity import (DashboardAccessPolicy, DashboardIdentity,
                                                       SalesforceSessionState, authorize_dashboard_refresh,
                                                       authorize_salesforce_reconnect)


NOW = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
POLICY = DashboardAccessPolicy("IDP_SUBJECT_OPERATOR01", "IDP_GROUP_VIEWERS01")


def identity(subject: str, groups: frozenset[str] = frozenset({"IDP_GROUP_VIEWERS01"})) -> DashboardIdentity:
    return DashboardIdentity(subject, groups, NOW - timedelta(minutes=1), NOW + timedelta(minutes=30))


class DashboardIdentityTests(unittest.TestCase):
    def test_only_opaque_operator_subject_can_refresh_or_reconnect(self):
        role = POLICY.role_for(identity("IDP_SUBJECT_OPERATOR01"), NOW)
        self.assertIs(role, Role.OPERATOR)
        authorize_dashboard_refresh(role)
        authorize_salesforce_reconnect(role, SalesforceSessionState.REAUTH_REQUIRED)

    def test_viewers_can_be_identified_but_cannot_refresh_or_reconnect(self):
        role = POLICY.role_for(identity("IDP_SUBJECT_VIEWER01"), NOW)
        self.assertIs(role, Role.VIEWER)
        with self.assertRaisesRegex(AuthorizationError, "refresh"):
            authorize_dashboard_refresh(role)
        with self.assertRaisesRegex(AuthorizationError, "reconnect"):
            authorize_salesforce_reconnect(role, SalesforceSessionState.REAUTH_REQUIRED)

    def test_expired_or_unapproved_identity_fails_closed(self):
        expired = DashboardIdentity("IDP_SUBJECT_OPERATOR01", frozenset({"IDP_GROUP_VIEWERS01"}), NOW - timedelta(hours=2), NOW - timedelta(hours=1))
        with self.assertRaisesRegex(AuthorizationError, "identity"):
            POLICY.role_for(expired, NOW)
        with self.assertRaisesRegex(AuthorizationError, "identity"):
            POLICY.role_for(identity("IDP_SUBJECT_UNKNOWN01", frozenset({"IDP_GROUP_OTHER01"})), NOW)
