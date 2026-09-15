"""Local role-policy primitives; identity authentication remains out of scope."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class AuthorizationError(PermissionError):
    """Raised when a local policy check fails without exposing identity details."""


_SYNC_REQUEST_ROLES = frozenset({Role.OPERATOR, Role.ADMIN})
_MANUAL_REVIEW_ROLES = frozenset({Role.REVIEWER})


def authorize_sync_request(role: Role) -> None:
    """Allow only operators and administrators to enqueue a synchronization."""
    if not isinstance(role, Role) or role not in _SYNC_REQUEST_ROLES:
        raise AuthorizationError("sync_request_not_authorized")


def authorize_manual_review(role: Role) -> None:
    """Allow only a reviewer role to decide a local manual-review record."""
    if not isinstance(role, Role) or role not in _MANUAL_REVIEW_ROLES:
        raise AuthorizationError("manual_review_not_authorized")
