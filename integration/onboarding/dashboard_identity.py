"""Offline policy for future SSO-authenticated dashboard access.

Subject and group references are opaque IdP identifiers. This module neither
parses a browser cookie nor validates an IdP assertion; that must happen at an
approved proxy before this policy can receive an identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re

from .authorization import AuthorizationError, Role


REFERENCE = re.compile(r"^[A-Z][A-Z0-9_-]{5,63}$")


class SalesforceSessionState(StrEnum):
    NOT_CONNECTED = "not_connected"
    ACTIVE = "active"
    REAUTH_REQUIRED = "reauth_required"


@dataclass(frozen=True, slots=True)
class DashboardIdentity:
    subject_reference: str
    group_references: frozenset[str]
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.subject_reference, str) or not REFERENCE.fullmatch(self.subject_reference):
            raise ValueError("invalid_dashboard_subject")
        if not self.group_references or not all(isinstance(group, str) and REFERENCE.fullmatch(group) for group in self.group_references):
            raise ValueError("invalid_dashboard_groups")
        if any(value.tzinfo is None or value.utcoffset() is None for value in (self.issued_at, self.expires_at)) or self.expires_at <= self.issued_at:
            raise ValueError("invalid_dashboard_identity_lifetime")

    def active_at(self, when: datetime) -> bool:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("invalid_dashboard_identity_time")
        return self.issued_at <= when < self.expires_at


@dataclass(frozen=True, slots=True)
class DashboardAccessPolicy:
    """One initial operator plus a separately approved read-only viewer group."""
    operator_subject_reference: str
    viewer_group_reference: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and REFERENCE.fullmatch(value) for value in (self.operator_subject_reference, self.viewer_group_reference)):
            raise ValueError("invalid_dashboard_access_reference")

    def role_for(self, identity: DashboardIdentity, when: datetime) -> Role:
        if not isinstance(identity, DashboardIdentity) or not identity.active_at(when):
            raise AuthorizationError("dashboard_identity_not_authorized")
        if identity.subject_reference == self.operator_subject_reference:
            return Role.OPERATOR
        if self.viewer_group_reference in identity.group_references:
            return Role.VIEWER
        raise AuthorizationError("dashboard_identity_not_authorized")


def authorize_dashboard_refresh(role: Role) -> None:
    if role is not Role.OPERATOR:
        raise AuthorizationError("dashboard_refresh_not_authorized")


def authorize_salesforce_reconnect(role: Role, session: SalesforceSessionState) -> None:
    if role is not Role.OPERATOR or session not in {SalesforceSessionState.NOT_CONNECTED, SalesforceSessionState.REAUTH_REQUIRED}:
        raise AuthorizationError("salesforce_reconnect_not_authorized")
