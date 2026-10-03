"""Session readiness for the attended dashboard: pure rules, no input/output.

The dashboard signs the operator in to Salesforce (the `sf` CLI browser flow)
and to Leonardo (the operator types credentials and MFA into the real login
page in the automation Chrome). This module only classifies non-secret probe
outcomes, ages them, and decides whether an action may launch. It never sees
a password, MFA code, cookie, or token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import re
from typing import Mapping

from .origin_policy import LEONARDO_DEVELOPMENT_ORIGIN, PRODUCTION_BACKOFFICE_ORIGIN
from .web_activation import MAX_APPROVAL_LIFETIME, REFERENCE_PATTERN


class SessionState(StrEnum):
    NOT_SIGNED_IN = "not_signed_in"
    SIGNING_IN = "signing_in"
    READY = "ready"
    STALE = "stale"
    EXPIRED = "expired"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class SessionStatus:
    state: SessionState
    reason: str = ""
    checked_at: datetime | None = None


NOT_SIGNED_IN = SessionStatus(SessionState.NOT_SIGNED_IN, "not_checked")


# --- Environments and the production lock ---------------------------------

@dataclass(frozen=True, slots=True)
class SessionEnvironment:
    key: str
    label: str
    origin: str
    profile_dir_name: str
    production: bool


LEONARDO_DEVELOPMENT = "leonardo_development"
BACKOFFICE_PRODUCTION = "backoffice_production"
ENVIRONMENTS: dict[str, SessionEnvironment] = {
    LEONARDO_DEVELOPMENT: SessionEnvironment(LEONARDO_DEVELOPMENT, "Leonardo Development",
                                             LEONARDO_DEVELOPMENT_ORIGIN, "leonardo-automation", False),
    # Its own automation profile: Development and production sessions never share cookies.
    BACKOFFICE_PRODUCTION: SessionEnvironment(BACKOFFICE_PRODUCTION, "BackOffice production",
                                              PRODUCTION_BACKOFFICE_ORIGIN, "backoffice-production-automation", True),
}
# The runner opens only Leonardo Development URLs today; an environment-aware
# runner is a separate, reviewed change. Until then production stays locked
# whatever the configuration says.
PRODUCTION_RUNNER_SUPPORTED = False


def production_unlock_problem(environ: Mapping[str, str], now: datetime) -> str | None:
    """Why production sessions stay locked, else None (flag + approval + runner support).

    `now` must be timezone-aware. Being signed in to production never
    authorises a write; creates and edits keep their own gates.
    """
    if environ.get("SURFACE_ONBOARDING_PRODUCTION_SESSIONS") != "1":
        return "production_sessions_flag_missing"
    reference = environ.get("SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF", "")
    if not REFERENCE_PATTERN.fullmatch(reference):
        return "production_approval_missing"
    try:
        issued = datetime.fromisoformat(environ.get("SURFACE_ONBOARDING_PRODUCTION_APPROVAL_ISSUED", ""))
        expires = datetime.fromisoformat(environ.get("SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES", ""))
    except ValueError:
        return "production_approval_missing"
    if issued.tzinfo is None or expires.tzinfo is None:
        return "production_approval_missing"
    if expires <= issued or expires - issued > MAX_APPROVAL_LIFETIME or not issued <= now < expires:
        return "production_approval_expired"
    if not PRODUCTION_RUNNER_SUPPORTED:
        return "production_runner_not_supported"
    return None


def environment_problem(key: str, environ: Mapping[str, str], now: datetime) -> str | None:
    """None when sessions may be prepared for this environment key."""
    environment = ENVIRONMENTS.get(key)
    if environment is None:
        return "unknown_environment"
    return production_unlock_problem(environ, now) if environment.production else None


# --- Probe classification ---------------------------------------------------

SALESFORCE_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


def classify_salesforce_probe(returncode: int | None, org_id: object, expected_org_id: str | None,
                              now: datetime, *, timed_out: bool = False, cli_missing: bool = False) -> SessionStatus:
    """Classify `SELECT Id FROM Organization` run against the pinned org alias.

    Only the exit code and the org Id are used; nothing else from the CLI
    output is kept. The org must be pinned (SURFACE_SF_EXPECTED_ORG_ID);
    15- and 18-character forms are compared on their first 15 characters.
    """
    if cli_missing:
        return SessionStatus(SessionState.BLOCKED, "salesforce_cli_missing", now)
    if timed_out:
        return SessionStatus(SessionState.BLOCKED, "salesforce_timeout", now)
    if returncode != 0:
        return SessionStatus(SessionState.EXPIRED, "salesforce_sign_in_required", now)
    if not isinstance(org_id, str) or not SALESFORCE_ID.fullmatch(org_id):
        return SessionStatus(SessionState.BLOCKED, "salesforce_schema", now)
    if not expected_org_id or not SALESFORCE_ID.fullmatch(expected_org_id):
        return SessionStatus(SessionState.BLOCKED, "salesforce_org_not_pinned", now)
    if org_id[:15] != expected_org_id[:15]:
        return SessionStatus(SessionState.BLOCKED, "salesforce_wrong_org", now)
    return SessionStatus(SessionState.READY, "salesforce_ready", now)


# Runner session codes (tools/attended_ce_only_playwright.py) -> readiness.
_LEONARDO_RESULTS = {
    "leonardo_session_active": SessionState.READY,
    "leonardo_session_bootstrapped": SessionState.READY,
    "leonardo_session_expired": SessionState.EXPIRED,
    "development_login_timeout": SessionState.EXPIRED,
}


def classify_leonardo_result(result: str, now: datetime) -> SessionStatus:
    """Map a runner session-check/bootstrap code; anything unknown is blocked (fail closed)."""
    state = _LEONARDO_RESULTS.get(result, SessionState.BLOCKED)
    return SessionStatus(state, result, now)


# Runner results recorded after a check that prove the session has since expired.
LEONARDO_EXPIRY_RESULTS = frozenset({"leonardo_session_expired", "development_login_timeout"})


def leonardo_expired_by_runs(status: SessionStatus, runner_state: Mapping[str, Mapping[str, str]]) -> SessionStatus:
    """Demote a ready Leonardo status when a later run reported an expired session."""
    if status.state is not SessionState.READY or status.checked_at is None:
        return status
    for record in runner_state.values():
        if record.get("result") not in LEONARDO_EXPIRY_RESULTS:
            continue
        try:
            completed = datetime.fromisoformat(record.get("completed_on") or "")
        except ValueError:
            continue
        if (completed.tzinfo is None) == (status.checked_at.tzinfo is None) and completed >= status.checked_at:
            return SessionStatus(SessionState.EXPIRED, str(record["result"]), status.checked_at)
    return status


# --- Freshness and the action gate ------------------------------------------

START_LEONARDO_TTL = timedelta(minutes=5)
START_SALESFORCE_TTL = timedelta(minutes=15)
READ_TTL = timedelta(minutes=15)
ACTION_TTLS = {
    # A Start creates a tenant: Leonardo must have been proven very recently.
    "start": {"salesforce": START_SALESFORCE_TTL, "leonardo": START_LEONARDO_TTL},
    "read": {"salesforce": READ_TTL, "leonardo": READ_TTL},
}


def effective(status: SessionStatus, now: datetime, ttl: timedelta) -> SessionStatus:
    """A ready status older than `ttl` (or from the future) is stale."""
    if status.state is not SessionState.READY:
        return status
    if status.checked_at is None or not timedelta(0) <= now - status.checked_at <= ttl:
        return SessionStatus(SessionState.STALE, "check_is_old", status.checked_at)
    return status


def action_gate(action: str, salesforce: SessionStatus, leonardo: SessionStatus, now: datetime) -> str | None:
    """None when `action` ("start" or "read") may launch, else the refusal code."""
    ttls = ACTION_TTLS.get(action)
    if ttls is None:
        return "unknown_action"
    for system, status in (("salesforce", salesforce), ("leonardo", leonardo)):
        current = effective(status, now, ttls[system])
        if current.state is not SessionState.READY:
            return system + "_" + current.state.value
    return None


# --- Dashboard login = the operator's Salesforce SSO (2026-10-03) ----------
# Owner decision: no dashboard password. Signing in runs a fresh
# `sf org login web` (corporate SSO + MFA); the identity Salesforce reports
# for that session must be an allowed operator in the pinned org.

EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


def allowed_operators(raw: str | None) -> frozenset[str]:
    """Comma-separated operator emails (SURFACE_DASHBOARD_ALLOWED_USERS); malformed entries are ignored."""
    return frozenset(item.strip().casefold() for item in (raw or "").split(",") if EMAIL.fullmatch(item.strip()))


def identity_problem(userinfo: object, allowed: frozenset[str], expected_org_id: str | None) -> str | None:
    """None when Salesforce's userinfo names an allowed operator in the pinned org, else the reason.

    Only email, preferred_username, and organization_id are read; userinfo
    carries no token. Either the email or the username may match.
    """
    if not allowed:
        return "login_no_allowed_operator"
    if not isinstance(userinfo, Mapping):
        return "login_identity_unavailable"
    org_id = userinfo.get("organization_id")
    if not expected_org_id or not SALESFORCE_ID.fullmatch(expected_org_id):
        return "salesforce_org_not_pinned"
    if not isinstance(org_id, str) or not SALESFORCE_ID.fullmatch(org_id) or org_id[:15] != expected_org_id[:15]:
        return "salesforce_wrong_org"
    names = {value.strip().casefold() for value in (userinfo.get("email"), userinfo.get("preferred_username"))
             if isinstance(value, str)}
    if not names & allowed:
        return "login_operator_not_allowed"
    return None


def operator_label(userinfo: Mapping[str, object], allowed: frozenset[str]) -> str:
    """The allowed address that matched (shown as "Signed in as …")."""
    for key in ("email", "preferred_username"):
        value = userinfo.get(key)
        if isinstance(value, str) and value.strip().casefold() in allowed:
            return value.strip().casefold()
    return ""
