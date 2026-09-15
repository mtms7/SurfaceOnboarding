"""Non-deploying control-plane model for an isolated, attended browser runner.

This contains no browser, network client, credential field, cookie handling, or
filesystem persistence.  A production implementation must use an approved KMS
envelope-encryption provider and a separate hardened runner host; the OPA VM is
explicitly not a browser runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic
from typing import Protocol
import re


REFERENCE = re.compile(r"^CO-[0-9]{4,10}$")
ALLOWED_DESTINATIONS = frozenset({"salesforce", "leonardo-development"})


class ControlPlaneBlocked(RuntimeError):
    pass


class EnvelopeProtector(Protocol):
    """Production boundary: KMS/HSM-backed authenticated envelope encryption."""

    def seal(self, plaintext: bytes) -> str: ...
    def open(self, ciphertext_reference: str) -> bytes: ...
    def destroy(self, ciphertext_reference: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RunnerGrant:
    grant_id: str
    correlation_id: str
    reference: str
    expires_at: float


@dataclass(slots=True)
class _RunnerSession:
    grant: RunnerGrant
    ciphertext_reference: str
    authenticated_destination: str | None = None
    disposed: bool = False


class BrowserRunnerControlPlane:
    """One-time, memory-only session grants with explicit manual-login state."""

    def __init__(self, protector: EnvelopeProtector, *, now: callable = monotonic) -> None:
        self._protector = protector
        self._now = now
        self._sessions: dict[str, _RunnerSession] = {}
        self._events: list[tuple[str, str]] = []  # correlation ID + redacted outcome only

    @property
    def redacted_events(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._events)

    def create_grant(self, *, reference: str, correlation_id: str, approved: bool,
                     manifest: bytes, ttl_seconds: int = 900) -> RunnerGrant:
        if not approved or not REFERENCE.fullmatch(reference) or not correlation_id or not manifest:
            raise ControlPlaneBlocked("grant:invalid_or_unapproved")
        if type(ttl_seconds) is not int or ttl_seconds < 60 or ttl_seconds > 900:
            raise ControlPlaneBlocked("grant:invalid_ttl")
        grant = RunnerGrant(token_urlsafe(24), correlation_id, reference, self._now() + ttl_seconds)
        self._sessions[grant.grant_id] = _RunnerSession(grant, self._protector.seal(manifest))
        self._events.append((correlation_id, "grant_created"))
        return grant

    def record_manual_login(self, grant_id: str, destination: str) -> None:
        session = self._active_session(grant_id)
        if destination not in ALLOWED_DESTINATIONS:
            raise ControlPlaneBlocked("login:destination_not_allowed")
        session.authenticated_destination = destination
        self._events.append((session.grant.correlation_id, "manual_login_completed"))

    def release_manifest_once(self, grant_id: str) -> bytes:
        session = self._active_session(grant_id)
        if session.authenticated_destination is None:
            raise ControlPlaneBlocked("manifest:manual_login_required")
        manifest = self._protector.open(session.ciphertext_reference)
        self._dispose(session, "manifest_released_and_session_destroyed")
        return manifest

    def expire(self) -> None:
        for session in tuple(self._sessions.values()):
            if not session.disposed and session.grant.expires_at <= self._now():
                self._dispose(session, "expired_and_session_destroyed")

    def _active_session(self, grant_id: str) -> _RunnerSession:
        self.expire()
        session = self._sessions.get(grant_id)
        if session is None or session.disposed:
            raise ControlPlaneBlocked("grant:missing_expired_or_consumed")
        return session

    def _dispose(self, session: _RunnerSession, outcome: str) -> None:
        if not session.disposed:
            self._protector.destroy(session.ciphertext_reference)
            session.disposed = True
            self._events.append((session.grant.correlation_id, outcome))
