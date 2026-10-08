"""Desktop-only, attended dashboard for Salesforce's Open_Onboardings view.

No scheduler, cache, write operation, VM transport, or generic query is
provided. Each request uses the operator's current Salesforce CLI session and
keeps data in memory only for rendering that response.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import contextvars
import copy
from html import escape
from dataclasses import dataclass
import functools
import hmac
import importlib.util
from typing import Any
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import shutil
import socket
import subprocess
import sys
from threading import Lock, Thread
from time import monotonic
from types import SimpleNamespace
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import state_paths
from integration.onboarding import run_progress
from integration.onboarding import users_file
from integration.onboarding import vm_snapshot
from integration.onboarding.state_paths import state_file
from integration.onboarding.dev_onboarded import dev_onboarded_evidence, dev_status
from integration.onboarding.renewal_mirror import mirror_tenant_ids
from integration.onboarding import session_readiness as readiness
from integration.onboarding.session_readiness import SessionState, SessionStatus
from phase1_validator.onboarding_comment_dates import extract_dealhub_dates
from phase1_validator.surface_source_readiness import evaluate_new_surface_source
from phase2_leonardo.case4_comment_validation import (
    ENGINE_VALUE as CASE4_ENGINE_VALUE,
    validate_case4_onboarding_comments,
)
from tools.attended_ce_only_playwright import (
    SCAN_STATUS_COMPLETED,
    SCAN_STATUS_FAILED,
    SCAN_STATUS_PATH,
    spycloud_state_of,
    MIRROR_PATH,
    RENEWAL_OUTCOME_MODES,
    RENEWAL_OUTCOMES_PATH,
    SPYCLOUD_STATE_PATH,
    VALIDATION_PATH,
    RunnerStateUnavailable,
    bootstrap_leonardo_session,
    check_leonardo_session,
    CE_ENGINE,
    format_surface_domains,
    CE_ROUTE_PRODUCT,
    CE_ROUTE_TYPE,
    SURFACE_ENGINE,
    CASE3_ENGINE,
    CASE3_ROUTE_PRODUCT,
    CASE3_ROUTE_TYPE,
    case3_fill_source,
    case3_scope_summary,
    case3_term_problem,
    build_renewal_plan,
    renewal_case,
    RENEWAL_ENGINES,
    SCAN_EXEC_DONE,
    SCAN_EXEC_RUNNING,
    SCAN_EXEC_WITH_ERRORS,
    SURFACE_ROUTE_PRODUCT,
    SURFACE_ROUTE_TYPE,
    _chrome_executable,
    close_automation_browser,
    production_match_for,
    load_check_state,
    load_runner_state,
    start_blocker,
    create_uncertain,
    renewal_uncertain,
    record_runner_result,
    record_runner_start,
    reset_leonardo_profile,
    reset_runner_record,
    run_inventory_precheck,
    surface_fill_source,
    surface_scope_summary,
)

HOST, PORT = "127.0.0.1", 8012
LEONARDO_DEVELOPMENT_TENANT_MANAGEMENT = "https://leonardo.dev.app.pentera.io/backoffice/tenantManagement"
PRODUCTION_BACKOFFICE_LOGIN = "https://app.pentera.io/login"
REFERENCE = re.compile(r"^CO-[0-9]{4,10}$")
MANUAL_START_ACK_TTL_SECONDS = 15 * 60
COMMENT_UPDATE_ACK_TTL_SECONDS = 10 * 60
VIEW_QUERY = "SELECT Id FROM ListView WHERE SobjectType = 'Customer_Onboarding__c' AND DeveloperName = 'Open_Onboardings' LIMIT 2"
QUEUE_FIELDS = ("Name", "Onboarding_Approval_Status__c", "Onboarding_Stage__c", "Onboarding_Product__c", "Onboarding_Type__c", "Account__r.Name", "Submission_Date__c")
DETAIL_FIELDS = ("Name", "Onboarding_Approval_Status__c", "Account__c", "Account_Name__c", "Onboarding_Product__c", "Onboarding_Type__c", "Primary_User_Name__c", "Main_Domain__c", "Alternate_Domains__c", "Email_Domains__c", "Onboarding_Comments__c", "Onboarding_Stage__c", "Surface_Account_ID__c", "Account_UUID__c", "LastModifiedDate")
DETAIL_DISPLAY_FIELDS = (
    ("Onboarding Approval Status", "Onboarding_Approval_Status__c"), ("Account", "Account_Name__c"),
    ("Onboarding Product", "Onboarding_Product__c"), ("Onboarding Type", "Onboarding_Type__c"),
    ("Primary User", "Primary_User_Name__c"), ("Main Domain", "Main_Domain__c"),
    ("Alternative Domains", "Alternate_Domains__c"), ("Email Domains", "Email_Domains__c"),
    ("Onboarding Comments", "Onboarding_Comments__c"), ("Onboarding Stage", "Onboarding_Stage__c"),
    ("Surface Account ID", "Surface_Account_ID__c"), ("Account UUID", "Account_UUID__c"),
)
ATTENDED_LEONARDO_READBACK_PATH = state_file(Path(__file__).resolve().parents[1] / "integration" / "attended_leonardo_readbacks.json")
ATTENDED_CE_ONLY_RUNNER = Path(__file__).resolve().with_name("attended_ce_only_playwright.py")
# Local operator acknowledgements for post-onboarding reminders (gitignored).
# It never drives Leonardo or Salesforce; it only hides a reminder.
ATTENDED_REMINDERS_PATH = state_file(Path(__file__).resolve().parents[1] / "integration" / "attended_scan_reminders.json")
REMINDER_FIELDS = {"scan_settings_off": "scan_settings_off_on", "ce_enabled": "ce_enabled_on",
                   "operator_assigned": "operator_assigned_on"}
# Manual "User created" confirmation (gitignored): {CO: {confirmed, confirmed_on, confirmed_by}}; no names or emails.
USER_CREATED_CONFIRMATION_PATH = state_file(Path(__file__).resolve().parents[1] / "integration" / "attended_user_created_confirmations.json")
LEONARDO_READBACK_STATES = frozenset({"Account Scanning", "No scan started"})
CASE4_PRODUCT = "Surface & Credential Exposure"
CASE4_TYPE = "Renewal of Surface + New Credential Exposure Module"
CO0745_REFERENCE = "CO-0745"
CO0702_REFERENCE = "CO-0702"
SURFACE_BASELINE_PRODUCT = re.compile(r"^pentera surface(?: go)?\s*-\s*(\d+) subdomains$", re.IGNORECASE)
_manual_start_acks: dict[str, tuple[str, str, float]] = {}
_manual_start_lock = Lock()
_view_id_lock = Lock()
_open_onboardings_view_id: str | None = None
_salesforce_login_lock = Lock()
_salesforce_login_process: subprocess.Popen[str] | None = None
_comment_update_acks: dict[str, tuple[str, str, float]] = {}
_comment_update_lock = Lock()
# One attended start at a time: the gate, the start record, and the launch run
# under this lock so two quick clicks cannot both pass the gate.
_start_lock = Lock()


@dataclass(frozen=True, slots=True)
class CommentUpdateEvaluation:
    co_id: str
    source_revision: str
    subscription_id: str
    subscription_revision: str
    proposed_comment: str

    @property
    def binding(self) -> str:
        return sha256("\x00".join((self.co_id, self.source_revision, self.subscription_id,
                                    self.subscription_revision, self.proposed_comment)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RenewalCommentEvaluation:
    """A read-only, revision-bound DealHub term proposal for one renewal CO."""

    reference: str
    co_id: str
    source_revision: str
    subscription_id: str
    subscription_revision: str
    expected_tenant_name: str
    product_name: str
    proposed_comment: str


@dataclass(frozen=True, slots=True)
class CredentialExposureFillPreflight:
    """Read-only candidate review for one Development-only CE-only fill."""

    reference: str
    source_revision: str
    email_domain_count: int
    blockers: tuple[str, ...]

    @property
    def eligible_for_fill_review(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class SurfaceScopePreflight:
    """Read-only, counts-only scope review for one Surface-only (Case 1) CO.

    ``scope`` holds only counts, tier, interval, and license dates (never
    domain values). ``scope_digest`` binds the operator's scope review to the
    exact computed scope: DealHub rows can change without changing the CO
    revision, so the start gate also compares this digest.
    """

    reference: str
    source_revision: str
    blockers: tuple[str, ...]
    scope: dict[str, object] | None = None
    core_plus_present: bool = False
    route: str = SURFACE_ENGINE
    blocker_details: tuple[str, ...] = ()

    @property
    def eligible_for_fill_review(self) -> bool:
        return not self.blockers and self.scope is not None and bool(self.source_revision)

    @property
    def scope_digest(self) -> str:
        if self.scope is None:
            return ""
        material = json.dumps({"reference": self.reference, "source_revision": self.source_revision,
                               "scope": self.scope}, sort_keys=True, separators=(",", ":"))
        return sha256(material.encode("utf-8")).hexdigest()


class ReadUnavailable(RuntimeError): pass


class WriteUnavailable(ReadUnavailable): pass


def listener_address() -> tuple[str, int]:
    """Return the one permitted loopback listener for the selected runtime."""
    runtime = os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold()
    default_port = "8000" if runtime == "vm" else "8012"
    host = os.environ.get("SURFACE_ONBOARDING_HOST", "127.0.0.1")
    raw_port = os.environ.get("SURFACE_ONBOARDING_PORT", default_port)
    if runtime not in {"desktop", "vm"} or host != "127.0.0.1":
        raise RuntimeError("invalid_dashboard_listener_configuration")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("invalid_dashboard_listener_configuration") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("invalid_dashboard_listener_configuration")
    if runtime == "vm" and os.environ.get("SURFACE_ONBOARDING_WEB_IDENTITY_APPROVED") != "1":
        raise RuntimeError("vm_web_identity_approval_required")
    return host, port


def salesforce_cli_command() -> str:
    """Resolve the platform CLI without copying a user session between hosts."""
    if state_paths.vm_mode():
        # VM data source switch (docs/41): the VM has no Salesforce connection; every caller already treats OSError
        # as "CLI unavailable" and fails closed, so no process is ever started in VM mode.
        raise OSError("salesforce_cli_unavailable_in_vm_mode")
    configured = os.environ.get("SURFACE_SF_CLI")
    if configured:
        return configured
    return "sf.cmd" if os.name == "nt" else "sf"


def salesforce_target_org() -> str:
    """The one Salesforce CLI alias every dashboard read and write is pinned to."""
    alias = os.environ.get("SURFACE_SF_TARGET_ORG", "surface-onboarding")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", alias):
        raise RuntimeError("invalid_salesforce_target_org")
    return alias


# The CLI's browser sign-in opens Chrome (where the operator's OneLogin session
# lives), not the system default browser, and waits for its OAuth callback on
# this loopback port (2026-10-03: the default browser was Edge, unseen, and an
# orphaned CLI kept the port, so every retry failed with PortInUseError).
SALESFORCE_OAUTH_PORT = 1717
SALESFORCE_LOGIN_BROWSERS = frozenset({"chrome", "edge", "firefox"})


def salesforce_login_browser() -> str:
    browser = os.environ.get("SURFACE_SF_LOGIN_BROWSER", "chrome").casefold()
    return browser if browser in SALESFORCE_LOGIN_BROWSERS else "chrome"


def salesforce_login_port_busy() -> bool:
    """True while another CLI sign-in still holds the OAuth callback port (nothing is sent)."""
    try:
        with socket.create_connection(("localhost", SALESFORCE_OAUTH_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def kill_process_tree(process: Any) -> None:
    """Stop a CLI sign-in and its children: killing only sf.cmd leaves node holding the port."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def local_browser_launch_allowed() -> bool:
    """Only the Windows attended pilot may launch a local operator browser.

    The Ubuntu service is deliberately unable to launch SSO/MFA or BackOffice
    pages. A future separately approved browser runner owns that interaction.
    """
    return os.name == "nt" and os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "desktop"


def start_attended_salesforce_login() -> bool:
    """Launch the standard Salesforce CLI browser sign-in without handling secrets.

    The CLI owns its interactive SSO/MFA exchange.  This process deliberately
    discards CLI output and retains only a running-process reference so the
    dashboard cannot duplicate browser-login prompts.
    """
    global _salesforce_login_process
    if not local_browser_launch_allowed():
        return False
    with _salesforce_login_lock:
        if _salesforce_login_process is not None and _salesforce_login_process.poll() is None:
            return True
        if salesforce_login_port_busy():
            return False
        try:
            _salesforce_login_process = subprocess.Popen(
                # The alias only: every read and write pins --target-org to it, so
                # the operator's global default org is never changed.
                [salesforce_cli_command(), "org", "login", "web", "--alias", salesforce_target_org(),
                 "--browser", salesforce_login_browser()],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            _salesforce_login_process = None
            return False


# --- Sign in / Prepare sessions (2026-10-03) ---------------------------------
# Readiness lives in memory only: a restart means "not signed in". Only state
# names, reason codes, and check times are kept; never CLI output, cookies,
# tokens, passwords, or MFA codes.
_readiness_lock = Lock()
_readiness: dict[str, SessionStatus] = {"salesforce": readiness.NOT_SIGNED_IN, "leonardo": readiness.NOT_SIGNED_IN}
_leonardo_worker: Thread | None = None
SALESFORCE_PROBE_TIMEOUT_SECONDS = 20
SALESFORCE_ORG_QUERY = "SELECT Id FROM Organization LIMIT 1"


def readiness_now() -> datetime:
    return datetime.now()


def session_statuses(*, include_runs: bool = True) -> dict[str, SessionStatus]:
    """Current statuses; a ready Leonardo session is demoted when a later run reported it expired."""
    with _readiness_lock:
        statuses = dict(_readiness)
    if not include_runs:
        return statuses
    try:
        statuses["leonardo"] = readiness.leonardo_expired_by_runs(statuses["leonardo"], load_runner_state())
    except RunnerStateUnavailable:
        pass
    return statuses


def set_session_status(system: str, status: SessionStatus) -> None:
    with _readiness_lock:
        _readiness[system] = status


def any_run_in_progress() -> bool:
    """True while any recorded attended run has no result (it may be using the automation browser)."""
    try:
        return any(not record.get("result") for record in load_runner_state().values())
    except RunnerStateUnavailable:
        return True


def probe_salesforce_readiness() -> SessionStatus:
    """Read-only: the pinned org's Id must equal SURFACE_SF_EXPECTED_ORG_ID."""
    now = readiness_now()
    try:
        done = subprocess.run([salesforce_cli_command(), "data", "query", "--query", SALESFORCE_ORG_QUERY, "--json",
                               "--target-org", salesforce_target_org()], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=SALESFORCE_PROBE_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        status = readiness.classify_salesforce_probe(None, None, None, now, timed_out=True)
    except OSError:
        status = readiness.classify_salesforce_probe(None, None, None, now, cli_missing=True)
    else:
        org_id = None
        if done.returncode == 0 and done.stdout is not None and len(done.stdout.encode()) <= 64 * 1024:
            try:
                records = json.loads(done.stdout).get("result", {}).get("records", [])
                org_id = records[0].get("Id") if isinstance(records, list) and len(records) == 1 else None
            except (ValueError, AttributeError, IndexError):
                org_id = None
        status = readiness.classify_salesforce_probe(done.returncode, org_id,
                                                     os.environ.get("SURFACE_SF_EXPECTED_ORG_ID"), now)
    set_session_status("salesforce", status)
    return status


def refresh_salesforce_after_login() -> None:
    """When the CLI browser sign-in has exited, check the pinned org once."""
    with _salesforce_login_lock:
        process = _salesforce_login_process
    if session_statuses()["salesforce"].state is SessionState.SIGNING_IN and process is not None \
            and process.poll() is not None:
        probe_salesforce_readiness()


def _leonardo_session_work(sign_in: bool) -> None:
    """Background worker: check the automation session; if needed, wait for the operator's SSO/MFA."""
    status = SessionStatus(SessionState.BLOCKED, "leonardo_check_failed", readiness_now())
    try:
        if run_preflight()["leonardo_network"] != "ok":
            # No VPN route: say so instead of waiting on the automation browser.
            status = SessionStatus(SessionState.BLOCKED, "leonardo_unreachable", readiness_now())
            return
        status = readiness.classify_leonardo_result(check_leonardo_session(), readiness_now())
        if sign_in and status.state is SessionState.EXPIRED:
            set_session_status("leonardo", SessionStatus(SessionState.SIGNING_IN, "waiting_for_operator", readiness_now()))
            status = readiness.classify_leonardo_result(bootstrap_leonardo_session(), readiness_now())
    except Exception:  # fail closed: never leave the page "signing in" after a crash
        status = SessionStatus(SessionState.BLOCKED, "leonardo_check_failed", readiness_now())
    finally:
        set_session_status("leonardo", status)


def start_leonardo_session_worker(sign_in: bool) -> bool:
    """Run one Leonardo check (and sign-in wait) in the background; False if one is already running."""
    global _leonardo_worker
    with _readiness_lock:
        if _leonardo_worker is not None and _leonardo_worker.is_alive():
            return False
        _readiness["leonardo"] = SessionStatus(SessionState.SIGNING_IN, "checking", readiness_now())
        _leonardo_worker = Thread(target=_leonardo_session_work, args=(sign_in,), daemon=True)
        _leonardo_worker.start()
        return True


def leonardo_worker_running() -> bool:
    with _readiness_lock:
        return _leonardo_worker is not None and _leonardo_worker.is_alive()


def prepare_sessions_problem(environment: str | None) -> str | None:
    """Why sessions cannot be prepared now, else None (production stays locked)."""
    problem = readiness.environment_problem(environment or "", os.environ, datetime.now(timezone.utc))
    if problem is not None:
        return problem
    if not local_browser_launch_allowed():
        return "desktop_runtime_required"
    if any_run_in_progress():
        return "run_in_progress"
    return None


def prepare_sessions() -> None:
    """Check both sessions; open the sign-in for any that needs the operator."""
    salesforce = probe_salesforce_readiness()
    if salesforce.state is SessionState.EXPIRED and start_attended_salesforce_login():
        set_session_status("salesforce", SessionStatus(SessionState.SIGNING_IN, "waiting_for_operator", readiness_now()))
    start_leonardo_session_worker(sign_in=True)


def action_readiness_problem(action: str) -> str | None:
    """Gate for every Start / Validate / scan refresh: None means it may launch.

    A stale or unchecked session is re-checked inline once (read-only), but
    never while a run may be using the automation browser or while the
    operator is signing in.
    """
    statuses = session_statuses()
    now = readiness_now()
    if readiness.action_gate(action, statuses["salesforce"], statuses["leonardo"], now) is None:
        return None
    if leonardo_worker_running() or statuses["salesforce"].state is SessionState.SIGNING_IN:
        return "session_signing_in"
    if any_run_in_progress():
        return "run_in_progress"
    ttls = readiness.ACTION_TTLS.get(action, {})
    if readiness.effective(statuses["salesforce"], now, ttls.get("salesforce", readiness.READ_TTL)).state \
            is not SessionState.READY:
        probe_salesforce_readiness()
    if readiness.effective(statuses["leonardo"], now, ttls.get("leonardo", readiness.READ_TTL)).state \
            is not SessionState.READY:
        set_session_status("leonardo", readiness.classify_leonardo_result(check_leonardo_session(), readiness_now()))
    statuses = session_statuses()
    return readiness.action_gate(action, statuses["salesforce"], statuses["leonardo"], readiness_now())


SESSION_REASON_TEXT = {
    "not_checked": "Not checked since the dashboard started.",
    "checking": "Checking…",
    "waiting_for_operator": "Waiting for you to finish sign-in (SSO/MFA) in the browser.",
    "check_is_old": "The last check is too old; it is re-checked before the next action.",
    "salesforce_ready": "Signed in to the pinned Salesforce org.",
    "salesforce_sign_in_required": "Sign-in required (no valid CLI session for the pinned org alias).",
    "salesforce_org_not_pinned": "The expected Salesforce org Id is not configured (SURFACE_SF_EXPECTED_ORG_ID). Nothing runs until it is.",
    "salesforce_wrong_org": "The CLI session belongs to a different Salesforce org than the pinned one.",
    "salesforce_timeout": "Salesforce did not answer in time. Check the network or VPN, then re-check.",
    "salesforce_cli_missing": "The Salesforce CLI could not be started on this desktop.",
    "salesforce_schema": "Salesforce returned an unexpected answer.",
    "salesforce_read_failed": "A Salesforce read failed; sign in again or re-check.",
    "leonardo_session_active": "The automation browser is signed in to Leonardo Development.",
    "leonardo_session_bootstrapped": "Signed in to Leonardo Development in the automation browser.",
    "leonardo_session_expired": "The Leonardo session has expired. Prepare sessions to sign in again.",
    "development_login_timeout": "The Leonardo sign-in was not completed in time.",
    "leonardo_session_unavailable": "No Leonardo Development page was reachable (VPN or network).",
    "leonardo_check_failed": "The Leonardo check stopped unexpectedly. Re-check, or use the Advanced controls below.",
    "leonardo_unreachable": "Leonardo Development is not reachable from this desktop. Connect the RND VPN, then re-check.",
    "login_operator_not_allowed": "The last sign-in was not an allowed operator; its CLI session was ended.",
    "login_identity_unavailable": "Salesforce did not report who signed in.",
}
GATE_REFUSAL_TEXT = {
    "session_signing_in": "A sign-in or session check is still running.",
    "run_in_progress": "An attended run is in progress, so the sessions cannot be re-checked now. Wait for it to finish.",
    "production_sessions_flag_missing": "BackOffice production is locked (not enabled on this desktop).",
    "production_approval_missing": "BackOffice production is locked (no owner/SecOps approval reference is configured).",
    "production_approval_expired": "BackOffice production is locked (the approval reference is expired or invalid).",
    "production_runner_not_supported": "BackOffice production is locked (the automation runner supports Leonardo Development only).",
    "unknown_environment": "Unknown environment.",
    "desktop_runtime_required": "Sign-in can only be prepared on the attended Windows desktop.",
}


def session_gate_page(problem: str, back: str = "/") -> str:
    """409 page for a refused action: nothing was launched."""
    system, _, state = problem.partition("_")
    if system in ("salesforce", "leonardo") and state:
        status = session_statuses()[system]
        detail = ("Salesforce" if system == "salesforce" else "Leonardo Development") + ": " + escape(
            SESSION_REASON_TEXT.get(status.reason, "state " + state.replace("_", " ") + "."))
    else:
        detail = escape(GATE_REFUSAL_TEXT.get(problem, "Sessions are not ready."))
    return ("<!doctype html><title>Sign-in needed</title><p><strong>Sessions are not ready.</strong> " + detail +
            " Nothing was started.</p><p>Code: <code>" + escape(problem) + "</code></p>"
            "<p><a href='/connection'>Sign in / Prepare sessions</a> · <a href='" + escape(back) + "'>Return</a></p>")


def start_attended_ce_only_runner(reference: str, revision: str) -> bool:
    """Launch one visible, isolated, desktop-only CE auto-confirm process."""
    if (not REFERENCE.fullmatch(reference) or not revision or not local_browser_launch_allowed()
            or not ATTENDED_CE_ONLY_RUNNER.is_file()):
        return False
    try:
        subprocess.Popen([sys.executable, str(ATTENDED_CE_ONLY_RUNNER), "--co", reference, "--revision", revision],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# One-time acknowledgement of the Start renewal form: bound to the CO's source revision, 15 minutes, consumed once.
RENEWAL_START_ACK_TTL_SECONDS = 15 * 60
_renewal_start_acks: dict[str, tuple[str, str, float]] = {}
_renewal_start_lock = Lock()


def renewal_start_ack_nonce(reference: str, row: dict[str, str | None], *, now: float | None = None) -> str | None:
    """The form's nonce for the CO's current revision (an active one is reused); None when not Approved or no revision."""
    token = _source_revision_token(reference, row)
    if token is None or not source_ready_to_onboard(row):
        return None
    current = monotonic() if now is None else now
    with _renewal_start_lock:
        ack = _renewal_start_acks.get(reference)
        if ack is not None and ack[0] == token and ack[2] > current:
            return ack[1]
        nonce = token_urlsafe(24)
        _renewal_start_acks[reference] = (token, nonce, current + RENEWAL_START_ACK_TTL_SECONDS)
        return nonce


def consume_renewal_start_ack(reference: str, row: dict[str, str | None], nonce: str, *, now: float | None = None) -> bool:
    """Consume exactly one matching acknowledgement (any failure also discards the stored one)."""
    if not isinstance(nonce, str) or not nonce:
        return False
    token = _source_revision_token(reference, row)
    current = monotonic() if now is None else now
    with _renewal_start_lock:
        ack = _renewal_start_acks.get(reference)
        if ack is None or token is None or not source_ready_to_onboard(row) or ack[0] != token or ack[2] <= current \
                or not hmac.compare_digest(ack[1], nonce):
            _renewal_start_acks.pop(reference, None)
            return False
        _renewal_start_acks.pop(reference, None)
        return True


# Routes that use the Surface scope review and Start card (Case 1 and Case 3).
SURFACE_ROUTES = frozenset({SURFACE_ENGINE, CASE3_ENGINE})


def start_attended_surface_runner(reference: str, revision: str, route: str = SURFACE_ENGINE) -> bool:
    """Launch one desktop-only Surface-only (Case 1) or combined (Case 3) auto-confirm process."""
    if (not REFERENCE.fullmatch(reference) or not revision or not local_browser_launch_allowed()
            or not ATTENDED_CE_ONLY_RUNNER.is_file() or route not in SURFACE_ROUTES):
        return False
    try:
        subprocess.Popen([sys.executable, str(ATTENDED_CE_ONLY_RUNNER), "--co", reference, "--revision", revision,
                          "--route", route],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def start_attended_renewal_runner(reference: str, revision: str) -> bool:
    """Launch one desktop-only orchestrated renewal run (Dev mirror if needed, dry run, apply; SpyCloud is not touched).

    Same launch shape as a create run; Leonardo Development only (the runner has no production mode).
    """
    if (not REFERENCE.fullmatch(reference) or not revision or not local_browser_launch_allowed()
            or not ATTENDED_CE_ONLY_RUNNER.is_file()):
        return False
    try:
        subprocess.Popen([sys.executable, str(ATTENDED_CE_ONLY_RUNNER), "--co", reference, "--revision", revision,
                          "--renew-run"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def start_attended_scan_status_all() -> bool:
    """Launch one desktop-only, read-only scan-status sweep of every onboarded Surface / Case 3 CO."""
    if not local_browser_launch_allowed() or not ATTENDED_CE_ONLY_RUNNER.is_file():
        return False
    try:
        subprocess.Popen([sys.executable, str(ATTENDED_CE_ONLY_RUNNER), "--scan-status-all"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def _start_runner_mode(*arguments: str) -> bool:
    """Launch one desktop-only, read-only runner mode (no fill, submit, or create)."""
    if not local_browser_launch_allowed() or not ATTENDED_CE_ONLY_RUNNER.is_file():
        return False
    try:
        subprocess.Popen([sys.executable, str(ATTENDED_CE_ONLY_RUNNER), *arguments],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def start_attended_validation(reference: str) -> bool:
    return REFERENCE.fullmatch(reference) is not None and _start_runner_mode("--co", reference, "--validate")


def start_attended_validation_all() -> bool:
    return _start_runner_mode("--validate-all")


VALIDATION_STATUSES = frozenset({"ok", "drift", "unknown", "info", "warn"})
VALIDATION_GROUPS = ("Account", "Licence", "Settings", "Domains", "People", "Scan")


def attended_validations() -> dict[str, dict[str, object]]:
    """Load the local Surface validation results; absence means none. Malformed entries are dropped."""
    try:
        raw = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    results: dict[str, dict[str, object]] = {}
    for reference, value in raw.items():
        try:
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict):
                continue
            checks = value.get("checks")
            if not isinstance(checks, list) or not all(
                    isinstance(c, dict) and c.get("status") in VALIDATION_STATUSES
                    and isinstance(c.get("check"), str) and c.get("group") in VALIDATION_GROUPS for c in checks):
                continue
            matches = value.get("primary_user_matches")
            checked_on = value.get("primary_user_checked_on")
            results[reference] = {"primary_user_matches": matches if type(matches) is bool else None,
                                  "primary_user_checked_on": checked_on if isinstance(checked_on, str) else "",
                                  "checks": checks, "plan_note": str(value.get("plan_note") or ""),
                                  "observed_at": datetime.fromisoformat(value["observed_at"]),
                                  "expires_at": datetime.fromisoformat(value["expires_at"])}
        except (KeyError, TypeError, ValueError):
            continue
    return results


def _validation_value(value: object) -> str:
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    if value is None:
        return "—"
    return str(value)


def dev_mirror_ids() -> frozenset[str]:
    """Dev tenant ids of the verified renewal mirrors; an unreadable record file means none (never hides a tenant)."""
    try:
        return mirror_tenant_ids(json.loads(MIRROR_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return frozenset()


DEV_MIRROR_CHIP = ("<span class='chip chip-info' title='Renewal test copy of a production tenant; "
                   "drift validation is skipped'>DEV mirror</span>")


def _validation_section(reference: str, notice: str = "", now: datetime | None = None, mirror: bool = False) -> str:
    """Surface validation checklist for an onboarded CO (local, read-only observation)."""
    if mirror:
        return ("<section class='stat' aria-labelledby='validation-title'><div class='stat-head'>"
                "<h2 id='validation-title'>Surface validation</h2></div><p>" + DEV_MIRROR_CHIP + " This tenant is the "
                "Development mirror of a production tenant, used to test the renewal. Its licence is edited on purpose, "
                "so drift validation is skipped (<code>dev_mirror_skipped</code>).</p></section>")
    result = attended_validations().get(reference)
    now = now or datetime.now()
    button = ("<form method='post' action='/attended/validate'><input type='hidden' name='reference' value='"
              + escape(reference) + "'><button type='submit' class='ghost sm'>Validate in Surface</button></form>")
    started = ("<p class='note'>A read-only validation was started in the automation browser. "
               "Reload this page in about 30 seconds.</p>" if notice == "validation-started" else "")
    if result is None:
        return ("<section class='stat' aria-labelledby='validation-title'><div class='stat-head'>"
                "<h2 id='validation-title'>Surface validation</h2></div>"
                "<p>Not validated yet. Validate reads the tenant from Leonardo Development (read-only) and compares "
                "it with the onboarding plan.</p>" + started + button + "</section>")
    checks = result["checks"]  # type: ignore[assignment]
    drift = [c for c in checks if c["status"] == "drift"]  # type: ignore[union-attr]
    unknown = [c for c in checks if c["status"] == "unknown"]  # type: ignore[union-attr]
    warned = [c for c in checks if c["status"] == "warn"]  # type: ignore[union-attr]
    stale = now > result["expires_at"]  # type: ignore[operator]
    cls, icon = (("source-blocked", "!") if drift else (("source-warn", "!") if unknown or warned else ("source-ready", "✓")))
    title = (f"{len(drift)} difference(s) found" if drift
             else (f"Verified, {len(unknown)} check(s) unconfirmed" if unknown
                   else (f"Verified, {len(warned)} warning(s)" if warned else "Everything matches")))
    marks = {"ok": "✓", "drift": "✗", "unknown": "?", "info": "·", "warn": "!"}
    rows = ""
    for group in VALIDATION_GROUPS:
        items = [c for c in checks if c["group"] == group]  # type: ignore[union-attr]
        if not items:
            continue
        lines = []
        for item in items:
            detail = ""
            if "expected" in item or "found" in item:
                if item["status"] == "info":
                    detail = " · " + escape(_validation_value(item.get("found")))
                else:
                    detail = (" · expected " + escape(_validation_value(item.get("expected")))
                              + ", found " + escape(_validation_value(item.get("found"))))
            style = (" style='color:var(--bad);font-weight:600'" if item["status"] == "drift"
                     else " style='color:#8a6d1a;font-weight:600'" if item["status"] == "warn" else "")
            lines.append(f"<span{style}>{marks[item['status']]} {escape(item['check'])}{detail}</span>")
        rows += f"<dt>{escape(group)}</dt><dd>{'<br>'.join(lines)}</dd>"
    plan_note = ""
    if result["plan_note"]:
        plan_note = ("<p class='note'>The onboarding plan could not be rebuilt from Salesforce (<code>"
                     + escape(str(result["plan_note"])) + "</code>); only account, people, and scan checks ran.</p>")
    observed = result["observed_at"].strftime("%Y-%m-%d %H:%M")  # type: ignore[union-attr]
    return ("<section class='stat " + cls + "' aria-labelledby='validation-title'><div class='stat-head'>"
            f"<span class='readiness-icon' aria-hidden='true'>{icon}</span>"
            f"<h2 id='validation-title'>Surface validation · {escape(title)}</h2></div>"
            f"<p>Observed {escape(observed)}" + (" · <b>stale, validate again</b>" if stale else "") + ". "
            "Compared with the onboarding plan; names, domains, and emails are checked but not stored.</p>"
            + plan_note + started + button
            + "<details class='fold'" + (" open" if drift or warned else "") + "><summary>" + str(len(checks)) + " checks</summary>"
            "<dl>" + rows + "</dl></details></section>")


def start_attended_spycloud_check(reference: str) -> bool:
    """Launch the read-only SpyCloud dry run (opens Edit, reports the checkbox, Cancel). Never passes --confirm-write:
    the save that turns SpyCloud OFF is a Leonardo write and stays a manual CLI step (SpyCloud ON is the expected default)."""
    return REFERENCE.fullmatch(reference) is not None and _start_runner_mode("--co", reference, "--spycloud-off")


SPYCLOUD_MESSAGES = {
    "spycloud_off_verified": "SpyCloud is OFF (saved and read back). Leonardo's default is ON; this is informational only.",
    "spycloud_already_off": "SpyCloud is OFF; nothing was saved. Leonardo's default is ON; this is informational only.",
    "spycloud_dry_run_on": "SpyCloud is ON, Leonardo's default (dry run; nothing was saved).",
    "spycloud_on_verified": "SpyCloud is ON (turned back ON by hand, saved and read back). Leonardo's default.",
    "spycloud_already_on": "SpyCloud is ON, Leonardo's default; nothing was saved.",
    "spycloud_dry_run_off": "SpyCloud is OFF (dry run; nothing was saved). Leonardo's default is ON; turning it back ON is a manual operator step.",
    "spycloud_readback_still_off": "A manual save was made but Leonardo still shows SpyCloud OFF. Run the SpyCloud check again.",
    "spycloud_readback_still_on": "A manual save was made but Leonardo still shows SpyCloud ON (the default). Nothing else is needed.",
    "spycloud_save_id_mismatch": "Leonardo's edit reply named a different tenant. Check the tenants in Leonardo Development.",
}


def attended_spycloud_states() -> dict[str, dict[str, object]]:
    """Load the per-CO SpyCloud outcomes; absence means none. Malformed entries are dropped.

    ``state`` (on / off / unknown) is derived from the outcome, never from the stored ``warning`` flag, so records
    written before the 2026-10-08 decision (ON used to carry warning=True) load unchanged. ``ok`` = the flag was read.
    """
    try:
        raw = json.loads(SPYCLOUD_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    states: dict[str, dict[str, object]] = {}
    for reference, value in raw.items():
        try:
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict):
                continue
            outcome = value.get("outcome")
            if not isinstance(outcome, str) or not re.fullmatch(r"[a-z_]{1,64}", outcome):
                continue
            if value.get("mode") not in ("standalone", "dry_run", "after_create"):
                continue
            states[reference] = {"outcome": outcome, "mode": value["mode"],
                                 "observed_at": datetime.fromisoformat(value["observed_at"]),
                                 "state": spycloud_state_of(outcome), "ok": spycloud_state_of(outcome) != "unknown"}
        except (KeyError, TypeError, ValueError):
            continue
    return states


def _spycloud_section(reference: str, notice: str = "") -> str:
    """SpyCloud card for an onboarded CE / Case 3 CO (owner 2026-10-08: ON is the default and expected; OFF is a note)."""
    state = attended_spycloud_states().get(reference)
    check = ("<form method='post' action='/attended/spycloud-check'><input type='hidden' name='reference' value='"
             + escape(reference) + "'><button type='submit' class='ghost sm'>Check SpyCloud (read-only)</button></form>")
    started = ("<p class='note'>A read-only SpyCloud check was started in the automation browser (it opens Edit and "
               "cancels). Reload this page in about 30 seconds.</p>" if notice == "spycloud-started" else "")
    how = ("<p class='meta-line'>Nothing to do: SpyCloud stays ON. Turning it OFF by hand is a Leonardo Development write "
           "and needs the operator's approval (<code>--co " + escape(reference) + " --spycloud-off --confirm-write</code>).</p>")
    if state is None:
        return ("<section class='stat' aria-labelledby='spycloud-title'><div class='stat-head'>"
                "<h2 id='spycloud-title'>SpyCloud</h2></div><p>Not checked yet. Leonardo creates Credential "
                "Exposure tenants with SpyCloud ON and it stays ON; the check is optional.</p>" + started + check + how + "</section>")
    level = str(state["state"])
    outcome = str(state["outcome"])
    message = SPYCLOUD_MESSAGES.get(outcome, "SpyCloud could not be verified. Reason: " + outcome + ".")
    observed = state["observed_at"].strftime("%Y-%m-%d %H:%M")  # type: ignore[union-attr]
    headline = {"on": "SpyCloud is ON (default)", "off": "SpyCloud is OFF (default is ON)"}.get(level, "SpyCloud not verified")
    cls, icon = ("source-ready", "✓") if level == "on" else ("source-warn", "!")
    return ("<section class='stat " + cls + "' aria-labelledby='spycloud-title'><div class='stat-head'>"
            f"<span class='readiness-icon' aria-hidden='true'>{icon}</span>"
            f"<h2 id='spycloud-title'>{escape(headline)}</h2></div><p>{escape(message)}</p>"
            f"<dl><dt>Result</dt><dd><code>{escape(outcome)}</code></dd><dt>Observed</dt><dd>{escape(observed)} "
            f"({escape(str(state['mode']).replace('_', ' '))})</dd></dl>" + started + check + how
            + "<p class='meta-line'>Local record from Leonardo Development; Salesforce is not changed.</p></section>")


def start_attended_scan_status(reference: str) -> bool:
    """Launch one desktop-only, read-only scan-status read (no fill, submit, or create)."""
    if not REFERENCE.fullmatch(reference) or not local_browser_launch_allowed() or not ATTENDED_CE_ONLY_RUNNER.is_file():
        return False
    try:
        subprocess.Popen([sys.executable, str(ATTENDED_CE_ONLY_RUNNER), "--co", reference, "--scan-status"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


SCAN_STATES = {
    "no_scan": ("", "No scan started", "Leonardo shows no recon scan yet; the schedule starts it."),
    "scan_started": ("source-warn", "Scan started", "A recon scan has run or is running. Completion is not confirmed yet."),
    "scan_completed": ("source-ready", "Scan completed", "Assign the Operator Account and turn the scanning settings off."),
    "scan_failed": ("source-blocked", "Scan failed", "Review the tenant in Leonardo Development."),
    "unrecognized": ("source-blocked", "Unrecognized scan status", "Leonardo returned a value this dashboard does not recognize. Check the tenant in Leonardo Development."),
}


SCAN_EXEC_STATES = ("done", "running", "no_executions", "unrecognized")
_SCAN_ENUM = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")


def _scan_executions_from_entry(value: dict[str, object]) -> dict[str, object]:
    """Validated per-execution fields of one stored entry; anything malformed is dropped (the row status stays)."""
    state, raw = value.get("execution_state"), value.get("executions")
    if state not in SCAN_EXEC_STATES or not isinstance(raw, list) or len(raw) > 200:
        return {}
    executions: list[dict[str, object]] = []
    try:
        for item in raw:
            entry: dict[str, object] = {}
            for key in ("campaign_type", "execution_type", "status"):
                text = item.get(key)
                if text is not None and not (isinstance(text, str) and _SCAN_ENUM.fullmatch(text)):
                    return {}
                entry[key] = text
            for key in ("start", "end"):
                text = item.get(key)
                entry[key] = datetime.fromisoformat(text) if text is not None else None
            duration = item.get("duration_ms")
            if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool) or duration < 0):
                return {}
            entry["duration_ms"] = duration
            executions.append(entry)
        since = value.get("running_since")
        since = datetime.fromisoformat(since) if since is not None else None
    except (AttributeError, TypeError, ValueError):
        return {}
    statuses = [e["status"] for e in executions]
    if state == "unrecognized" and statuses and all(s in SCAN_EXEC_DONE for s in statuses):
        # Apply the current confirmed list, so a newly confirmed status (DONE_WITH_ERRORS, owner 2026-10-07)
        # updates an older read without a new Leonardo read.
        state = "done"
    with_errors = state == "done" and any(s in SCAN_EXEC_WITH_ERRORS for s in statuses)
    return {"executions": executions, "execution_state": state, "running_since": since, "with_errors": with_errors}


def _clock(moment: datetime | None) -> str:
    """Local wall-clock text for a stored (UTC) time."""
    if moment is None:
        return "unknown"
    return (moment.astimezone() if moment.tzinfo else moment).strftime("%Y-%m-%d %H:%M")


def _hms(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _scan_executions_html(observation: dict[str, object]) -> str:
    """Headline and per-execution list from the Details > Duration Per Scan read (empty if not read)."""
    state = observation.get("execution_state")
    if state is None:
        return ""
    executions: list[dict[str, object]] = observation["executions"]  # type: ignore[assignment]
    if state == "running":
        headline = f"Running since {_clock(observation.get('running_since'))}"  # type: ignore[arg-type]
    elif state == "done":
        # Owner 2026-10-07: the headline is the total of all scan durations; each scan is listed below with its
        # own duration and whether it finished.
        durations = [e["duration_ms"] for e in executions if isinstance(e["duration_ms"], int)]
        count = len(executions)
        headline = ("Done · total " + _hms(sum(durations)) + f" ({count} scan{'' if count == 1 else 's'})"
                    if durations else "Done")
    elif state == "no_executions":
        headline = "No scan executions yet"
    else:
        headline = "Unrecognized execution status"

    def finished_word(status: object) -> str:
        if status in SCAN_EXEC_WITH_ERRORS:
            return "finished with errors"
        if status in SCAN_EXEC_DONE:
            return "finished"
        if status in SCAN_EXEC_RUNNING:
            return "running"
        return "unknown status " + str(status or "")

    items = "".join(
        f"<li><b>{escape(finished_word(e['status']))}</b> · "
        f"{escape(_hms(e['duration_ms']) if isinstance(e['duration_ms'], int) else 'no duration yet')}"
        f" · started {escape(_clock(e['start']))}"  # type: ignore[arg-type]
        f" · <code>{escape(str(e['campaign_type'] or 'unknown'))}</code></li>"
        for e in executions)
    warning = ("<p class='note' style='color:var(--warn)'>A scan finished with errors (DONE_WITH_ERRORS) — check the "
               "tenant in Leonardo Development.</p>" if observation.get("with_errors") else "")
    return (f"<p><b>{escape(headline)}</b></p>" + warning + (f"<ul class='meta-line'>{items}</ul>" if items else ""))


def attended_scan_statuses() -> dict[str, dict[str, object]]:
    """Load the local scan observations; absence means none. Malformed entries are dropped."""
    try:
        raw = json.loads(SCAN_STATUS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    statuses: dict[str, dict[str, object]] = {}
    for reference, value in raw.items():
        try:
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict):
                continue
            if value.get("state") not in SCAN_STATES:
                continue
            observed, expires = datetime.fromisoformat(value["observed_at"]), datetime.fromisoformat(value["expires_at"])
            status_enum = value.get("status_enum")
            if status_enum is not None and not (isinstance(status_enum, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", status_enum)):
                continue
            last = value.get("last_recon_scan")
            if last is not None:
                datetime.fromisoformat(str(last))
            duration = value.get("duration_ms")
            if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool) or duration < 0):
                continue
            state = value["state"]
            if state in ("scan_started", "scan_completed", "scan_failed"):
                # Apply the current confirmed lists, so a newly confirmed value updates old reads.
                state = ("scan_completed" if status_enum in SCAN_STATUS_COMPLETED
                         else "scan_failed" if status_enum in SCAN_STATUS_FAILED else "scan_started")
            statuses[reference] = {"state": state, "status_enum": status_enum, "last_recon_scan": last,
                                   "duration_ms": duration, "observed_at": observed, "expires_at": expires,
                                   **_scan_executions_from_entry(value)}
        except (KeyError, TypeError, ValueError):
            continue
    return statuses


SCAN_CHECK_TEXT = {
    "scan_status_recorded": "the scan status was read and saved",
    "scan_status_route_unsupported": "this CO's product/type has no scan-status read (unsupported route)",
    "scan_status_renewal_mirror_missing": "this renewal has no development mirror tenant to read; create the mirror first",
    "duplicate_search_schema_unavailable": "Leonardo's tenant search page did not look as expected; nothing was read",
    "scan_status_schema_unavailable": "Leonardo's scan columns did not look as expected; nothing was read",
    "scan_status_tenant_not_found": "no matching tenant was found in Leonardo Development",
    "scan_status_not_onboarded": "this CO is not onboarded yet, so there is no tenant to read",
    "scan_status_not_applicable": "this tenant has no scan (Credential Exposure only)",
    "scan_status_source_unavailable": "the Salesforce source could not be read",
    "scan_status_write_unavailable": "the scan status was read but could not be saved locally",
    "development_login_timeout": "the Leonardo Development sign-in timed out; sign in again in the automation browser",
    "leonardo_session_expired": "the Leonardo session expired; sign in again in the automation browser",
    "playwright_runtime_unavailable": "the automation browser runtime is not available on this computer",
    "attended_ce_runner_unavailable": "the attended runner is not available on this computer",
    "scan_status_tenant_ambiguous": "several tenants matched and they could not be narrowed to one; nothing was read",
    "scan_status_executions_unavailable": ("the scan status was read and saved; the per-execution details (Duration Per "
                                           "Scan) could not be opened"),
    "scan_status_executions_id_mismatch": ("the scan status was read and saved; the per-execution details belonged to "
                                           "another tenant and were ignored"),
}
# The row status was read and saved, only the optional executions read failed: "partial", not "failed".
SCAN_CHECK_PARTIAL = frozenset({"scan_status_executions_unavailable", "scan_status_executions_id_mismatch"})


def _read_age_text(moment: datetime, now: datetime) -> str:
    """Plain age such as "3 days ago" (naive local clock; a timezone-aware time is converted to local)."""
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    seconds = int((now - moment).total_seconds())
    if seconds < 0:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return "just now"


def _scan_check_for(reference: str) -> dict[str, str] | None:
    """The latest scan-status check the runner recorded for this CO (local state), else None."""
    try:
        check = load_check_state().get(reference)
    except (ValueError, OSError):
        return None
    return check if isinstance(check, dict) and check.get("kind") == "scan_status" else None


def _scan_check_line(check: dict[str, str] | None) -> str:
    """"Last scan-status read: ok|failed - reason - local time" from the latest recorded check."""
    if check is None:
        return ""
    result = check.get("result")
    moment_text = check.get("completed_on") or check.get("started_on") or ""
    try:
        when = _clock(datetime.fromisoformat(moment_text))
    except ValueError:
        when = "unknown time"
    if result is None:
        return ("<p class='note'>Last scan-status read: <b>started</b> · no result recorded yet (still running or it "
                "ended without a result) · " + escape(when) + "</p>")
    ok = result == "scan_status_recorded"
    partial = result in SCAN_CHECK_PARTIAL
    reason = SCAN_CHECK_TEXT.get(result, result)
    style = "" if ok else " style='color:var(--warn)'" if partial else " style='color:var(--bad)'"
    word = "ok" if ok else "partial" if partial else "failed"
    return ("<p class='note'" + style + ">Last scan-status read: <b>" + word + "</b> · "
            + escape(reason) + " · " + escape(when) + "</p>")


def _scan_status_section(reference: str, notice: str = "", now: datetime | None = None) -> str:
    """Leonardo scan-status card for an onboarded CO (local, read-only observation)."""
    observation = attended_scan_statuses().get(reference)
    now = now or datetime.now()
    check_line = _scan_check_line(_scan_check_for(reference))
    refresh = ("<form method='post' action='/attended/scan-status-refresh'><input type='hidden' name='reference' value='"
               + escape(reference) + "'><button type='submit' class='ghost sm'>Refresh scan status</button></form>")
    started = ("<p class='note'>A read-only scan-status read was started in the automation browser. "
               "Reload this page in about 30 seconds.</p>" if notice == "started" else "")
    if observation is None:
        return ("<section class='stat' aria-labelledby='scan-status-title'><div class='stat-head'>"
                "<h2 id='scan-status-title'>Leonardo scan status</h2></div>"
                "<p>Not read yet. Refresh reads the tenant's scan fields from Leonardo Development (read-only).</p>"
                + check_line + started + refresh + "</section>")
    cls, title, message = SCAN_STATES[str(observation["state"])]
    stale = now > observation["expires_at"]  # type: ignore[operator]
    icon_style = "" if cls else " style='background:#9aa1ad'"
    icon = {"source-ready": "✓", "source-blocked": "!", "source-warn": "!"}.get(cls, "–")
    duration = observation["duration_ms"]
    rows = (f"<dt>Last recon scan</dt><dd>{escape(str(observation['last_recon_scan'] or 'None'))}</dd>"
            f"<dt>Leonardo status</dt><dd><code>{escape(str(observation['status_enum'] or 'None'))}</code></dd>"
            f"<dt>Duration</dt><dd>{escape(f'{duration / 3_600_000:.1f} h' if isinstance(duration, int) else 'None')}</dd>"
            f"<dt>Observed</dt><dd>{escape(observation['observed_at'].strftime('%Y-%m-%d %H:%M'))}"  # type: ignore[union-attr]
            f" · read {escape(_read_age_text(observation['observed_at'], now))}"  # type: ignore[arg-type]
            + (" · <b>stale, refresh</b> (this read is past its expiry; the status may have changed)" if stale else "")
            + "</dd>")
    return ("<section class='stat" + (" " + cls if cls else "") + "' aria-labelledby='scan-status-title'><div class='stat-head'>"
            f"<span class='readiness-icon' aria-hidden='true'{icon_style}>{icon}</span>"
            f"<h2 id='scan-status-title'>Leonardo scan status · {escape(title)}</h2></div><p>{escape(message)}</p>"
            + _scan_executions_html(observation) + "<dl>" + rows + "</dl>" + check_line + started + refresh
            + "<p class='meta-line'>Local observation from Leonardo Development; Salesforce is not changed.</p></section>")


def route_for(row: dict[str, str | None]) -> str | None:
    """Return the attended route engine value for exact Product/Type values only."""
    product, onboarding_type = row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")
    if product == CE_ROUTE_PRODUCT and onboarding_type == CE_ROUTE_TYPE:
        return CE_ENGINE
    if product == SURFACE_ROUTE_PRODUCT and onboarding_type == SURFACE_ROUTE_TYPE:
        return SURFACE_ENGINE
    if product == CASE3_ROUTE_PRODUCT and onboarding_type == CASE3_ROUTE_TYPE:
        return CASE3_ENGINE
    return None


# Display-read cache (owner decision 2026-10-01, for page speed). Each `sf` CLI
# call costs about 4 s, so GET pages reuse a Salesforce read for up to five
# minutes. It applies only while a GET handler renders (_display_reads), keeps
# results in memory only (never disk or logs), and is cleared by a Refresh or
# any POST. Starts and the runner never use it: they re-read Salesforce and
# re-check the source revision themselves.
DISPLAY_READ_TTL_SECONDS = 300.0
_display_reads: contextvars.ContextVar[bool] = contextvars.ContextVar("display_reads", default=False)
_display_read_times: contextvars.ContextVar[list[float] | None] = contextvars.ContextVar("display_read_times", default=None)
_display_cache: dict[tuple[object, ...], tuple[float, float, Future]] = {}
_display_cache_lock = Lock()
_display_prefetch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="display-read")


def clear_display_cache() -> None:
    with _display_cache_lock:
        _display_cache.clear()


def _display_cached(fn: Any) -> Any:
    """Cache fn(*args) for display GETs; outside a display GET, call fn directly.

    Concurrent callers share one in-flight read. A failed read is not cached.
    Callers get a deep copy, so a page cannot change a cached value.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any) -> Any:
        if not _display_reads.get():
            return fn(*args)
        key = (fn.__name__, *args)
        now = monotonic()
        with _display_cache_lock:
            entry = _display_cache.get(key)
            stale = (entry is None or entry[0] <= now
                     or (entry[2].done() and entry[2].exception() is not None))
            if stale:
                entry = (now + DISPLAY_READ_TTL_SECONDS, datetime.now().timestamp(), Future())
                _display_cache[key] = entry
        _expires, read_at, future = entry
        if stale:
            try:
                future.set_result(fn(*args))
            except BaseException as error:
                future.set_exception(error)
                with _display_cache_lock:
                    if _display_cache.get(key) is entry:
                        del _display_cache[key]
                raise
        value = future.result(timeout=120)
        times = _display_read_times.get()
        if times is not None:
            times.append(read_at)
        return copy.deepcopy(value)
    return wrapper


def prefetch_display_read(fn: Any, *args: Any) -> None:
    """Start a cached display read in the background so it overlaps other reads."""
    def task() -> None:
        _display_reads.set(True)
        try:
            fn(*args)
        except Exception:
            pass  # The page's own call re-raises the failure on the request thread.
    _display_prefetch_pool.submit(task)


def peek_display_cache(name: str, *args: Any) -> Any:
    """A finished, unexpired cached value (deep copy), or None. Never reads Salesforce."""
    with _display_cache_lock:
        entry = _display_cache.get((name, *args))
    if entry is None or entry[0] <= monotonic() or not entry[2].done() or entry[2].exception() is not None:
        return None
    return copy.deepcopy(entry[2].result())


def display_read_at() -> str:
    """Time of the oldest Salesforce read used by this page (HH:MM:SS)."""
    times = _display_read_times.get()
    stamp = min(times) if times else datetime.now().timestamp()
    return datetime.fromtimestamp(stamp).strftime("%H:%M:%S")


@_display_cached
def evaluate_surface_fill_preflight(reference: str, route: str = SURFACE_ENGINE) -> SurfaceScopePreflight:
    """Fresh read of the Surface-only source through the runner's own reader.

    The dashboard and the runner compute the scope with the same code. Any
    source failure is a blocker (its stable code), never a partial scope.
    """
    if not REFERENCE.fullmatch(reference):
        raise ReadUnavailable()
    if route == CASE3_ENGINE:
        return _evaluate_case3_preflight(reference)
    try:
        source = surface_fill_source(reference)
    except RuntimeError as error:
        return SurfaceScopePreflight(reference, "", (str(error),))
    try:
        scope = surface_scope_summary(source)
    except ValueError as error:
        return SurfaceScopePreflight(reference, source.source_revision, (str(error),),
                                     core_plus_present=source.core_plus_present)
    return SurfaceScopePreflight(reference, source.source_revision, (), scope, source.core_plus_present)


def _evaluate_case3_preflight(reference: str) -> SurfaceScopePreflight:
    """Case 3: the Surface scope plus the CE overlay; a term mismatch says why (1a)."""
    try:
        source = case3_fill_source(reference)
    except RuntimeError as error:
        return SurfaceScopePreflight(reference, "", (str(error),), route=CASE3_ENGINE)
    try:
        problem = case3_term_problem(source)
    except ValueError:
        problem = None
    try:
        scope = case3_scope_summary(source)
    except ValueError as error:
        return SurfaceScopePreflight(reference, source.source_revision, (str(error),), core_plus_present=True,
                                     route=CASE3_ENGINE, blocker_details=(problem,) if problem else ())
    return SurfaceScopePreflight(reference, source.source_revision, (), scope, True, route=CASE3_ENGINE)


def evaluate_surface_start(acknowledged_revision: str | None, scope_digest: str | None,
                           evaluation: SurfaceScopePreflight, state: dict[str, dict[str, str]]) -> str:
    """One-time, revision- and scope-bound gate for the Surface-only runner start."""
    decision = evaluate_ce_only_start(acknowledged_revision, evaluation, state)  # type: ignore[arg-type]
    if decision != "start":
        return decision
    if not scope_digest or scope_digest != evaluation.scope_digest:
        return "scope_changed"
    return "start"


def load_attended_reminders() -> dict[str, dict[str, str]]:
    """Load local reminder acknowledgements; absence means none, corruption fails closed."""
    try:
        raw = json.loads(ATTENDED_REMINDERS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ReadUnavailable() from exc
    if not isinstance(raw, dict):
        raise ReadUnavailable()
    reminders: dict[str, dict[str, str]] = {}
    allowed = set(REMINDER_FIELDS.values())
    for reference, value in raw.items():
        if (not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict)
                or set(value) - allowed):
            raise ReadUnavailable()
        for item in value.values():
            if not isinstance(item, str):
                raise ReadUnavailable()
            try:
                datetime.fromisoformat(item)
            except ValueError as exc:
                raise ReadUnavailable() from exc
        reminders[reference] = dict(value)
    return reminders


def load_user_created_confirmations() -> dict[str, dict[str, object]]:
    """Local manual confirmations; absence means none, an unreadable file counts as none (fails closed)."""
    try:
        raw = json.loads(USER_CREATED_CONFIRMATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for reference, value in raw.items():
        if (isinstance(reference, str) and REFERENCE.fullmatch(reference) and isinstance(value, dict)
                and value.get("confirmed") is True and isinstance(value.get("confirmed_on"), str)
                and isinstance(value.get("confirmed_by"), str)):
            result[reference] = {"confirmed": True, "confirmed_on": value["confirmed_on"][:10],
                                 "confirmed_by": value["confirmed_by"][:80]}
    return result


def set_user_created_confirmation(reference: str, confirmed: bool, operator: str | None) -> None:
    """Record or undo the manual confirmation (local only; no Leonardo or Salesforce action)."""
    if not REFERENCE.fullmatch(reference):
        raise ValueError("invalid_confirmation_record")
    records = load_user_created_confirmations()
    if confirmed:
        records[reference] = {"confirmed": True, "confirmed_on": datetime.now().date().isoformat(),
                              "confirmed_by": operator or "operator"}
    else:
        records.pop(reference, None)
    temporary = USER_CREATED_CONFIRMATION_PATH.with_name(USER_CREATED_CONFIRMATION_PATH.name + ".tmp")
    temporary.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, USER_CREATED_CONFIRMATION_PATH)


def record_attended_reminder(reference: str, kind: str) -> None:
    """Record one local acknowledgement (no Leonardo or Salesforce action)."""
    if not REFERENCE.fullmatch(reference) or kind not in REMINDER_FIELDS:
        raise ValueError("invalid_reminder_record")
    reminders = load_attended_reminders()
    reminders.setdefault(reference, {})[REMINDER_FIELDS[kind]] = datetime.now().isoformat(timespec="seconds")
    temporary = ATTENDED_REMINDERS_PATH.with_name(ATTENDED_REMINDERS_PATH.name + ".tmp")
    temporary.write_text(json.dumps(reminders, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, ATTENDED_REMINDERS_PATH)


def evaluate_ce_only_start(acknowledged_revision: str | None, evaluation: CredentialExposureFillPreflight,
                           state: dict[str, dict[str, str]]) -> str:
    """One-time, revision-bound gate for the attended CE-only runner start."""
    if acknowledged_revision is None or not acknowledged_revision:
        return "revision_acknowledgement_missing"
    if not evaluation.eligible_for_fill_review:
        return "preflight_blocked"
    if acknowledged_revision != evaluation.source_revision:
        return "source_revision_changed"
    record = state.get(evaluation.reference)
    if record is not None and record.get("source_revision") == evaluation.source_revision:
        return "revision_already_acknowledged"
    blocker = start_blocker(record)
    if blocker is not None:
        return blocker
    return "start"


# Owner decisions 2026-10-01: which Leonardo identifier belongs in which
# Salesforce CO field, per route (Case 3 writes both, decision 3a). Routes not
# listed have no decided mapping and fail closed.
SALESFORCE_ID_MAPPING: dict[str, tuple[tuple[str, str, str], ...]] = {
    # route: ((readback key, Salesforce field, label), ...)
    CE_ENGINE: (("account_uuid", "Account_UUID__c", "Account UUID"),),
    SURFACE_ENGINE: (("surface_account_id", "Surface_Account_ID__c", "Surface Account ID"),),
    CASE3_ENGINE: (("surface_account_id", "Surface_Account_ID__c", "Surface Account ID"),
                   ("account_uuid", "Account_UUID__c", "Account UUID")),
    # Owner decision 2026-10-07: renewals (Cases 4-6) carry both, like Case 3. The Dev mirror's IDs are shown on the
    # page only; Salesforce keeps the production IDs (ID_WRITEBACK_ENABLED stays off).
    **{engine: (("surface_account_id", "Surface_Account_ID__c", "Surface Account ID"),
                ("account_uuid", "Account_UUID__c", "Account UUID")) for engine in sorted(RENEWAL_ENGINES)},
}


def salesforce_id_writeback_plan(route: str | None, readback: dict[str, str] | None,
                                 row: dict[str, str | None]) -> dict[str, object]:
    """Compare the captured Leonardo identifiers with the CO's mapped Salesforce fields.

    Status is one of "mapping_not_decided", "not_captured", "ready_to_write"
    (at least one mapped field empty, none different), "matches_salesforce"
    (all equal), or "conflict" (any field holds a different value). "items"
    lists every mapped field; a single-field route also carries its field,
    label, salesforce_value, and leonardo_value at the top level. It never
    writes anything.
    """
    mapping = SALESFORCE_ID_MAPPING.get(route or "")
    if mapping is None:
        return {"status": "mapping_not_decided"}
    items: list[dict[str, str]] = []
    for key, field, label in mapping:
        item = {"key": key, "field": field, "label": label, "salesforce_value": (row.get(field) or "").strip()}
        leonardo_value = (readback or {}).get(key)
        if leonardo_value:
            item["leonardo_value"] = leonardo_value
            current = item["salesforce_value"]
            # The UUID is hexadecimal, so letter case does not change its identity.
            same = current.casefold() == leonardo_value.casefold() if key == "account_uuid" else current == leonardo_value
            item["status"] = "ready_to_write" if not current else ("matches_salesforce" if same else "conflict")
        else:
            item["status"] = "not_captured"
        items.append(item)
    statuses = {item["status"] for item in items}
    if "not_captured" in statuses:
        status = "not_captured"
    elif "conflict" in statuses:
        status = "conflict"
    elif "ready_to_write" in statuses:
        status = "ready_to_write"
    else:
        status = "matches_salesforce"
    plan: dict[str, object] = {"status": status, "items": items}
    if len(items) == 1:
        plan.update({k: v for k, v in items[0].items() if k in ("field", "label", "salesforce_value", "leonardo_value")})
    return plan


def salesforce_id_already_present(route: str | None, row: dict[str, str | None]) -> bool:
    """True when any of the route's mapped Salesforce ID fields already holds a value.

    Such a CO was onboarded before (usually in production); the runner refuses
    to create or dry-run it (salesforce_id_already_present).
    """
    mapping = SALESFORCE_ID_MAPPING.get(route or "", ())
    return any((row.get(field) or "").strip() for _key, field, _label in mapping)


_SALESFORCE_ID_PRESENT_SECTION = (
    "<section class='manual-action' aria-labelledby='sf-id-present-title'><div><h2 id='sf-id-present-title'>Already has an ID in Salesforce</h2>"
    "<p>The Salesforce ID field for this route is already set, so this CO was probably onboarded in production. "
    "The attended runner will not create or dry-run it.</p></div>"
    "<button type='button' disabled aria-disabled='true'>Start onboarding blocked</button>"
    "<p class='manual-blocker'>Review the existing tenant manually. Nothing was sent to Leonardo or Salesforce.</p></section>"
)


SALESFORCE_ID_STATUS = {
    "ready_to_write": ("source-ready", "✓", "Captured (env dev)",
                       "Captured from Leonardo Development and shown here only. The Salesforce field stays empty."),
    "matches_salesforce": ("source-ready", "✓", "Matches Salesforce", "The Salesforce field already holds the captured Leonardo Development value."),
    "conflict": ("source-warn", "!", "Salesforce has the production ID",
                 "Salesforce holds a different ID (the production tenant). It stays authoritative; "
                 "the Leonardo Development ID is test evidence only and is never written."),
    "not_captured": ("", "–", "Not captured yet", "No local Leonardo readback for this CO yet. It is captured when the tenant is created, or by a read-only readback of an existing tenant."),
}


# Salesforce ID writeback (owner decision (b) 2026-10-01): during the pilot a
# captured Leonardo Development ID may be written into its EMPTY mapped field
# only. One operator review + one confirmation per write, bound to the fresh
# source revision; a read-after-write must show the exact value. An uncertain
# outcome is recorded and never retried automatically.
ID_WRITEBACK_ACK_TTL_SECONDS = 10 * 60
# Owner decision 2026-10-01 (later the same day): Leonardo Development IDs stay
# on the dashboard only, labelled env dev; nothing is written to Salesforce.
# This replaces pilot decision (b). The guarded write path stays tested but off.
ID_WRITEBACK_ENABLED = False
ID_ENVIRONMENT_LABEL = "dev"
ID_WRITEBACK_PATH = state_file(Path(__file__).resolve().parents[1] / "integration" / "attended_salesforce_id_writebacks.json")
ID_VALUE_PATTERNS = {"Surface_Account_ID__c": r"[a-f0-9]{24}", "Account_UUID__c": r"[a-f0-9]{32}"}
_id_writeback_acks: dict[str, tuple[str, str, float]] = {}
_id_writeback_lock = Lock()


@dataclass(frozen=True, slots=True)
class IdWritebackEvaluation:
    reference: str
    co_id: str
    source_revision: str
    fields: tuple[str, ...]
    values: tuple[str, ...]
    blocker: str = ""

    @property
    def field(self) -> str:
        return ", ".join(self.fields)

    @property
    def value(self) -> str:
        return ", ".join(self.values)

    @property
    def binding(self) -> str:
        return sha256("|".join((self.reference, self.co_id, self.source_revision, *self.fields, *self.values)).encode()).hexdigest()


def salesforce_id_writebacks() -> dict[str, dict[str, str]]:
    """Local writeback records (field, value hash, time, result); absence means none."""
    try:
        raw = json.loads(ID_WRITEBACK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and REFERENCE.fullmatch(k) and isinstance(v, dict)
            and all(isinstance(item, str) for item in v.values())}


def _record_id_writeback(reference: str, field: str, value: str, result: str) -> None:
    records = salesforce_id_writebacks()
    records[reference] = {"field": field, "value_sha256": sha256(value.encode()).hexdigest(),
                          "recorded_at": datetime.now().isoformat(timespec="seconds"), "result": result}
    ID_WRITEBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ID_WRITEBACK_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, ID_WRITEBACK_PATH)


def evaluate_id_writeback(reference: str) -> IdWritebackEvaluation:
    """Fresh, fixed-field read; the write is allowed only for a Ready-to-write plan."""
    if not REFERENCE.fullmatch(reference):
        raise ReadUnavailable()
    response = sf_json(["data", "query", "--query",
                        "SELECT Id, Name, LastModifiedDate, Onboarding_Product__c, Onboarding_Type__c, "
                        "Surface_Account_ID__c, Account_UUID__c FROM Customer_Onboarding__c WHERE Name = '"
                        + reference + "' LIMIT 2", "--json"])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or len(records) != 1:  # type: ignore[index]
            raise ReadUnavailable()
        record = records[0]
        if record.get("Name") != reference:
            raise ReadUnavailable()
        row = {key: (value if isinstance(value, str) else None) for key, value in record.items() if key != "attributes"}
    except (KeyError, TypeError, AttributeError):
        raise ReadUnavailable() from None
    co_id, revision = row.get("Id") or "", row.get("LastModifiedDate") or ""
    readback = attended_leonardo_readbacks().get(reference)
    plan = salesforce_id_writeback_plan(route_for(row), readback, row)
    pending = [item for item in plan.get("items", ()) if item["status"] == "ready_to_write"]  # type: ignore[union-attr]
    fields = tuple(item["field"] for item in pending)
    values = tuple(item["leonardo_value"] for item in pending)
    blocker = ""
    if plan["status"] != "ready_to_write":
        blocker = str(plan["status"])
    elif not re.fullmatch(r"[A-Za-z0-9]{15,18}", co_id) or not revision:
        blocker = "source_invalid"
    elif not fields or not all(re.fullmatch(ID_VALUE_PATTERNS.get(f, r"(?!)"), v) for f, v in zip(fields, values)):
        blocker = "value_invalid"
    elif salesforce_id_writebacks().get(reference, {}).get("result") == "write_uncertain":
        blocker = "previous_write_uncertain"
    return IdWritebackEvaluation(reference, co_id, revision, fields, values, blocker)


def issue_id_writeback_ack(evaluation: IdWritebackEvaluation, *, now: float | None = None) -> str:
    nonce = token_urlsafe(24)
    with _id_writeback_lock:
        _id_writeback_acks[evaluation.reference] = (evaluation.binding, nonce,
                                                     (monotonic() if now is None else now) + ID_WRITEBACK_ACK_TTL_SECONDS)
    return nonce


def consume_id_writeback_ack(evaluation: IdWritebackEvaluation, nonce: str, *, now: float | None = None) -> bool:
    with _id_writeback_lock:
        ack = _id_writeback_acks.pop(evaluation.reference, None)
    return bool(ack and isinstance(nonce, str) and ack[0] == evaluation.binding and ack[1] == nonce
                and ack[2] > (monotonic() if now is None else now))


def write_salesforce_id_after_confirmation(evaluation: IdWritebackEvaluation, nonce: str) -> str:
    """Write the one mapped field, then read it back.

    Returns "written_verified", "write_blocked" (nothing was sent),
    "write_rejected" (Salesforce refused the update), or "write_uncertain"
    (the update may have been applied; never retried automatically).
    """
    if not ID_WRITEBACK_ENABLED or evaluation.blocker or not consume_id_writeback_ack(evaluation, nonce):
        return "write_blocked"
    field, value = evaluation.field, evaluation.value
    assignments = " ".join(f + "=" + v for f, v in zip(evaluation.fields, evaluation.values))
    try:
        update = sf_write_json(["data", "update", "record", "--sobject", "Customer_Onboarding__c",
                                "--record-id", evaluation.co_id, "--values", assignments, "--json"])
        if update.get("status") != 0:  # type: ignore[union-attr]
            result = "write_rejected"
            _record_id_writeback(evaluation.reference, field, value, result)
            return result
    except (WriteUnavailable, AttributeError):
        result = "write_uncertain"
        _record_id_writeback(evaluation.reference, field, value, result)
        return result
    try:
        readback = sf_json(["data", "query", "--query", "SELECT Name, " + ", ".join(evaluation.fields)
                            + " FROM Customer_Onboarding__c WHERE Id = '" + evaluation.co_id + "' LIMIT 2", "--json"])
        rows = readback["result"]["records"]  # type: ignore[index]
        verified = bool(readback["status"] == 0 and isinstance(rows, list) and len(rows) == 1  # type: ignore[index]
                        and rows[0].get("Name") == evaluation.reference
                        and all(rows[0].get(f) == v for f, v in zip(evaluation.fields, evaluation.values)))
    except (ReadUnavailable, KeyError, TypeError, AttributeError):
        verified = False
    result = "written_verified" if verified else "write_uncertain"
    _record_id_writeback(evaluation.reference, field, value, result)
    return result


ID_WRITEBACK_RESULTS = {
    "written_verified": ("verified", "Written to Salesforce and read back"),
    "write_rejected": ("blocked", "Salesforce rejected the update; nothing was changed"),
    "write_uncertain": ("blocked", "The update may or may not have been applied. Check the CO in Salesforce; it will not be retried"),
    "write_blocked": ("blocked", "Not written: the confirmation expired, was already used, or the source changed. Review again"),
}


def page_id_writeback_confirmation(evaluation: IdWritebackEvaluation, nonce: str) -> str:
    ref = escape(evaluation.reference)
    body = ("<h2>Write the Leonardo Development ID to Salesforce</h2>"
            "<dl><dt>CO</dt><dd>" + ref + "</dd>"
            + "".join("<dt>Salesforce field</dt><dd><code>" + escape(f) + "</code> (currently empty)</dd>"
                      "<dt>Value (Leonardo Development)</dt><dd><code>" + escape(v) + "</code></dd>"
                      for f, v in zip(evaluation.fields, evaluation.values)) +
            "<dt>Source revision</dt><dd>" + escape(evaluation.source_revision) + "</dd></dl>"
            "<p class='note'>Confirming updates only the field(s) above, then reads them back. It does not change the stage, "
            "comments, or any other field. This confirmation expires in 10 minutes and is invalidated by any change to the CO.</p>"
            "<form method='post' action='/attended/salesforce-id-writeback-confirm'>"
            "<input type='hidden' name='reference' value='" + ref + "'><input type='hidden' name='nonce' value='" + escape(nonce) + "'>"
            "<button type='submit'>Confirm write to Salesforce</button></form>"
            "<p><a href='/co/" + ref + "'>Cancel and return to " + ref + "</a></p>")
    return _app_shell(evaluation.reference + " Salesforce ID",
                      "<a class='crumb' href='/co/" + ref + "'>&larr; " + ref + "</a><div class='page-head'><h1>" + ref
                      + "</h1></div><section class='card'>" + body + "</section>", active="onboardings")


def _salesforce_ids_section(route: str | None, readback: dict[str, str] | None, row: dict[str, str | None],
                            reference: str = "", extra: str = "", force_open: bool = False) -> str:
    """Folded Salesforce IDs panel (``extra``: the local readback block); a warning opens it.

    ``force_open`` opens it at the User Created stage, where the next step is the production ID update.
    """
    plan = salesforce_id_writeback_plan(route, readback, row)
    status = plan["status"]

    def folded(cls: str, icon: str, icon_style: str, title: str, body: str) -> str:
        return ("<details class='more" + (" " + cls if cls else "") + "'"
                + (" open" if cls in ("source-warn", "source-blocked") or force_open else "") + " aria-labelledby='salesforce-ids-title'>"
                f"<summary><span class='readiness-icon' aria-hidden='true'{icon_style}>{icon}</span>"
                f"<h2 id='salesforce-ids-title' class='sum-h'>Salesforce IDs · {escape(title)}</h2></summary>"
                "<div class='body'>" + body + extra + "</div></details>")
    if status == "mapping_not_decided":
        if readback is None:
            return ""
        return folded("", "–", " style='background:#9aa1ad'", "Mapping not decided",
                      "<p class='note'>No owner-approved Salesforce field mapping exists for this route. "
                      "Nothing is proposed for Salesforce.</p>")
    cls, icon, title, message = SALESFORCE_ID_STATUS[status]
    icon_style = "" if cls else " style='background:#9aa1ad'"
    rows = f"<dt>Environment</dt><dd><span class='chip chip-info'>{escape(ID_ENVIRONMENT_LABEL)}</span> Leonardo Development</dd>"
    for item in plan["items"]:  # type: ignore[union-attr]
        rows += f"<dt>Salesforce field</dt><dd><code>{escape(item['field'])}</code></dd>"
        if "leonardo_value" in item:
            rows += (f"<dt>{escape(item['label'])} (<b>Leonardo Development</b>)</dt>"
                     f"<dd><code style='user-select:all'>{escape(item['leonardo_value'])}</code></dd>")
        rows += f"<dt>Current Salesforce value</dt><dd>{escape(item['salesforce_value']) if item['salesforce_value'] else 'Empty'}</dd>"
    mapped = {key for key, _field, _label in SALESFORCE_ID_MAPPING[route or ""]}
    for other, other_label in (("surface_account_id", "Surface Account ID"), ("account_uuid", "Account UUID")):
        if other not in mapped and readback is not None and readback.get(other):
            rows += f"<dt>Also captured (not written)</dt><dd>{other_label} (Leonardo Development): <code>{escape(readback[other])}</code></dd>"
    reference = reference or row.get("Name") or ""
    last = salesforce_id_writebacks().get(reference) if REFERENCE.fullmatch(reference) else None
    note = ("Dashboard only: Leonardo Development IDs (env " + ID_ENVIRONMENT_LABEL
            + ") are never written to Salesforce.")
    action = ""
    if last is not None and last.get("result") in ID_WRITEBACK_RESULTS:
        note = ("Last write " + ID_WRITEBACK_RESULTS[last["result"]][1].split(";")[0].split(".")[0].lower()
                + " · " + last.get("recorded_at", ""))
    if ID_WRITEBACK_ENABLED and status == "ready_to_write" and (last is None or last.get("result") != "write_uncertain"):
        action = ("<form method='post' action='/attended/salesforce-id-writeback-review'><input type='hidden' name='reference' value='"
                  + escape(reference) + "'><button type='submit' class='ghost'>Review write to Salesforce</button></form>")
    return folded(cls, icon, icon_style, title,
                  f"<p class='note'>{escape(message)}</p><p class='login-safety'>" + escape(note) + "</p>"
                  "<dl>" + rows + "</dl>" + action)


def attended_leonardo_readbacks() -> dict[str, dict[str, str]]:
    """Load optional local evidence; absence means no evidence, never a source failure."""
    try:
        raw = json.loads(ATTENDED_LEONARDO_READBACK_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict): raise ValueError()
        readbacks: dict[str, dict[str, str]] = {}
        for reference, value in raw.items():
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict): raise ValueError()
            account_id, account_uuid = value.get("surface_account_id"), value.get("account_uuid")
            state, observed, source = value.get("leonardo_state"), value.get("observed_on"), value.get("source")
            if (not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9]{16,64}", account_id)
                    or not isinstance(account_uuid, str) or not re.fullmatch(r"[a-f0-9]{32}", account_uuid)
                    or state not in LEONARDO_READBACK_STATES or not isinstance(observed, str)
                    or source != "Leonardo Development Details readback"):
                raise ValueError()
            date.fromisoformat(observed)
            readbacks[reference] = {"surface_account_id": account_id, "account_uuid": account_uuid,
                                    "leonardo_state": state, "observed_on": observed, "source": source}
        return readbacks
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ReadUnavailable() from exc


def open_attended_leonardo_tenant_management(*, opener: object = webbrowser.open_new_tab) -> bool:
    """Open the exact Development tenant-management route after an operator POST.

    The browser resolves the active session: it reaches tenant management when
    authenticated or redirects to its login flow when it is not. This dashboard
    deliberately does not inspect VPN state, browser cookies, or credentials.
    """
    if not local_browser_launch_allowed():
        return False
    try:
        return bool(opener(LEONARDO_DEVELOPMENT_TENANT_MANAGEMENT))  # type: ignore[operator]
    except webbrowser.Error:
        return False


def open_attended_production_backoffice_login(*, opener: object = webbrowser.open_new_tab) -> bool:
    """Open only the production login page for a human renewal preflight."""
    if not local_browser_launch_allowed():
        return False
    try:
        return bool(opener(PRODUCTION_BACKOFFICE_LOGIN))  # type: ignore[operator]
    except webbrowser.Error:
        return False


def source_ready_to_onboard(row: dict[str, str | None]) -> bool:
    """The dashboard's source-ready state follows the approved Salesforce rule."""
    return row.get("Onboarding_Approval_Status__c") == "Approved"


def _source_revision_token(reference: str, row: dict[str, str | None]) -> str | None:
    revision = row.get("LastModifiedDate")
    if not REFERENCE.fullmatch(reference) or not isinstance(revision, str) or not revision:
        return None
    return sha256(f"{reference}\x00{revision}".encode("utf-8")).hexdigest()


def grant_manual_start_ack(reference: str, row: dict[str, str | None], *, now: float | None = None) -> str | None:
    """Record a short-lived attended-session acknowledgement for one revision."""
    token = _source_revision_token(reference, row)
    if token is None or not source_ready_to_onboard(row):
        return None
    issued = monotonic() if now is None else now
    nonce = token_urlsafe(24)
    with _manual_start_lock:
        _manual_start_acks[reference] = (token, nonce, issued + MANUAL_START_ACK_TTL_SECONDS)
    return nonce


def manual_start_ack_is_active(reference: str, row: dict[str, str | None], *, now: float | None = None) -> bool:
    """Fail closed on expiry, source drift, or a missing session acknowledgement."""
    token = _source_revision_token(reference, row)
    current = monotonic() if now is None else now
    with _manual_start_lock:
        ack = _manual_start_acks.get(reference)
        if ack is None or token is None or not source_ready_to_onboard(row) or ack[0] != token or ack[2] <= current:
            _manual_start_acks.pop(reference, None)
            return False
        return True


def manual_start_ack_nonce(reference: str, row: dict[str, str | None], *, now: float | None = None) -> str | None:
    """Return the one-time acknowledgement nonce only while its revision is current."""
    if not manual_start_ack_is_active(reference, row, now=now):
        return None
    with _manual_start_lock:
        ack = _manual_start_acks.get(reference)
        return ack[1] if ack is not None else None


def consume_manual_start_ack(reference: str, row: dict[str, str | None], nonce: str, *, now: float | None = None) -> bool:
    """Consume exactly one matching acknowledgement before opening the manual route."""
    if not isinstance(nonce, str) or not nonce:
        return False
    token = _source_revision_token(reference, row)
    current = monotonic() if now is None else now
    with _manual_start_lock:
        ack = _manual_start_acks.get(reference)
        if ack is None or token is None or not source_ready_to_onboard(row) or ack[0] != token or ack[1] != nonce or ack[2] <= current:
            _manual_start_acks.pop(reference, None)
            return False
        _manual_start_acks.pop(reference, None)
        return True


# Read-timing log (owner decision 2026-10-07): one entry per Salesforce read
# with a label, duration and ok/fail only. The label is the SOQL object name or
# the CLI command words; never query text, values, record ids, or output.
READ_TIMING_LOG: deque[dict[str, object]] = deque(maxlen=200)
_timing_logger = logging.getLogger("surface_dashboard.timing")
_SOQL_OBJECT = re.compile(r"\bFROM\s+([A-Za-z][A-Za-z0-9_]*)", re.IGNORECASE)
_REST_OBJECT = re.compile(r"/sobjects/([A-Za-z][A-Za-z0-9_]*)")
_CLI_WORD = re.compile(r"[a-z][a-z-]{0,23}")


def read_label(args: list[str]) -> str:
    """A short, value-free name for one CLI read (SOQL object, REST object, or command words)."""
    for index, arg in enumerate(args):
        if arg == "--query" and index + 1 < len(args):
            found = _SOQL_OBJECT.search(args[index + 1])
            if found:
                return found.group(1)
    for arg in args:
        found = _REST_OBJECT.search(arg)
        if found:
            return "rest:" + found.group(1)
    words = [arg for arg in args if _CLI_WORD.fullmatch(arg)]
    return " ".join(words[:2]) or "sf"


def record_timing(kind: str, label: str, started: float, ok: bool) -> None:
    millis = round((monotonic() - started) * 1000)
    READ_TIMING_LOG.append({"kind": kind, "label": label, "ms": millis, "ok": ok})
    _timing_logger.info("%s %s %d ms %s", kind, label, millis, "ok" if ok else "fail")


def sf_json(args: list[str], label: str | None = None) -> object:
    started = monotonic()
    ok = False
    try:
        # Decode CLI output as UTF-8 (the --json contract) rather than the
        # locale code page; cp1252 cannot decode UTF-8 continuation bytes and
        # would otherwise leave done.stdout as None and crash the handler.
        done = subprocess.run([salesforce_cli_command(), *args, "--target-org", salesforce_target_org()], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35, check=False)
        if done.returncode or done.stdout is None or len(done.stdout.encode()) > 512 * 1024:
            raise ReadUnavailable()
        result = json.loads(done.stdout)
        ok = True
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ReadUnavailable() from exc
    finally:
        record_timing("sf_read", label or read_label(args), started, ok)


def sf_write_json(args: list[str]) -> object:
    """Run the sole attended write command without retaining CLI output."""
    try:
        # Same UTF-8 decoding as sf_json so non-ASCII CLI output cannot crash
        # the write path or leave done.stdout as None.
        done = subprocess.run([salesforce_cli_command(), *args, "--target-org", salesforce_target_org()], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35, check=False)
        if done.returncode or done.stdout is None or len(done.stdout.encode()) > 512 * 1024:
            raise WriteUnavailable()
        return json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise WriteUnavailable() from exc


def evaluate_co0741_comment_update() -> CommentUpdateEvaluation:
    """Read the exact CO and its one selected DealHub term without retaining raw rows."""
    response = sf_json(["data", "query", "--query", "SELECT Id, Name, Account__c, LastModifiedDate, Onboarding_Comments__c, Onboarding_Stage__c, Onboarding_Approval_Status__c, Onboarding_Product__c, Onboarding_Type__c FROM Customer_Onboarding__c WHERE Name = 'CO-0741' LIMIT 2", "--json"])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or len(records) != 1:
            raise ReadUnavailable()
        co = records[0]
        co_id, account_id, revision = co["Id"], co["Account__c"], co["LastModifiedDate"]
        if (co.get("Name") != "CO-0741" or co.get("Onboarding_Product__c") != CASE4_PRODUCT
                or co.get("Onboarding_Type__c") != CASE4_TYPE or not isinstance(co_id, str)
                or not re.fullmatch(r"[A-Za-z0-9]{15,18}", co_id) or not isinstance(account_id, str)
                or not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id) or not isinstance(revision, str)
                or not revision or not isinstance(co.get("Onboarding_Comments__c"), (str, type(None)))):
            raise ReadUnavailable()
        # This repair workflow intentionally applies only after an operator has
        # cleared the prior comment; it cannot overwrite an existing value.
        if co["Onboarding_Comments__c"] not in (None, ""):
            raise ReadUnavailable()
        if co.get("Onboarding_Stage__c") != "New" or co.get("Onboarding_Approval_Status__c") != "Pending":
            raise ReadUnavailable()
        subscriptions = sf_json(["data", "query", "--query", "SELECT Id, SystemModstamp, DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c FROM DealHub_Subscription__c WHERE DealHub_Account__c = '" + account_id + "' LIMIT 100", "--json"])
        rows = subscriptions["result"]["records"]  # type: ignore[index]
        selected = [row for row in rows if row.get("DealHub_Account__c") == account_id
                    and row.get("Product_Full_Name__c") == "Pentera Surface Go - 500 Subdomains"
                    and isinstance(row.get("DealHub_Status__c"), str)
                    and row["DealHub_Status__c"].casefold() == "active"]
        if subscriptions["status"] != 0 or not isinstance(rows, list) or len(selected) != 1:
            raise ReadUnavailable()
        subscription = selected[0]
        subscription_id, subscription_revision = subscription["Id"], subscription["SystemModstamp"]
        start, end = subscription["DealHub_Subscription_Start_Date__c"], subscription["DealHub_Subscription_End_Date__c"]
        if (not isinstance(subscription_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", subscription_id)
                or not isinstance(subscription_revision, str) or not subscription_revision
                or not isinstance(start, str) or not isinstance(end, str)):
            raise ReadUnavailable()
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if end_date <= start_date:
            raise ReadUnavailable()
        return CommentUpdateEvaluation(co_id, revision, subscription_id, subscription_revision,
                                       f"{start_date.isoformat()} - {end_date.isoformat()}")
    except (KeyError, TypeError, ValueError):
        raise ReadUnavailable() from None


def evaluate_co0745_renewal_comment() -> RenewalCommentEvaluation:
    """Read one renewal CO and one active Surface baseline without writing.

    This is deliberately scoped to CO-0745 during the attended pilot.  It
    cannot infer an existing Production tenant and does not issue a Salesforce
    approval/update acknowledgement.  Any missing, stale, non-Surface,
    non-active, duplicate, or malformed source value fails closed.
    """
    response = sf_json([
        "data", "query", "--query",
        "SELECT Id, Name, Account__c, Account_Name__c, LastModifiedDate, Onboarding_Comments__c, Onboarding_Stage__c, "
        "Onboarding_Approval_Status__c, Onboarding_Product__c, Onboarding_Type__c "
        "FROM Customer_Onboarding__c WHERE Name = 'CO-0745' LIMIT 2",
        "--json",
    ])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or len(records) != 1:
            raise ReadUnavailable()
        co = records[0]
        co_id, account_id, account_name, revision = co["Id"], co["Account__c"], co["Account_Name__c"], co["LastModifiedDate"]
        product, onboarding_type = co.get("Onboarding_Product__c"), co.get("Onboarding_Type__c")
        if (
            co.get("Name") != CO0745_REFERENCE
            or not isinstance(co_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", co_id)
            or not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id)
            or not isinstance(account_name, str) or not " ".join(account_name.split())
            or not isinstance(revision, str) or not revision
            or not isinstance(product, str) or "surface" not in product.casefold()
            or not isinstance(onboarding_type, str) or "renewal" not in onboarding_type.casefold()
            or co.get("Onboarding_Comments__c") not in (None, "")
            or co.get("Onboarding_Stage__c") != "New"
            or co.get("Onboarding_Approval_Status__c") != "Pending"
        ):
            raise ReadUnavailable()
        subscriptions = sf_json([
            "data", "query", "--query",
            "SELECT Id, SystemModstamp, DealHub_Account__c, Product_Full_Name__c, DealHub_Status__c, "
            "DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c "
            "FROM DealHub_Subscription__c WHERE DealHub_Account__c = '" + account_id + "' LIMIT 100",
            "--json",
        ])
        rows = subscriptions["result"]["records"]  # type: ignore[index]
        if subscriptions["status"] != 0 or not isinstance(rows, list):
            raise ReadUnavailable()
        selected = [
            row for row in rows
            if isinstance(row, dict)
            and row.get("DealHub_Account__c") == account_id
            and isinstance(row.get("Product_Full_Name__c"), str)
            and SURFACE_BASELINE_PRODUCT.fullmatch(row["Product_Full_Name__c"])
            and isinstance(row.get("DealHub_Status__c"), str)
            and row["DealHub_Status__c"].casefold() == "active"
        ]
        if len(selected) != 1:
            raise ReadUnavailable()
        subscription = selected[0]
        subscription_id, subscription_revision = subscription["Id"], subscription["SystemModstamp"]
        product_name = subscription["Product_Full_Name__c"]
        start, end = subscription["DealHub_Subscription_Start_Date__c"], subscription["DealHub_Subscription_End_Date__c"]
        if (
            not isinstance(subscription_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", subscription_id)
            or not isinstance(subscription_revision, str) or not subscription_revision
            or not isinstance(product_name, str)
            or not isinstance(start, str) or not isinstance(end, str)
        ):
            raise ReadUnavailable()
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if end_date <= start_date:
            raise ReadUnavailable()
        return RenewalCommentEvaluation(
            CO0745_REFERENCE, co_id, revision, subscription_id, subscription_revision,
            " ".join(account_name.split()) + " - CE Only", product_name,
            f"{start_date.isoformat()} - {end_date.isoformat()}",
        )
    except (KeyError, TypeError, ValueError):
        raise ReadUnavailable() from None


@_display_cached
def evaluate_ce_only_fill_preflight(reference: str) -> CredentialExposureFillPreflight:
    """Validate only CE-only Email Domains for one approved CO (the owner gate).

    The owner decision is deliberately narrow: the only pass/fail validation is
    exactly one valid Email_Domains__c value. Product, subscription, dates,
    country, primary domain, and user data are not validation gates. The
    reference is validated against the CO pattern before it is interpolated,
    so the query is safe.
    """
    if not REFERENCE.fullmatch(reference):
        raise ReadUnavailable()
    response = sf_json([
        "data", "query", "--query",
        "SELECT Id, Name, LastModifiedDate, Email_Domains__c, Onboarding_Product__c, Onboarding_Type__c "
        "FROM Customer_Onboarding__c WHERE Name = '" + reference + "' LIMIT 2",
        "--json",
    ])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or len(records) != 1:
            raise ReadUnavailable()
        co = records[0]
        revision = co["LastModifiedDate"]
        if (co.get("Name") != reference or not isinstance(revision, str) or not revision):
            raise ReadUnavailable()
        blockers: list[str] = []
        if (co.get("Onboarding_Product__c") != CE_ROUTE_PRODUCT
                or co.get("Onboarding_Type__c") != CE_ROUTE_TYPE):
            blockers.append("not_a_new_credential_exposure_onboarding")
        raw_email_domains = co.get("Email_Domains__c")
        email_domains = [item.removeprefix("@") for item in re.split(r"[,;\s]+", raw_email_domains.strip()) if item] if isinstance(raw_email_domains, str) else []
        domain = email_domains[0].casefold() if len(email_domains) == 1 else ""
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", domain):
            blockers.append("exactly_one_email_domain_required")
        return CredentialExposureFillPreflight(reference, revision, len(email_domains),
                                               tuple(sorted(set(blockers))))
    except (KeyError, TypeError, ValueError):
        raise ReadUnavailable() from None


def issue_comment_update_ack(evaluation: CommentUpdateEvaluation, *, now: float | None = None) -> str:
    """Issue one short-lived confirmation bound to both Salesforce revisions."""
    nonce = token_urlsafe(24)
    with _comment_update_lock:
        _comment_update_acks["CO-0741"] = (evaluation.binding, nonce,
                                              (monotonic() if now is None else now) + COMMENT_UPDATE_ACK_TTL_SECONDS)
    return nonce


def consume_comment_update_ack(evaluation: CommentUpdateEvaluation, nonce: str, *, now: float | None = None) -> bool:
    with _comment_update_lock:
        ack = _comment_update_acks.get("CO-0741")
        _comment_update_acks.pop("CO-0741", None)
    return bool(ack and isinstance(nonce, str) and ack[0] == evaluation.binding and ack[1] == nonce and ack[2] > (monotonic() if now is None else now))


def update_co0741_comment_after_confirmation(evaluation: CommentUpdateEvaluation, nonce: str) -> bool:
    """Update the sole authorized field after one fresh validation, then read back."""
    if not consume_comment_update_ack(evaluation, nonce):
        return False
    update = sf_write_json(["data", "update", "record", "--sobject", "Customer_Onboarding__c", "--record-id", evaluation.co_id,
                            "--values", "Onboarding_Comments__c='" + evaluation.proposed_comment + "' Onboarding_Stage__c='Request Approved' Onboarding_Approval_Status__c=Approved", "--json"])
    try:
        if update["status"] != 0:  # type: ignore[index]
            return False
        readback = sf_json(["data", "query", "--query", "SELECT Name, Onboarding_Comments__c, Onboarding_Stage__c, Onboarding_Approval_Status__c FROM Customer_Onboarding__c WHERE Id = '" + evaluation.co_id + "' LIMIT 2", "--json"])
        rows = readback["result"]["records"]  # type: ignore[index]
        return bool(readback["status"] == 0 and isinstance(rows, list) and len(rows) == 1
                    and rows[0].get("Name") == "CO-0741"
                    and rows[0].get("Onboarding_Comments__c") == evaluation.proposed_comment
                    and rows[0].get("Onboarding_Stage__c") == "Request Approved"
                    and rows[0].get("Onboarding_Approval_Status__c") == "Approved")
    except (KeyError, TypeError):
        return False


def open_onboardings_view_id() -> str:
    """Resolve and retain only the static Salesforce list-view identifier.

    This avoids one Salesforce CLI process on every refresh. It never caches a
    Customer Onboarding row, subscription value, or browser/session material.
    """
    global _open_onboardings_view_id
    with _view_id_lock:
        if _open_onboardings_view_id is not None:
            return _open_onboardings_view_id
        view = sf_json(["data", "query", "--query", VIEW_QUERY, "--json"])
        try:
            records = view["result"]["records"]  # type: ignore[index]
            view_id = records[0]["Id"]
            if (view["status"] != 0 or not isinstance(records, list) or len(records) != 1
                    or not isinstance(view_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", view_id)):
                raise ReadUnavailable()
            _open_onboardings_view_id = view_id
            return view_id
        except (KeyError, TypeError, IndexError):
            raise ReadUnavailable() from None


def display_date(value: str | None) -> str:
    if not value:
        return "Date unavailable"
    if value == "Not verified":
        return value
    try:
        parsed = date.fromisoformat(value[:10])
        return f"{parsed:%b} {parsed.day}, {parsed.year}"
    except ValueError:
        pass
    try:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S GMT %Y")
        return f"{parsed:%b} {parsed.day}, {parsed.year}"
    except ValueError:
        pass
    return "Date unavailable"


def subscription_summaries(references: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """Read comment dates transiently and return only the safe date summary."""
    if not references or any(not REFERENCE.fullmatch(reference) for reference in references):
        raise ReadUnavailable()
    quoted = ", ".join(f"'{reference}'" for reference in references)
    response = sf_json(["data", "query", "--query", "SELECT Name, Onboarding_Comments__c FROM Customer_Onboarding__c WHERE Name IN (" + quoted + ")", "--json"])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or len(records) != len(references):
            raise ReadUnavailable()
        summaries: dict[str, dict[str, str]] = {}
        for record in records:
            reference = record["Name"]
            comment = record["Onboarding_Comments__c"]
            if (not isinstance(reference, str) or reference not in references or reference in summaries
                    or (comment is not None and not isinstance(comment, str))):
                raise ReadUnavailable()
            parsed = extract_dealhub_dates(comment)
            if parsed["ready_for_cse_review"]:
                summaries[reference] = {
                    "Subscription_Start": str(parsed["dealhub_start_date"]),
                    "Subscription_End": str(parsed["dealhub_end_date"]),
                }
            else:
                summaries[reference] = {"Subscription_Start": "Not verified", "Subscription_End": "Not verified"}
        if set(summaries) != set(references):
            raise ReadUnavailable()
        return summaries
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


def queue_rows() -> list[dict[str, str | None]]:
    """Open-queue rows plus the local readback state (read fresh on every call)."""
    rows = queue_source_rows()
    readbacks = attended_leonardo_readbacks()
    scans = attended_scan_statuses()
    for row in rows:
        readback = readbacks.get(row["Name"] or "")
        if readback is not None:
            row["Local_Leonardo_State"] = readback["leonardo_state"]
        scan = scans.get(row["Name"] or "")
        if scan is not None:
            row["Local_Scan_State"] = str(scan["state"])
    validations = attended_validations()
    mirrors = dev_mirror_ids()
    for row in rows:
        result = validations.get(row["Name"] or "")
        readback = readbacks.get(row["Name"] or "")
        if readback is not None and readback["surface_account_id"] in mirrors:
            continue  # a DEV mirror is not drift-validated; an old result is not shown
        if result is not None:
            row["Local_Validation_Drift"] = str(sum(1 for c in result["checks"] if c["status"] == "drift"))  # type: ignore[union-attr]
    return rows


@_display_cached
def queue_source_rows() -> list[dict[str, str | None]]:
    if vm_mode():
        return snapshot_queue_rows()
    try:
        view_id = open_onboardings_view_id()
        result = sf_json(["api", "request", "rest", f"/services/data/v67.0/sobjects/Customer_Onboarding__c/listviews/{view_id}/results", "--method", "GET"])
        rows: list[dict[str, str | None]] = []
        for record in result["records"]:  # type: ignore[index]
            values = {column["fieldNameOrPath"]: column["value"] for column in record["columns"]}
            if not set(QUEUE_FIELDS) <= set(values) or not isinstance(values["Name"], str) or not REFERENCE.fullmatch(values["Name"]): raise ReadUnavailable()
            rows.append({field: value if isinstance(value, str) else None for field, value in values.items() if field in QUEUE_FIELDS})
        return rows
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


HISTORY_MONTHS = 13  # 12 complete months + the current month to date
HISTORY_CACHE_SECONDS = 10 * 60
HISTORY_FAILURE_CACHE_SECONDS = 60
HISTORY_DURATION_LIMIT = 2000
# Fixed series order (color follows the product, never its rank).
HISTORY_PRODUCTS = ("Credential Exposure", "Surface & Credential Exposure", "Surface")
HISTORY_OTHER = "Other"
# Fixed read-only queries: aggregate counts, plus creation/completion
# timestamps only for the median (no names, domains, or other fields are
# selected; record ids in the CLI response are discarded). Month buckets use
# the CLI user's timezone. LAST_N_MONTHS:12 + THIS_MONTH = the 13 months
# ending with the current (partial) month.
COMPLETED_HISTORY_QUERY = (
    "SELECT CALENDAR_YEAR(convertTimezone(Completed_Time_Stamp__c)) y, "
    "CALENDAR_MONTH(convertTimezone(Completed_Time_Stamp__c)) m, Onboarding_Product__c p, COUNT(Id) n "
    "FROM Customer_Onboarding__c WHERE Onboarding_Stage__c = 'Onboarding Completed' "
    "AND (Completed_Time_Stamp__c = LAST_N_MONTHS:12 OR Completed_Time_Stamp__c = THIS_MONTH) "
    "GROUP BY CALENDAR_YEAR(convertTimezone(Completed_Time_Stamp__c)), "
    "CALENDAR_MONTH(convertTimezone(Completed_Time_Stamp__c)), Onboarding_Product__c"
)
CREATED_HISTORY_QUERY = (
    "SELECT CALENDAR_YEAR(convertTimezone(CreatedDate)) y, CALENDAR_MONTH(convertTimezone(CreatedDate)) m, "
    "COUNT(Id) n FROM Customer_Onboarding__c "
    "WHERE (CreatedDate = LAST_N_MONTHS:12 OR CreatedDate = THIS_MONTH) "
    "GROUP BY CALENDAR_YEAR(convertTimezone(CreatedDate)), CALENDAR_MONTH(convertTimezone(CreatedDate))"
)
COMPLETION_DURATIONS_QUERY = (
    "SELECT CreatedDate, Completed_Time_Stamp__c FROM Customer_Onboarding__c "
    "WHERE Onboarding_Stage__c = 'Onboarding Completed' AND Completed_Time_Stamp__c = LAST_N_DAYS:90 "
    f"LIMIT {HISTORY_DURATION_LIMIT}"
)
REJECTED_TOTAL_QUERY = (
    "SELECT COUNT(Id) n FROM Customer_Onboarding__c WHERE Onboarding_Approval_Status__c = 'Rejected'"
)
_history_lock = Lock()
# (monotonic time, history or None for a cached failure)
_history_cache: tuple[float, "ClosedHistory | None"] | None = None


@dataclass(frozen=True, slots=True)
class HistoryMonth:
    """Completed onboardings in one calendar month, by product, plus new COs."""

    year: int
    month: int
    completed: tuple[int, ...]  # aligned with ClosedHistory.series
    created: int

    @property
    def completed_total(self) -> int:
        return sum(self.completed)

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%b %Y")


@dataclass(frozen=True, slots=True)
class ClosedKpis:
    """Headline numbers for the history card (derived values only)."""

    completed_30d: int
    completed_prev_30d: int
    median_days_90d: float | None  # None when there is no sample or the read was truncated
    median_sample: int
    rejected_total: int


@dataclass(frozen=True, slots=True)
class ClosedHistory:
    """Monthly closed-queue history (aggregate counts only; never persisted)."""

    series: tuple[str, ...]
    months: tuple[HistoryMonth, ...]  # oldest -> newest; the last is the current month
    as_of: date
    kpis: ClosedKpis | None = None
    read_at: str = ""  # local HH:MM of the Salesforce read (the value is cached)

    @property
    def completed_total(self) -> int:
        return sum(month.completed_total for month in self.months)


def _salesforce_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReadUnavailable()
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        raise ReadUnavailable() from None


def build_closed_kpis(duration_records: list[object], rejected_records: list[object], now: datetime) -> ClosedKpis:
    """Completed in the last 30 days vs the 30 before, median days (created ->
    completed) over the last 90 days, and the all-time rejected count.

    Only the two timestamps of each record are used; nothing is retained.
    A read that hit the row limit reports no median rather than a biased one.
    """
    from statistics import median

    completed_30d = completed_prev_30d = 0
    durations: list[float] = []
    for record in duration_records:
        if not isinstance(record, dict):
            raise ReadUnavailable()
        created = _salesforce_datetime(record.get("CreatedDate"))
        completed = _salesforce_datetime(record.get("Completed_Time_Stamp__c"))
        age_days = (now - completed).total_seconds() / 86400
        if age_days < 30:
            completed_30d += 1
        elif age_days < 60:
            completed_prev_30d += 1
        duration = (completed - created).total_seconds() / 86400
        if duration >= 0:
            durations.append(duration)
    truncated = len(duration_records) >= HISTORY_DURATION_LIMIT
    median_days = None if truncated or not durations else float(median(durations))
    if len(rejected_records) != 1:
        raise ReadUnavailable()
    return ClosedKpis(completed_30d, completed_prev_30d, median_days, len(durations),
                      _aggregate_count(rejected_records[0], "n"))


def history_month_keys(today: date, count: int = HISTORY_MONTHS) -> list[tuple[int, int]]:
    """The ``count`` calendar months ending with ``today``'s month, oldest first."""
    keys: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(count):
        keys.append((year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return list(reversed(keys))


def _aggregate_count(record: object, key: str) -> int:
    value = record.get(key) if isinstance(record, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or int(value) != value:
        raise ReadUnavailable()
    return int(value)


def build_closed_history(completed_records: list[object], created_records: list[object], today: date) -> ClosedHistory:
    """Fold the two aggregate result sets into 12 zero-filled months.

    Products outside HISTORY_PRODUCTS (or blank) are folded into "Other",
    which is only added as a series when it has any count. Rows outside the
    window are ignored; malformed rows fail closed (ReadUnavailable).
    """
    keys = history_month_keys(today)
    index = {key: position for position, key in enumerate(keys)}
    completed = [[0] * (len(HISTORY_PRODUCTS) + 1) for _ in keys]
    created = [0] * len(keys)
    for record in completed_records:
        key = (_aggregate_count(record, "y"), _aggregate_count(record, "m"))
        count = _aggregate_count(record, "n")
        if key not in index:
            continue
        product = record.get("p") if isinstance(record, dict) else None
        slot = HISTORY_PRODUCTS.index(product) if product in HISTORY_PRODUCTS else len(HISTORY_PRODUCTS)
        completed[index[key]][slot] += count
    for record in created_records:
        key = (_aggregate_count(record, "y"), _aggregate_count(record, "m"))
        if key in index:
            created[index[key]] += _aggregate_count(record, "n")
    has_other = any(row[-1] for row in completed)
    series = HISTORY_PRODUCTS + ((HISTORY_OTHER,) if has_other else ())
    months = tuple(
        HistoryMonth(year, month, tuple(row if has_other else row[:-1]), created[position])
        for position, ((year, month), row) in enumerate(zip(keys, completed))
    )
    return ClosedHistory(series, months, today)


def _aggregate_records(query: str) -> list[object]:
    response = sf_json(["data", "query", "--query", query, "--json"])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list):  # type: ignore[index]
            raise ReadUnavailable()
        return records
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


def closed_history(*, now: float | None = None, today: date | None = None,
                   wall_clock: datetime | None = None) -> ClosedHistory:
    """Return the closed-queue history with its headline numbers.

    Cached in memory only (never on disk or in logs): a result for 10
    minutes, a failure for 60 seconds so a broken read cannot slow every page
    load. The cache holds only the frozen derived counts. Raises
    ReadUnavailable when Salesforce cannot be read; the queue page then shows
    the history card as unavailable and the queue itself is unaffected.
    """
    global _history_cache
    clock = monotonic() if now is None else now
    with _history_lock:
        if _history_cache is not None:
            cached_at, cached = _history_cache
            ttl = HISTORY_CACHE_SECONDS if cached is not None else HISTORY_FAILURE_CACHE_SECONDS
            if clock - cached_at < ttl:
                if cached is None:
                    raise ReadUnavailable()
                return cached
        read_clock = wall_clock or datetime.now().astimezone()
        try:
            history = build_closed_history(_aggregate_records(COMPLETED_HISTORY_QUERY),
                                           _aggregate_records(CREATED_HISTORY_QUERY), today or date.today())
            kpis = build_closed_kpis(_aggregate_records(COMPLETION_DURATIONS_QUERY),
                                     _aggregate_records(REJECTED_TOTAL_QUERY), read_clock)
        except ReadUnavailable:
            _history_cache = (clock, None)
            raise
        history = ClosedHistory(history.series, history.months, history.as_of, kpis, read_clock.strftime("%H:%M"))
        _history_cache = (clock, history)
        return history


def cached_closed_history(*, now: float | None = None) -> ClosedHistory | None:
    """Return the fresh cached history without reading Salesforce (None when cold).

    Filtered queue views use this for the history tile so they never pay for
    a history read; the unfiltered view and the History tab warm the cache.
    """
    clock = monotonic() if now is None else now
    with _history_lock:
        if _history_cache is None:
            return None
        cached_at, cached = _history_cache
        if cached is None or clock - cached_at >= HISTORY_CACHE_SECONDS:
            return None
        return cached


@_display_cached
def detail_row(reference: str) -> dict[str, str | None]:
    if not REFERENCE.fullmatch(reference): raise ReadUnavailable()
    if vm_mode():
        return snapshot_detail_row(reference)
    query = "SELECT " + ", ".join(DETAIL_FIELDS) + f" FROM Customer_Onboarding__c WHERE Name = '{reference}' LIMIT 2"
    response = sf_json(["data", "query", "--query", query, "--json"])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list) or len(records) != 1: raise ReadUnavailable()
        record = records[0]
        if record.get("Name") != reference or not set(DETAIL_FIELDS) <= set(record): raise ReadUnavailable()
        return {field: record[field] if isinstance(record[field], str) else None for field in DETAIL_FIELDS}
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


DEALHUB_FIELDS = ("Product_Full_Name__c", "DealHub_Status__c", "DealHub_Subscription_Start_Date__c", "DealHub_Subscription_End_Date__c")


def _dealhub_records(query_tail: str, label: str) -> list[dict[str, object]]:
    response = sf_json(["data", "query", "--query", "SELECT " + ", ".join(DEALHUB_FIELDS)
                        + " FROM DealHub_Subscription__c WHERE " + query_tail + " LIMIT 100", "--json"], label)
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list):  # type: ignore[index]
            raise ReadUnavailable()
        if not all(isinstance(record, dict) for record in records):
            raise ReadUnavailable()
        return [{key: record.get(key) for key in DEALHUB_FIELDS} for record in records]
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


@_display_cached
def dealhub_rows_for_co(reference: str) -> list[dict[str, object]]:
    """DealHub rows for the account of one CO, in ONE query (semi-join on the CO name).

    The CO page needs these rows for both the commercial check and the renewal
    plan; the semi-join lets the read start together with the CO row read
    instead of waiting for its Account__c. Display only, never stored.
    """
    if not REFERENCE.fullmatch(reference):
        raise ReadUnavailable()
    return _dealhub_records("DealHub_Account__c IN (SELECT Account__c FROM Customer_Onboarding__c WHERE Name = '"
                            + reference + "')", "DealHub_Subscription__c")


@_display_cached
def _renewal_rows_by_account(account_id: str) -> list[dict[str, object]]:
    if not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
        raise ReadUnavailable()
    return _dealhub_records("DealHub_Account__c = '" + account_id + "'", "DealHub_Subscription__c")


def renewal_subscription_rows(account_id: str, reference: str | None = None) -> list[dict[str, object]]:
    """DealHub rows for one account. With the CO ``reference`` this is the page's single shared read.

    The account is still validated first, so a CO without a valid Account__c
    shows the same "could not be read" outcome as before.
    """
    if not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
        raise ReadUnavailable()
    if reference is not None:
        return dealhub_rows_for_co(reference)
    return _renewal_rows_by_account(account_id)


RENEWAL_BLOCKER_TEXT = {
    "no_new_surface_term": "No new-model Surface term (Go/Prime) is in DealHub yet; only legacy or no Surface rows.",
    "no_core_plus_term": "No Core Plus term is in DealHub for the Credential Exposure renewal.",
    "surface_term_ambiguous": "Several different Surface baselines start on the same day.",
    "core_plus_term_ambiguous": "Several Core Plus rows with different end dates start on the same day.",
    "terms_differ": "The Surface and Core Plus terms end on different dates (rule 1a: one tenant, one licence term).",
}


def _renewal_plan_data(row: dict[str, str | None], today: date | None = None) -> tuple[bool, dict[str, Any] | None]:
    """(subscriptions_readable, plan) for a renewal CO; the plan is None when none can be built."""
    if renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")) is None:
        return True, None
    try:
        rows = renewal_subscription_rows(row.get("Account__c") or "", row.get("Name"))
    except ReadUnavailable:
        return False, None
    return True, build_renewal_plan(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"), rows, today)


def _renewal_plan_section(row: dict[str, str | None], today: date | None = None,
                          data: tuple[bool, dict[str, Any] | None] | None = None) -> str:
    """Read-only renewal plan (Cases 4-6 and single-product renewals): a manual checklist, no action.

    ``data`` is the page's already computed ``_renewal_plan_data`` (so the page reads DealHub once).
    """
    case = renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    if case is None:
        return ""
    head = ("<section class='card' aria-labelledby='renewal-plan-title'><div class='card-head'>"
            "<h2 id='renewal-plan-title' class='pill'>Renewal plan · " + escape(case[1]) + "</h2>"
            "<span class='chip chip-neutral'>Plan</span></div>")
    readable, plan = data if data is not None else _renewal_plan_data(row, today)
    if not readable:
        return head + "<p class='note'>The DealHub subscriptions could not be read. No plan is shown.</p></section>"
    if plan is None:
        return ""
    has_id = bool((row.get("Surface_Account_ID__c") or "").strip() or (row.get("Account_UUID__c") or "").strip())
    facts = [("Existing tenant", "Salesforce holds an ID (production tenant) — open it by that ID" if has_id
              else "No ID in Salesforce — find the tenant by name and primary domain")]
    surface, ce = plan["surface_term"], plan["ce_term"]
    if surface:
        addon = (" + " + str(surface["addon_subdomains"]) + " add-on" if surface["addon_subdomains"] else "")
        facts += [("New Surface term", f"{surface['products'][0]} · {surface['status']} · {surface['start']} → {surface['end']}"),
                  ("Tier / interval", f"{str(surface['tier']).title()} · {surface['scanning_interval']}"),
                  ("Subdomains / domains / assets",
                   f"{surface['subdomains']} ({surface['baseline_subdomains']} baseline{addon}) / {surface['domains']} / {surface['assets']}"),
                  ("Surface settings", ("Case 5 applies and verifies the create-time Surface profile; Number of domains = subdomains: "
                                        if case[0] == CASE5_ENGINE else "Revalidate the Surface profile: ")
                   + "90 h, Recon / brute force / Nuclei ON, discovery / dorking / AI / static IP / auth testing / multi-stack OFF; Notifications / Multiple users / API ON")]
    if ce:
        extra = f" + {ce['ce_domains_addon']} from add-on rows (Q7)" if ce["ce_domains_addon"] else ""
        facts += [("New Core Plus term", f"{ce['products'][0]} · {ce['status']} · {ce['start']} → {ce['end']}"),
                  ("Leaked Credentials", f"ON · {ce['leaked_credentials_interval']} · {ce['ce_domains_included']} CE email domain{extra}")]
    term = surface or ce
    if term:
        when = "now" if term["applicable_now"] else "from " + term["apply_from"]
        facts += [("Apply", when + " (up to 14 days before the new term starts) — " + RENEWAL_APPLY_WINDOW_NOTE),
                  ("Expiration (Q1)", f"DealHub term end exactly: {term['end']}"),
                  ("Start date (Q2)", "never changed")]
    if "terms_agree" in plan:
        agree = plan["terms_agree"]
        facts.append(("Term check", "✓ Surface and Core Plus agree" if agree["term_end"]
                      else "✗ the terms differ — manual review"))
    if plan["legacy_ignored"]:
        facts.append(("Legacy rows ignored (Q11)", str(len(plan["legacy_ignored"])) + " older-model product row(s)"))
    blockers = "".join("<p class='note' style='color:var(--bad)'><b>" + escape(RENEWAL_BLOCKER_TEXT.get(code, code))
                       + "</b> <code>" + escape(code) + "</code></p>" for code in plan["blockers"])
    return (head + blockers + "<dl>" + "".join("<dt>" + escape(k) + "</dt><dd>" + escape(v) + "</dd>" for k, v in facts)
            + "</dl><p class='login-safety'>Read-only plan built from Salesforce and DealHub; showing it opens and saves "
            "nothing. Rules: Q1 expiration = DealHub term end; Q2 start never changes; Q3 Approved = human-validated, and added "
            "domains must pass the production duplicate gate; Q10 routing by product + type (Case 6 = Surface + CE renewal). "
            "A renewal is applied only by Start renewal in What to do now (Leonardo Development; Dev mirror first).</p></section>")


# Owner 2026-10-07: after a verified renewal (Cases 4-6) the operator checks the Operator Account by hand; nothing is automatic.
RENEWAL_OPERATOR_CHECK_TEXT = "Check the Operator Account (did the customer's TA change?)"
CASE5_ENGINE = "case_5_renew_ce_new_surface"


def _renewal_operator_check() -> str:
    return "<ul class='todo'>" + _step(escape(RENEWAL_OPERATOR_CHECK_TEXT), False) + "</ul>"


RENEWAL_OUTCOME_DATES = ("old_expiration", "new_expiration", "expiration_kept_current", "expiration_kept_target", "apply_from")
RENEWAL_APPLY_WINDOW_NOTE = "enforced in production; the Dev mirror may be renewed early"

RENEWAL_RESULT_TEXT = {
    "renewal_domains_mismatch_manual_review":
        "Salesforce domains differ from the tenant's domains — manual review; nothing was saved",
    "renewal_not_yet_applicable":
        "Production renewals are applied only from 14 days before the new term starts; nothing was saved",
}


def renewal_apply_text(term: Any) -> str:
    """"Apply from <date> — enforced in production; the Dev mirror may be renewed early" (owner 2026-10-07)."""
    if not term:
        return "\u2014"
    return ("now" if term["applicable_now"] else "Apply from " + str(term["apply_from"])) + " \u2014 " + RENEWAL_APPLY_WINDOW_NOTE


def renewal_expiry_text(old: str, end: str) -> str:
    """Plan text for the expiry: "Expiry kept (...)" when the tenant is already at/after the DealHub term end (owner 2026-10-07)."""
    try:
        if date.fromisoformat(old) >= date.fromisoformat(end):
            return "Expiry kept (already " + old + ", DealHub term end " + end + ")"
    except ValueError:
        pass
    return old + " \u2192 " + end + " (DealHub term end)"


def attended_renewal_outcomes() -> dict[str, dict[str, object]]:
    """Load the per-CO latest --renew outcome; absence means none, an unreadable file counts as none (fails closed)."""
    try:
        raw = json.loads(RENEWAL_OUTCOMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    outcomes: dict[str, dict[str, object]] = {}
    for reference, value in raw.items():
        try:
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference) or not isinstance(value, dict):
                continue
            result, label = value.get("result"), value.get("leonardo_write")
            if (not isinstance(result, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", result)
                    or value.get("mode") not in RENEWAL_OUTCOME_MODES
                    or label not in ("verified", "attempted_unverified", "not_performed")):
                continue
            outcomes[reference] = {"result": result, "mode": value["mode"], "leonardo_write": label,
                                   "observed_at": datetime.fromisoformat(value["observed_at"]),
                                   "changes": value["changes"] if type(value.get("changes")) is int else None,
                                   "added_domains": value["added_domains"] if type(value.get("added_domains")) is int else None,
                                   "tenant_id": value["tenant_id"] if isinstance(value.get("tenant_id"), str)
                                   and re.fullmatch(r"[A-Za-z0-9]{16,64}", value["tenant_id"]) else None,
                                   **{key: date.fromisoformat(value[key]).isoformat() if isinstance(value.get(key), str) else None
                                      for key in RENEWAL_OUTCOME_DATES}}
        except (KeyError, TypeError, ValueError):
            continue
    return outcomes


def _renewal_outcome_section(reference: str) -> str:
    """Latest renewal dry run / apply for this CO (local record written by the runner; Salesforce is never changed)."""
    outcome = attended_renewal_outcomes().get(reference)
    if outcome is None:
        return ""
    verified = outcome["leonardo_write"] == "verified"
    reason = RENEWAL_RESULT_TEXT.get(str(outcome["result"]))
    facts = [("Result", "<code>" + escape(str(outcome["result"])) + "</code>")]
    if reason:
        facts.append(("Reason", escape(reason)))
    facts += [("Mode", escape(str(outcome["mode"]).replace("_", " "))),
              ("Leonardo write", escape(str(outcome["leonardo_write"]).replace("_", " "))),
              ("Observed", escape(outcome["observed_at"].strftime("%Y-%m-%d %H:%M")))]  # type: ignore[union-attr]
    if outcome.get("expiration_kept_current") and outcome.get("expiration_kept_target"):
        facts.append(("Expiration", escape("Expiry kept (already " + str(outcome["expiration_kept_current"])
                                           + ", DealHub term end " + str(outcome["expiration_kept_target"]) + ")")))
    elif outcome["new_expiration"]:
        facts.append(("Expiration", escape(str(outcome["old_expiration"] or "—")) + " → " + escape(str(outcome["new_expiration"]))))
    if outcome.get("apply_from") and str(outcome["result"]) == "renewal_not_yet_applicable":
        facts.append(("Apply from", escape(str(outcome["apply_from"]))))
    if outcome["changes"] is not None:
        facts.append(("Planned changes", str(outcome["changes"]) + (
            " · " + str(outcome["added_domains"]) + " added domain(s)" if outcome["added_domains"] else "")))
    return ("<section class='card' aria-labelledby='renewal-outcome-title'><div class='card-head'>"
            "<h2 id='renewal-outcome-title' class='pill'>Latest renewal run</h2><span class='chip "
            + ("chip-ok" if verified else "chip-neutral") + "'>" + ("Verified" if verified else "Not applied") + "</span></div><dl>"
            + "".join("<dt>" + k + "</dt><dd>" + v + "</dd>" for k, v in facts)
            + "</dl><p class='login-safety'>Local record of the last renewal dry run or apply (Leonardo Development); "
            "Salesforce is not changed.</p></section>")


def surface_commercial_readiness(row: dict[str, str | None]) -> dict[str, object] | None:
    """Read one CO's related subscriptions only when its detail page is opened.

    This keeps the queue landing page fast while giving an operator a fresh,
    product-level decision on the selected new Surface CO.  The result has no
    write capability and is kept only for rendering the current response.
    """
    if row.get("Onboarding_Approval_Status__c") not in {"Pending", "Approved"}:
        return None
    if row.get("Onboarding_Product__c") != "Surface & Credential Exposure":
        return None
    if vm_mode():
        return None  # DealHub rows are not part of the published snapshot (docs/41); no live read on the VM
    account_id = row.get("Account__c")
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
        return {"commercial_ready": False, "manual_review_required": True, "reason": "account_missing"}
    reference = row.get("Name")
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
        raise ReadUnavailable()
    try:
        subscriptions: list[dict[str, object]] = []
        for record in dealhub_rows_for_co(reference):
            if not isinstance(record, dict):
                raise ReadUnavailable()
            subscriptions.append({
                "product_full_name": record.get("Product_Full_Name__c"),
                "status": record.get("DealHub_Status__c"),
                "start_date": record.get("DealHub_Subscription_Start_Date__c"),
                "end_date": record.get("DealHub_Subscription_End_Date__c"),
            })
        return evaluate_new_surface_source(
            onboarding_comments=row.get("Onboarding_Comments__c"),
            main_domain=row.get("Main_Domain__c"),
            subscriptions=subscriptions,
        )
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


def post_form(handler: BaseHTTPRequestHandler) -> dict[str, list[str]] | None:
    """Parse one small URL-encoded form, otherwise fail closed."""
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length < 1 or length > 4096:
            return None
        parsed = parse_qs(handler.rfile.read(length).decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        return parsed
    except (UnicodeDecodeError, ValueError):
        return None


def exact_form_value(form: dict[str, list[str]] | None, name: str) -> str | None:
    values = form.get(name) if form is not None else None
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
        return None
    return values[0]


HISTORY_SERIES_CLASSES = {HISTORY_PRODUCTS[0]: "s1", HISTORY_PRODUCTS[1]: "s2", HISTORY_PRODUCTS[2]: "s3", HISTORY_OTHER: "so"}
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Chart geometry (viewBox 880x200 includes the x-axis band): 13 bands x 64px
# from x=40; plot top y=16, height 150 -> baseline y=166; bars 24px wide with a
# 4px rounded data-end and a 2px surface gap between stacked segments.
_QH_W, _QH_H, _QH_X0, _QH_BAND, _QH_Y0, _QH_PH, _QH_BAR, _QH_R, _QH_GAP = 880, 200, 40, 64, 16, 150, 24, 4, 2


def nice_ticks(peak: int) -> tuple[int, ...]:
    """0-based integer axis ticks: at most 5 intervals, clean 1/2/2.5/5 x 10^k steps."""
    step = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 25000)
                if peak <= 5 * s)
    return tuple(range(0, max(step, -(-peak // step) * step) + 1, step))


def _svg_number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _rounded_top(x: float, y: float, width: float, height: float) -> str:
    """Column path with a 4px rounded data-end and a square baseline end."""
    r = min(_QH_R, height, width / 2)
    n = _svg_number
    return (f"M{n(x)},{n(y + height)}V{n(y + r)}A{n(r)},{n(r)} 0 0 1 {n(x + r)},{n(y)}"
            f"H{n(x + width - r)}A{n(r)},{n(r)} 0 0 1 {n(x + width)},{n(y + r)}V{n(y + height)}Z")


def _history_month_name(month: HistoryMonth, partial_through: date | None) -> str:
    name = _MONTH_ABBR[month.month - 1] + f" {month.year}"
    if partial_through is not None:
        name += f" · to {partial_through.day} {_MONTH_ABBR[partial_through.month - 1]}"
    return name


def render_closed_history(history: ClosedHistory, read_at: str) -> str:
    """Render the closed-onboardings chart: stat strip, legend, SVG, CSS-only
    hover overlay, table view, and footnote. Pure function of the history.

    Completed onboardings are stacked by product in fixed color order; new
    COs created per month are a 2px tick on the same count axis (one axis,
    never dual). The current month is marked "to date" and never labelled.
    Tooltips enhance only: every value is also in the table view.
    """
    months = history.months
    series = history.series
    classes = [HISTORY_SERIES_CLASSES[name] for name in series]
    kpis = history.kpis
    stats = ""
    if kpis is not None:
        delta = kpis.completed_30d - kpis.completed_prev_30d
        delta_text = ("no change" if delta == 0 else f"{delta:+d}") + " vs previous 30 days"
        median = ("unavailable" if kpis.median_days_90d is None
                  else f"{kpis.median_days_90d:.1f} days" if kpis.median_days_90d < 10
                  else f"{kpis.median_days_90d:.0f} days")
        stats = ("<div class='qh-stats'>"
                 f"<div class='qh-stat'><span>Completed · last 30 days</span><b>{kpis.completed_30d:,}</b>"
                 f"<small>{escape(delta_text)}</small></div>"
                 f"<div class='qh-stat'><span>Median time to complete · last 90 days</span><b>{escape(median)}</b>"
                 f"<small>{kpis.median_sample:,} completed · created → completed</small></div></div>")
    legend = "".join(f"<li><i class='qh-sw {css}'></i>{escape(name)}</li>" for name, css in zip(series, classes))
    legend += "<li><i class='qh-lk'></i>New COs created</li>"
    if not any(month.completed_total or month.created for month in months):
        return (f"<figure class='qh'>{stats}<p class='empty'>No onboardings were completed or created in the last "
                f"{len(months)} months.</p></figure>")
    ticks = nice_ticks(max(max(month.completed_total, month.created) for month in months))
    top = ticks[-1]
    n = _svg_number

    def sy(value: float) -> float:
        return _QH_Y0 + _QH_PH - value * _QH_PH / top

    count = len(months)
    right = _QH_X0 + count * _QH_BAND
    svg: list[str] = []
    partial_index = count - 1  # the current month (to date)
    svg.append(f"<rect class='pb' x='{_QH_X0 + partial_index * _QH_BAND + 2}' y='{_QH_Y0 - 6}' "
               f"width='{_QH_BAND - 4}' height='{_QH_PH + 6}' rx='6'/>")
    for tick in ticks:
        if tick:
            svg.append(f"<line class='grid' x1='{_QH_X0}' x2='{right}' y1='{n(sy(tick))}' y2='{n(sy(tick))}' "
                       "shape-rendering='crispEdges'/>")
        svg.append(f"<text class='yt' x='{_QH_X0 - 8}' y='{n(sy(tick) + 4)}'>{tick:,}</text>")
    hits: list[str] = []
    ticks_y: list[float] = []
    for index, month in enumerate(months):
        x = _QH_X0 + index * _QH_BAND + (_QH_BAND - _QH_BAR) / 2
        cx = _QH_X0 + index * _QH_BAND + _QH_BAND / 2
        values = list(zip(month.completed, classes, series))
        stack = [(value, css) for value, css, _name in values if value > 0]
        cumulative = 0
        for position, (value, css) in enumerate(stack):
            base_y, top_y = sy(cumulative), sy(cumulative + value)
            cumulative += value
            if position == len(stack) - 1:  # the topmost segment carries the rounded data-end
                height = max(1.0, base_y - top_y)
                svg.append(f"<path class='{css}' d='{_rounded_top(x, base_y - height, _QH_BAR, height)}'/>")
            else:  # 2px surface gap carved from the top of each lower segment
                height = max(1.0, base_y - top_y - _QH_GAP)
                svg.append(f"<rect class='{css}' x='{n(x)}' y='{n(base_y - height)}' width='{_QH_BAR}' height='{n(height)}'/>")
        ticks_y.append(sy(month.created))
        if month.created:  # inflow tick: 2px ink, bar width + 8px each side, 1px surface halo
            d = f"M{n(cx - _QH_BAR / 2 - 8)},{n(sy(month.created))}H{n(cx + _QH_BAR / 2 + 8)}"
            svg.append(f"<path class='th' d='{d}'/><path class='tk' d='{d}'/>")
        partial = index == partial_index
        sub = "to date" if partial else (str(month.year) if index == 0 or month.month == 1 else "")
        svg.append(f"<text x='{n(cx)}' y='{_QH_Y0 + _QH_PH + 17}'>{_MONTH_ABBR[month.month - 1]}</text>")
        if sub:
            svg.append(f"<text class='yr' x='{n(cx)}' y='{_QH_Y0 + _QH_PH + 31}'>{sub}</text>")
        name = _history_month_name(month, history.as_of if partial else None)
        rows = "".join(f"<i class='{css}'></i><b>{value:,}</b><span>{escape(label)}</span>" for value, css, label in values)
        rows += (f"<span class='tot'></span><b class='tot'>{month.completed_total:,}</b><span class='tot'>completed</span>"
                 f"<i class='cr'></i><b>{month.created:,}</b><span>new COs created</span>")
        side = "r" if (count - 1 - index) * _QH_BAND >= 250 else "l"
        hits.append(f"<div class='qh-hit' style='left:{100 * index / count:.4f}%;width:{100 / count:.4f}%'>"
                    f"<div class='qh-tt {side}'><b>{escape(name)}</b><div class='qh-g'>{rows}</div></div></div>")
    svg.append(f"<line class='base' x1='{_QH_X0}' x2='{right}' y1='{_QH_Y0 + _QH_PH}' y2='{_QH_Y0 + _QH_PH}' "
               "shape-rendering='crispEdges'/>")
    complete = list(range(count - 1))
    if complete:  # selective direct labels: the peak and the last complete month, skipped on collision
        for index in dict.fromkeys([max(complete, key=lambda i: months[i].completed_total), complete[-1]]):
            month = months[index]
            label_y = sy(month.completed_total) - 6
            if month.completed_total and not label_y - 15 <= ticks_y[index] <= label_y + 7:
                cx = _QH_X0 + index * _QH_BAND + _QH_BAND / 2
                svg.append(f"<text class='cap' x='{n(cx)}' y='{n(label_y)}'>{month.completed_total:,}</text>")
    header = "".join(f"<th scope='col'>{escape(name)}</th>" for name in series)
    body = "".join(
        f"<tr><th scope='row'>{escape(_history_month_name(m, history.as_of if i == partial_index else None))}</th>"
        + "".join(f"<td>{value:,}</td>" for value in m.completed)
        + f"<td>{m.completed_total:,}</td><td>{m.created:,}</td></tr>" for i, m in enumerate(months))
    totals = [sum(m.completed[j] for m in months) for j in range(len(series))]
    foot = ("<tr><td>Total</td>" + "".join(f"<td>{value:,}</td>" for value in totals)
            + f"<td>{history.completed_total:,}</td><td>{sum(m.created for m in months):,}</td></tr>")
    rejected = f" {kpis.rejected_total:,} COs rejected all time (Salesforce has no rejection date to chart)." if kpis else ""
    first, last = months[0], months[-1]
    description = (f"Completed onboardings per month by product, {_history_month_name(first, None)} to "
                   f"{_history_month_name(last, history.as_of)}, with new COs created per month on the same axis. "
                   "All values are in the table view.")
    return (
        f"<figure class='qh'>{stats}<ul class='qh-legend'>{legend}</ul><div class='qh-scroll'><div class='qh-plot'>"
        f"<svg viewBox='0 0 {_QH_W} {_QH_H}' role='img' aria-labelledby='qh-title qh-desc'>"
        f"<title id='qh-title'>Closed onboardings per month</title><desc id='qh-desc'>{escape(description)}</desc>"
        f"{''.join(svg)}</svg><div class='qh-hits' aria-hidden='true'>{''.join(hits)}</div></div></div>"
        f"<details class='qh-table'><summary>Table view</summary><table><thead><tr><th scope='col'>Month</th>{header}"
        f"<th scope='col'>Completed</th><th scope='col'>New COs created</th></tr></thead><tbody>{body}</tbody>"
        f"<tfoot>{foot}</tfoot></table></details>"
        f"<p class='qh-note'>Salesforce, read at {escape(read_at)}. Completed = stage Onboarding Completed, by completion "
        f"month; new COs = all COs, by created month.{escape(rejected)}</p></figure>"
    )


def history_card(history: ClosedHistory | None, read_at: str, failed: bool = False) -> str:
    """The closed-onboardings card shown on the History tab."""
    if failed or history is None:
        body = ("<p class='empty'><span class='chip chip-warn'>History unavailable</span> The Salesforce history read "
                "failed. The onboarding queue is unaffected; reload in a minute to retry. If the Salesforce session "
                "expired, sign in again on <a href='/connection'>Connection</a>.</p>")
        summary = ""
    else:
        body = render_closed_history(history, read_at)
        created = sum(month.created for month in history.months)
        summary = (f"<span class='note'>{history.completed_total:,} completed · {created:,} created · "
                   f"last {len(history.months) - 1} months + this month</span>")
    return ("<section class='card' id='history' aria-labelledby='history-h'><div class='card-head'>"
            "<h2 class='pill' id='history-h'>Closed onboardings</h2>" + summary + "</div>" + body + "</section>")


def history_tile(history: ClosedHistory | None, failed: bool = False) -> str:
    """The filter-bar chip that summarizes the closed queue and opens the History tab."""
    kpis = history.kpis if history is not None else None
    if kpis is not None:
        delta = kpis.completed_30d - kpis.completed_prev_30d
        number, label = f"{kpis.completed_30d:,}", "Completed"
        sub = "30 days · " + ("no change" if delta == 0 else f"{delta:+d} vs prior")
    elif history is not None:
        number, label, sub = f"{history.completed_total:,}", "Completed", f"last {len(history.months)} months"
    else:
        number, label = "—", "Closed history"
        sub = "Unavailable · open to retry" if failed else "Monthly trend"
    return (f"<a class='fchip hist' href='/history'><b>{number}</b> {escape(label)} "
            f"<small>{escape(sub)}</small> <i aria-hidden='true'>&rarr;</i></a>")


def page_history(history: ClosedHistory | None, failed: bool = False) -> str:
    """The History tab: the closed-onboardings chart, stat strip, and table view."""
    read_at = (history.read_at if history is not None else "") or datetime.now().strftime("%H:%M")
    meta = (f"<span>Read from Salesforce at {escape(read_at)} · updates every {HISTORY_CACHE_SECONDS // 60} min</span>"
            if not failed and history is not None else "")
    head = f"<div class='page-head'><h1>History</h1><div class='head-meta'>{meta}</div></div>"
    return _app_shell("History", head + history_card(history, read_at, failed=failed or history is None),
                      active="history")


def render_history() -> str:
    """Read the cached closed-onboardings history and render the History tab.

    A history read failure degrades inside the page (never the connection
    page) because the history is independent of the open queue.
    """
    try:
        return page_history(closed_history())
    except ReadUnavailable:
        return page_history(None, failed=True)


# Queue keys stay stable for bookmarks and tests ("scanning" is the follow-up queue).
QUEUE_DEFINITIONS = (
    ("review", "Manual review", "Failed runs, duplicates, and source-data gaps."),
    ("ready", "Ready to onboard", "Approved in Salesforce; no tenant yet."),
    ("validation", "Needs validation", "Pending: check the DealHub product and term."),
    ("scanning", "Follow-up", "Tenant exists: Salesforce update, scan, customer user."),
)
_TENANT_STAGES = frozenset({"Account Scanning", "Scan Completed Successfully", "User Created"})
_TENANT_RESULTS = frozenset({"readback_verified", "readback_only_verified"})
_PRODUCT_SHORT = {"Surface": "Surface", "Credential Exposure": "Credential Exposure", "Surface & Credential Exposure": "Surface + CE"}
_TYPE_SHORT = {"New Product Onboarding": "New", "Renewal of Existing Product": "Renewal",
               "Renewal of Surface + New Credential Exposure Module": "Renewal + new CE",
               "Renewal of Credential Exposure Module + New Surface Product": "Renewal CE + new Surface"}


def classify_queue_row(row: dict[str, str | None], record: dict[str, str] | None) -> tuple[str, str, str]:
    """Return (queue key, next-step HTML, owner) for one open CO; the first matching rule wins.

    owner is "you" (the operator acts next), "runner" (an attended run is in
    progress), or "leonardo" (waiting on a scan). A tenant that already exists
    (verified run, local readback, or a Salesforce stage past approval) is a
    follow-up, never "ready to onboard" again.
    """
    approval, stage = row.get("Onboarding_Approval_Status__c"), row.get("Onboarding_Stage__c")
    result = record.get("result") if record else None
    if create_uncertain(record):
        return "review", "<b>Check Leonardo</b><span class='sub'>Create uncertain · verify read-only</span>", "you"
    if record is not None and record.get("route") in RENEWAL_ENGINES:
        if result is None:
            return "ready", "<span class='wait'>Waiting · runner</span><span class='sub'>A renewal run is in progress</span>", "runner"
        if renewal_uncertain(record):
            return "review", "<b>Check Leonardo</b><span class='sub'>Renewal uncertain · verify</span>", "you"
        if result in RENEWAL_RUN_SUCCESS:
            return "scanning", "<b>Renewed in Dev</b><span class='sub'>Salesforce updates stay manual</span>", "you"
        return "review", "<b>Review stopped renewal</b><span class='sub'>See the reason on the CO page</span>", "you"
    if record is not None and result is not None and result not in _TENANT_RESULTS:
        if result in DUPLICATE_RESULTS:
            return "review", "<b>Review existing tenant</b><span class='sub'>Duplicate check stopped the run</span>", "you"
        if result == "salesforce_id_already_present":
            return "review", "<b>Review existing tenant</b><span class='sub'>Already has an ID in Salesforce</span>", "you"
        return "review", "<b>Review failed run</b><span class='sub'>Nothing was created</span>", "you"
    local_state = row.get("Local_Leonardo_State")
    if result in _TENANT_RESULTS or local_state is not None or stage in _TENANT_STAGES:
        if row.get("Local_Scan_State") == "scan_completed" and stage in ("New", "Request Approved", "Account Scanning"):
            return "scanning", ("<b>Assign Operator, scanning off</b>"
                                "<span class='sub'>Leonardo scan completed</span>"), "you"
        if stage in ("New", "Request Approved"):
            return "scanning", "<b>Update Salesforce</b><span class='sub'>IDs and stage → Account Scanning</span>", "you"
        if stage == "Scan Completed Successfully":
            return "scanning", "<b>Create customer user</b>", "you"
        if stage == "User Created":
            return "scanning", "<b>Complete onboarding</b>", "you"
        evidence = ("<span class='sub'>Leonardo evidence indicates a tenant is scanning.</span>"
                    if local_state == "Account Scanning" else "")
        return "scanning", "<span class='wait'>Waiting · Leonardo scan</span>" + evidence, "leonardo"
    if record is not None and result is None:
        return "ready", "<span class='wait'>Waiting · runner</span><span class='sub'>An attended run is in progress</span>", "runner"
    if approval not in ("Pending", "Approved"):
        return "review", "<b>Set approval status</b><span class='sub'>Approval status not populated</span>", "you"
    if approval == "Approved" and stage in ("New", "Request Approved"):
        route = route_for(row)
        if route in SURFACE_ROUTES:
            return "ready", "<b>Review scope, start</b>", "you"
        if route == CE_ENGINE:
            return "ready", "<b>Start onboarding</b>", "you"
        return "ready", "<b>Onboard manually</b>", "you"
    if approval == "Pending" and stage == "New":
        return "validation", "<b>Validate DealHub term</b>", "you"
    return "review", "<b>Review source record</b>", "you"


def _run_chip(record: dict[str, str] | None) -> str:
    if record is None:
        return ""
    result = record.get("result")
    if result is None:
        return "<span class='chip chip-warn'>Running</span>"
    if result in _TENANT_RESULTS:
        return "<span class='chip chip-ok'>Onboarded</span>"
    if create_uncertain(record):
        return "<span class='chip chip-warn'>Create uncertain</span>"
    if record.get("route") in RENEWAL_ENGINES:
        if renewal_uncertain(record):
            return "<span class='chip chip-warn'>Renewal uncertain</span>"
        if result in RENEWAL_RUN_SUCCESS:
            return "<span class='chip chip-ok'>Renewed (Dev)</span>"
        return "<span class='chip chip-bad'>Renewal stopped</span>"
    if result in DUPLICATE_RESULTS:
        return "<span class='chip chip-bad'>Duplicate</span>"
    return "<span class='chip chip-bad'>Onboarding failed</span>"


def _route_chip(row: dict[str, str | None]) -> str:
    route = route_for(row)
    if route == CE_ENGINE:
        return "<span class='chip chip-info'>CE-only</span>"
    if route == SURFACE_ENGINE:
        return "<span class='chip chip-info'>Surface-only</span>"
    if route == CASE3_ENGINE:
        return "<span class='chip chip-info'>Surface + CE</span>"
    return "<span class='chip chip-neutral'>Manual</span>"


def _age_days(submitted: str | None, today: date) -> str:
    try:
        return f"{(today - date.fromisoformat((submitted or '')[:10])).days} d"
    except ValueError:
        return "—"


QUEUE_SORTS = frozenset({"start", "co", "queue", "age"})
_QUEUE_RANK = {"ready": 0, "review": 1, "validation": 2, "scanning": 3}
_QUEUE_CHIP = {"ready": "chip-ok", "review": "chip-bad", "validation": "chip-warn", "scanning": "chip-info"}
_NOT_YET_ONBOARDED = frozenset({"ready", "review", "validation"})


@_display_cached
def queue_start_dates(references: tuple[str, ...]) -> dict[str, str]:
    """Subscription start date (ISO) per CO, read-only, from the DealHub range in its comments.

    One batched query; only the validated date is kept (never the comment).
    A CO without exactly one valid range simply has no entry.
    """
    dates: dict[str, str] = {}
    for reference, summary in subscription_summaries(references).items():
        try:
            dates[reference] = date.fromisoformat(summary["Subscription_Start"]).isoformat()
        except ValueError:
            continue
    return dates


def _start_cell(start: str | None, key: str, today: date) -> str:
    if not start:
        return "<span class='muted'>—</span>"
    parsed = date.fromisoformat(start)
    days = (parsed - today).days
    label = f"{parsed:%b} {parsed.day}" + ("" if parsed.year == today.year else f", {parsed.year}")
    if days < 0:
        when = (f"<span class='chip chip-bad'>overdue {-days}d</span>" if key in _NOT_YET_ONBOARDED
                else f"<span class='sub'>{-days}d ago</span>")
    else:
        when = "<span class='sub'>today</span>" if days == 0 else f"<span class='sub'>in {days}d</span>"
    return f"<b>{label}</b>{when}"


def page_queue(rows: list[dict[str, str | None]], selected_queue: str = "", *,
               runner_state: dict[str, dict[str, str]] | None = None, runner_state_unavailable: bool = False,
               history: ClosedHistory | None = None, history_failed: bool = False,
               read_at: str | None = None, today: date | None = None, scan_started: bool = False,
               start_dates: dict[str, str] | None = None, start_dates_unavailable: bool = False,
               sort: str = "", direction: str = "", check_state: dict[str, dict[str, str]] | None = None,
               onboarded_dev: int = 0) -> str:
    """Render the open-onboardings dashboard: one filter bar (queues, history) and one sortable table.

    Default order is the soonest subscription start first (blank dates last),
    then Ready, Manual review, Needs validation, Follow-up. Without ``start_dates``
    (not given, or ``start_dates_unavailable`` after a failed read) the page orders by submission date.
    """
    today = today or date.today()
    read_at = read_at or datetime.now().strftime("%H:%M")
    state = runner_state or {}
    starts = start_dates or {}
    allowed = {key for key, _label, _helper in QUEUE_DEFINITIONS}
    selected = selected_queue if selected_queue in allowed else ""
    sort = sort if sort in QUEUE_SORTS else "start"
    if direction not in ("asc", "desc"):
        direction = "desc" if sort == "age" else "asc"
    descending = direction == "desc"
    classified = []
    for row in rows:
        record = state.get(row.get("Name") or "")
        key, step, owner = classify_queue_row(row, record)
        classified.append((row, record, key, step, owner))
    counts = {key: sum(1 for item in classified if item[2] == key) for key in allowed}
    need_you = sum(1 for item in classified if item[4] == "you")
    duplicates = sum(1 for item in classified if item[2] == "review" and item[1]
                     and (item[1].get("result") or "").startswith("duplicate"))
    automated = sum(1 for item in classified if item[2] == "ready" and item[4] == "you" and route_for(item[0]))
    running = sum(1 for item in classified if item[2] == "ready" and item[4] == "runner")
    waiting = sum(1 for item in classified if item[2] == "scanning" and item[4] == "leonardo")
    hints = {
        "review": f"{duplicates} duplicate" if duplicates else "Failed runs and data gaps",
        "ready": f"{automated} automated" + (f" · {running} running" if running else ""),
        "validation": "DealHub term check",
        "scanning": f"{waiting} waiting on Leonardo" if waiting else "Salesforce, scan, user",
    }
    visible = [item for item in classified if not selected or item[2] == selected]

    def start_of(item: tuple) -> str:
        name = item[0].get("Name") or ""
        return starts.get(name, "") if start_dates is not None else (item[0].get("Submission_Date__c") or "")[:10]

    def sort_value(item: tuple) -> str:
        if sort == "co":
            return item[0].get("Name") or ""
        if sort == "queue":
            return str(_QUEUE_RANK.get(item[2], 9))
        if sort == "age":  # days since submission, so the oldest sorts "highest" (default: descending)
            try:
                return f"{(today - date.fromisoformat((item[0].get('Submission_Date__c') or '')[:10])).days + 100000:06d}"
            except ValueError:
                return ""
        return start_of(item)

    base = sorted(visible, key=lambda item: (_QUEUE_RANK.get(item[2], 9), item[0].get("Name") or ""))
    filled = sorted((item for item in base if sort_value(item)), key=sort_value, reverse=descending)
    ordered = filled + [item for item in base if not sort_value(item)]  # blanks always last

    def link(queue: str | None = None, **changes: str) -> str:
        params = {"queue": selected if queue is None else queue, "sort": sort if sort != "start" else "",
                  "dir": direction if direction != ("desc" if sort == "age" else "asc") else "", **changes}
        query = urlencode({name: value for name, value in params.items() if value})
        return "/?" + query if query else "/"

    def chip(href: str, label: str, number: int, current: bool, extra: str = "") -> str:
        on = " on" if current else ""
        aria = " aria-current='page'" if current else ""
        return f"<a class='fchip{on}{extra}' href='{escape(href)}'{aria}>{escape(label)} <b>{number}</b></a>"

    chips = chip(link(""), "All open", len(rows), not selected)
    for key, label, _helper in QUEUE_DEFINITIONS:
        number = counts[key]
        extra = " ok" if key == "ready" and number else (" bad" if key == "review" and number else "")
        chips += chip(link(key), label, number, key == selected, extra)
    notes = [f"{need_you} need you"] + [hints[key] for key in ("ready", "review") if counts[key]]
    bar = (f"<nav class='fbar' aria-label='Queues and history'><span class='fgroup'>{chips}</span>"
           f"<span class='note'>{escape(' · '.join(notes))}</span>{history_tile(history, failed=history_failed)}</nav>")
    banner = ""
    if runner_state_unavailable:
        banner = _outcome_banner("info", "The local attended-run record could not be read, so run results are not shown. "
                                 "Open the CO page before acting.", "runner_state_unavailable",
                                 headline="Local run results unavailable")
    if start_dates_unavailable and rows:
        banner += ("<p class='note'>Subscription start dates could not be read from Salesforce, so the list is ordered "
                   "by submission date.</p>")

    def header(key: str, label: str, css: str = "") -> str:
        active = key == sort
        target = link(sort=key, dir=("asc" if descending else "desc") if active else "")
        order = "descending" if descending else "ascending"
        aria = f" aria-sort='{order}'" if active else ""
        cls = f" class='{css}'" if css else ""
        return f"<th scope='col'{cls}{aria}><a class='sort' href='{escape(target)}'>{escape(label)}</a></th>"

    head_row = ("<thead><tr>" + header("start", "Start", "c-start") + header("co", "Onboarding", "stick")
                + "<th scope='col'>Account</th>" + header("queue", "Queue")
                + "<th scope='col' class='c-prod'>Product</th><th scope='col' class='c-sf'>Salesforce</th>"
                "<th scope='col' class='c-auto'>Automation</th><th scope='col'>Next step</th>"
                + header("age", "Age", "num") + "<th scope='col' class='c-act'><span class='sr'>Action</span></th></tr></thead>")
    labels = {key: label for key, label, _helper in QUEUE_DEFINITIONS}
    body = ""
    for row, record, key, step, owner in ordered:
        reference = escape(row.get("Name") or "")
        product, onboarding_type = row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")
        leonardo = row.get("Local_Leonardo_State")
        run = _run_chip(record)
        production = _production_chip((check_state or {}).get(row.get("Name") or ""))
        if production and (record or {}).get("result") in ("duplicate_production_match", "production_clone_unavailable"):
            run = production
        drift = row.get("Local_Validation_Drift")
        automation = (_route_chip(row) + (f"<span class='run'>{run}</span>" if run else "")
                      + (f"<span class='sub'>Leonardo: {escape(leonardo)}</span>" if leonardo else "")
                      + (f"<span class='sub' style='color:var(--bad)'>⚠ {escape(drift)} setting(s) differ</span>"
                         if drift not in (None, "0") else ""))
        ready_row = key == "ready" and owner == "you"
        action = (f"<a class='btn sm' href='/co/{reference}'>Review &amp; start</a>" if ready_row
                  else f"<a href='/co/{reference}'>Open</a>")
        row_class = " class='is-ready'" if ready_row else ""
        body += (
            f"<tr{row_class}>"
            f"<td class='c-start'>{_start_cell(starts.get(row.get('Name') or '') or None, key, today)}</td>"
            f"<td class='stick'><a class='co' href='/co/{reference}'>{reference}</a></td>"
            f"<td>{escape(row.get('Account__r.Name') or 'Account unavailable')}</td>"
            f"<td><span class='chip {_QUEUE_CHIP[key]}'>{escape(labels[key])}</span></td>"
            f"<td class='c-prod'>{escape(_PRODUCT_SHORT.get(product or '', product or '—'))}"
            f"<span class='sub'>{escape(_TYPE_SHORT.get(onboarding_type or '', onboarding_type or '—'))}</span></td>"
            f"<td class='c-sf'>{escape(row.get('Onboarding_Stage__c') or '—')}"
            f"<span class='sub'>{escape(row.get('Onboarding_Approval_Status__c') or '—')}</span></td>"
            f"<td class='c-auto'>{automation}</td><td>{step}</td>"
            f"<td class='num'>{_age_days(row.get('Submission_Date__c'), today)}</td><td class='c-act'>{action}</td></tr>")
    if ordered:
        table = f"<div class='tbl-wrap'><table class='dense'>{head_row}<tbody>{body}</tbody></table></div>"
    elif rows:
        helper_label = labels.get(selected, "this view")
        table = f"<p class='empty'>Nothing in {escape(helper_label)} right now. <a href='/'>Show all open ({len(rows)})</a></p>"
    else:
        table = (f"<p class='empty'>No open onboardings. The Salesforce Open_Onboardings view returned 0 records at "
                 f"{escape(read_at)}.</p>")
    hidden = ((f"<input type='hidden' name='queue' value='{escape(selected)}'>" if selected else "")
              + (f"<input type='hidden' name='sort' value='{escape(sort)}'>" if sort != "start" else "")
              + "<input type='hidden' name='refresh' value='1'>")
    title = labels.get(selected, "Open onboardings")
    head = (f"<div class='page-head'><h1>{escape(title)}</h1><span class='note'>Read from Salesforce at "
            f"{escape(read_at)}</span><div class='head-meta'><form method='get' action='/'>{hidden}"
            "<button class='ghost' type='submit'>Refresh</button></form>"
            "<details class='menu'><summary>More</summary><div class='menu-pop'>"
            "<form method='post' action='/attended/scan-status-refresh-all'>"
            "<button class='ghost' type='submit' title='Read-only: reads each onboarded Surface tenant&#39;s scan status'>"
            "Refresh scan statuses</button></form><form method='post' action='/attended/validate-all'>"
            "<button class='ghost' type='submit' title='Read-only: compares each onboarded tenant with its plan'>"
            "Validate all</button></form></div></details></div></div>")
    if scan_started:
        banner = ("<p class='note'>A read-only scan-status sweep of every onboarded Surface tenant was started in the "
                  "automation browser. Reload in about a minute.</p>") + banner
    onboarded_link = f"<p class='note'><a href='/dev/onboarded'>{onboarded_dev} onboarded on Dev &rarr;</a></p>"
    return _app_shell("Onboardings", head + bar + banner + onboarded_link + f"<section class='card tbl-card'>{table}</section>",
                      active="onboardings", wide=True)


def ce_only_eligible(row: dict[str, str | None]) -> bool:
    """True when the detail row has exactly one valid Email_Domains__c value.

    This mirrors the CE-only owner gate used by the fill preflight (exactly one
    valid email domain). It is used only to decide whether the automated CE-only
    Onboard action is surfaced on a CO detail page; the preflight re-reads the
    source and is the authoritative gate before any run starts.
    """
    if (row.get("Onboarding_Product__c") != CE_ROUTE_PRODUCT
            or row.get("Onboarding_Type__c") != CE_ROUTE_TYPE):
        # Only a new Credential Exposure onboarding may use the CE-only route.
        return False
    raw = row.get("Email_Domains__c")
    if not isinstance(raw, str):
        return False
    # Salesforce often holds "@example.com"; one leading "@" is stripped (nothing else is normalised).
    domains = [item.removeprefix("@") for item in re.split(r"[,;\s]+", raw.strip()) if item]
    if len(domains) != 1:
        return False
    domain = domains[0].casefold()
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", domain))


def _domain_count(raw: str | None) -> str:
    items = [item for item in re.split(r"[,;\s]+", (raw or "").strip()) if item]
    return str(len(items)) if items else "—"


# --- Stage tracker (2026-10-06) ------------------------------------------------
# DASHBOARD-ONLY and derived: nothing here is written to Salesforce.
# TODO: on the move to production ask the owner again: Salesforce Onboarding Stage
# must then be updated (today Salesforce's own stage is only displayed next to it).
ONBOARDING_STAGES = ("New", "Request Approved", "Account Scanning", "Scan Completed Successfully",
                     "User Created", "Onboarding Completed")
_STAGE_HINT = (
    "Waiting for the approval in Salesforce.",
    "Approved; the tenant is not created yet.",
    "Tenant created; the first scan is running or pending.",
    "Scan finished (Leonardo shows a completed status or finished executions); assign the Operator Account and create the customer user.",
    "Operator account and customer user exist; complete the onboarding in Salesforce.",
    "Onboarding is completed in Salesforce.",
)


def derive_onboarding_stage(salesforce_stage: str | None, approval_status: str | None, *,
                            tenant_created: bool = False, scan_done: bool = False,
                            user_created: bool | None = None) -> dict[str, object]:
    """Pure: the furthest stage that is PROVABLE, never beyond the first missing proof.

    Each stage needs every earlier stage plus its own evidence: approval "Approved"
    (stage 2), a verified create/readback (3), a finished scan read (4), operator AND
    customer user present (5). Salesforce's own stage also counts as evidence for itself
    and everything before it. "Onboarding Completed" can only come from Salesforce.
    Unknown (None) or false evidence stops the chain.
    """
    sf_index = ONBOARDING_STAGES.index(salesforce_stage) if salesforce_stage in ONBOARDING_STAGES else None
    floor = -1 if sf_index is None else sf_index
    local = (approval_status == "Approved", tenant_created is True, scan_done is True, user_created is True, False)
    index = 0
    for step, proven in enumerate(local, start=1):
        if proven or floor >= step:
            index = step
        else:
            break
    return {"index": index, "stage": ONBOARDING_STAGES[index], "salesforce_index": sf_index,
            "ahead": sf_index is not None and index > sf_index,
            "behind": sf_index is not None and index < sf_index}


def user_created_evidence(tenant: object, environment: str) -> dict[str, object]:
    """Pure: operator / customer-user parts of "User Created" from one minimized inventory row.

    Each part is True, False, or None (not collected). The production clone (Redash) only
    carries the operator flag, so there the customer user stays None (never guessed).
    """
    row = tenant if isinstance(tenant, dict) else {}
    operator = row.get("operator_assigned")
    operator = operator if type(operator) is bool else None
    primary = row.get("primary_user")
    customer = primary.get("present") if environment == "dev" and isinstance(primary, dict) else None
    customer = customer if type(customer) is bool else None
    parts = {"operator account": operator, "customer user": customer}
    created = (False if any(value is False for value in parts.values())
               else None if any(value is None for value in parts.values()) else True)
    terms = row.get("terms_accepted")
    return {"created": created, "operator": operator, "customer": customer,
            "missing": [name for name, value in parts.items() if value is False],
            "unknown": [name for name, value in parts.items() if value is None],
            "terms_accepted": terms if type(terms) is bool else None}


def user_created_proof(primary_user_matches: object, operator_assigned: object, manual: dict[str, object] | None,
                       checked_on: str = "") -> dict[str, object]:
    """Pure: "User Created" = (primary user verified AND operator assigned) OR a manual confirmation."""
    verified = primary_user_matches is True
    operator = operator_assigned is True
    if manual and manual.get("confirmed") is True:
        proof, text = "manual", "Confirmed manually on " + str(manual.get("confirmed_on") or "an earlier date")
    elif verified and operator:
        proof, text = "automatic", "Primary user verified in Leonardo" + (" on " + checked_on if checked_on else "")
    else:
        proof = None
        missing = []
        if not verified:
            missing.append("Primary user not verified — run Validate")
        if not operator:
            missing.append("No operator assigned")
        text = "; ".join(missing)
    return {"created": proof is not None, "proof": proof, "text": text}


def _stage_tenant(reference: str, row: dict[str, str | None], readback: dict[str, str] | None,
                  now: datetime | None = None) -> tuple[dict[str, object] | None, str, str]:
    """Local, read-only: (inventory row, environment, captured_at) for this CO, else (None, "", "").

    Dev snapshot for a CO with a Leonardo readback; production clone for a CO that already
    carries both Salesforce IDs. Same id AND uuid must match (inventory.match_readbacks).
    """
    if readback is not None:
        environment, ids = "dev", readback
    elif (row.get("Surface_Account_ID__c") or "").strip() and (row.get("Account_UUID__c") or "").strip():
        environment = "prod-clone"
        ids = {"surface_account_id": (row.get("Surface_Account_ID__c") or "").strip(),
               "account_uuid": (row.get("Account_UUID__c") or "").strip()}
    else:
        return None, "", ""
    try:
        payload = inventory.load_latest(inventory_root(), environment, max_age=INVENTORY_DISPLAY_MAX_AGE,
                                        now=now or datetime.now(timezone.utc))
        tenant_id = inventory.match_readbacks(payload, {reference: ids})["by_reference"].get(reference)
    except (inventory.InventoryError, OSError, ValueError, TypeError, AttributeError):
        return None, environment, ""
    for tenant in payload.get("tenants") or ():
        if isinstance(tenant, dict) and tenant_id not in (None, "conflict") and tenant.get("id") == tenant_id:
            return tenant, environment, str(payload.get("captured_at") or "")
    return None, environment, ""


def _stage_state(reference: str, row: dict[str, str | None], readback: dict[str, str] | None) -> dict[str, object]:
    """Gather the local evidence once and derive the stage (dashboard-only)."""
    try:
        record = load_runner_state().get(reference)
    except Exception:  # noqa: BLE001 - a missing or unreadable run record is simply no evidence
        record = None
    tenant_created = readback is not None or (isinstance(record, dict) and record.get("result") in _TENANT_RESULTS)
    try:
        scan = attended_scan_statuses().get(reference)
    except Exception:  # noqa: BLE001
        scan = None
    # Proof of a finished scan: the Details executions read says "done", OR Leonardo's own row status is a
    # confirmed completed value (observation state scan_completed). Running/failed/unrecognized/no_scan never count.
    scan_done = bool(scan and (scan.get("execution_state") == "done" or scan.get("state") == "scan_completed"))
    tenant, environment, captured = _stage_tenant(reference, row, readback)
    evidence = user_created_evidence(tenant, environment) if tenant is not None else None
    try:
        validation = attended_validations().get(reference) or {}
    except Exception:  # noqa: BLE001
        validation = {}
    try:
        reminder_operator = bool(load_attended_reminders().get(reference, {}).get(REMINDER_FIELDS["operator_assigned"]))
    except ReadUnavailable:
        reminder_operator = False
    operator = (evidence["operator"] is True if evidence else False) or reminder_operator
    proof = user_created_proof(validation.get("primary_user_matches"), operator,
                               load_user_created_confirmations().get(reference),
                               str(validation.get("primary_user_checked_on") or ""))
    result = derive_onboarding_stage(row.get("Onboarding_Stage__c"), row.get("Onboarding_Approval_Status__c"),
                                     tenant_created=tenant_created, scan_done=scan_done,
                                     user_created=proof["created"])  # type: ignore[arg-type]
    return {**result, "evidence": evidence, "user_proof": proof, "environment": environment, "captured_at": captured,
            "tenant": tenant, "scan_done": scan_done, "tenant_created": tenant_created, "operator": operator,
            "validation": validation, "scan": scan, "record": record}


def _user_evidence_text(state: dict[str, object]) -> str:
    proof = state.get("user_proof")
    if isinstance(proof, dict):
        return "User Created: " + str(proof["text"]) + "."
    evidence = state.get("evidence")
    if not isinstance(evidence, dict):
        return "User Created: not checked (no matching tenant in the local inventory snapshot)."
    def part(label: str, value: object) -> str:
        return label + ": " + ("present" if value is True else "missing" if value is False else "not collected")
    source = "Leonardo Development snapshot" if state.get("environment") == "dev" else "production clone"
    when = " · captured " + str(state["captured_at"])[:16] if state.get("captured_at") else ""
    return ("User Created (" + source + when + "): " + part("operator account", evidence["operator"]) + " · "
            + part("customer user", evidence["customer"]))


def _user_confirm_form(state: dict[str, object], reference: str) -> str:
    """Small secondary button: confirm the customer user manually, or undo that confirmation."""
    proof = state.get("user_proof")
    manual = isinstance(proof, dict) and proof.get("proof") == "manual"
    if state.get("index") is None or (not manual and int(state["index"]) >= 5):  # type: ignore[call-overload]
        return ""
    action, label = ("/attended/unconfirm-user-created", "Undo") if manual else ("/attended/confirm-user-created", "Confirm user created")
    return ("<form method='post' action='" + action + "'><input type='hidden' name='reference' value='" + escape(reference)
            + "'><button type='submit' class='ghost sm'>" + label + "</button></form>")


def _stage_tracker_html(state: dict[str, object], reference: str = "") -> str:
    """Horizontal stepper (done / current / upcoming) with accessible text; no scripts or assets.

    ``index`` is the last stage with proof, so it is drawn as done (owner 2026-10-07: a reached stage that still
    looked "current" read as not done); the stage after it is the current one.
    """
    index = int(state["index"])  # type: ignore[call-overload]
    items = []
    for number, name in enumerate(ONBOARDING_STAGES):
        if number <= index:
            cls, word, mark = "done", "done", "✓"
        elif number == index + 1:
            cls, word, mark = "current", "current stage", str(number + 1)
        else:
            cls, word, mark = "upcoming", "upcoming", str(number + 1)
        items.append("<li class='st " + cls + "'" + (" aria-current='step'" if cls == "current" else "") + ">"
                     "<span class='dot' aria-hidden='true'>" + mark + "</span><span class='lbl'>" + escape(name)
                     + "<span class='sr'> — " + word + "</span></span></li>")
    sf_index = state.get("salesforce_index")
    # Owner 2026-10-07: one note line, only when the derived stage is ahead of Salesforce. The user-evidence text
    # and the Confirm user created form moved to the "What to do now" card.
    note = ""
    if state.get("ahead") and sf_index is not None:
        note = ("<p class='tracker-note'><span class='chip chip-warn'>Salesforce still shows: "
                + escape(ONBOARDING_STAGES[int(sf_index)]) + "</span></p>")  # type: ignore[call-overload]
    return ("<nav class='tracker' aria-label='Onboarding stage'><ol class='stages'>" + "".join(items) + "</ol>"
            + note + "</nav>")


_DOMAIN_NOTE_TEXT = {
    "space_separated": "entries were separated by spaces",
    "duplicate_removed": "duplicate entries were removed",
    "main_repeated": "the main domain was repeated in Alternate Domains",
    "lowercased": "uppercase letters were lowercased",
    "trailing_dot_removed": "a trailing dot was removed",
    "www_removed": "a leading www. was removed",
    "url_reduced_to_host": "a URL was reduced to its domain",
}
_DOMAIN_REJECT_TEXT = {
    "wildcard": "wildcards are not supported", "url": "a URL, not a domain", "email": "an email address, not a domain",
    "public_suffix": "a public suffix, not a registrable domain", "network": "an IP or network (not supported)",
    "malformed": "not a valid domain", "whitespace": "contains whitespace",
}
# No page runs a script (owner 2026-10-07: the cleaned domain value is shown as text, no Copy box).
PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"


def _domain_lists(info: dict[str, object]) -> str:
    """The domain lists exactly as the tenant form will receive them (read-only)."""
    shown = (("Main domain", info["main"] or "Not valid"),
             ("Alternate Domains", info["alternate_domains"] or "None"),
             ("SubDomains", info["subdomains"] or "None"))
    return "<dl>" + "".join("<dt>" + escape(label) + "</dt><dd>" + escape(str(value)) + "</dd>" for label, value in shown) + "</dl>"


def _domains_banners(info: dict[str, object]) -> tuple[str, str]:
    """(banners, "") of the domains preview: main-domain error, rejected entries, the cleaned value as plain text.

    The CO page shows these outside any fold (a blocker must never be hidden). The second value is kept for the
    callers and is always empty (no script).
    """
    body = ""
    if info["main_error"]:
        body += ("<div class='banner banner-bad'><b>Main_Domain__c is not a single registrable domain.</b> Nothing can run "
                 "until it is corrected in Salesforce.</div>")
    if info["rejected"]:
        items = "".join("<li><code>" + escape(item["entry"]) + "</code> — "
                        + escape(_DOMAIN_REJECT_TEXT.get(item["reason"], item["reason"])) + "</li>" for item in info["rejected"])
        body += ("<div class='banner banner-bad'><b>These Alternate Domains entries are rejected:</b><ul>" + items
                 + "</ul>Nothing can run until Salesforce is corrected.</div>")
    script = ""
    if info["notes"]:
        human = "; ".join(_DOMAIN_NOTE_TEXT.get(code, code) for code in info["notes"])
        body += "<div class='banner banner-warn'><b>Alternate Domains cleaned</b> (" + escape(human) + ")"
        if info["salesforce_clean_value"]:
            body += ": " + escape(info["salesforce_clean_value"])
        body += "</div>"
    return body, script


def _domains_card(row: dict[str, str | None]) -> str:
    """Read-only 'Domains for the tenant' card: exactly what the tenant form will receive.

    Built from the Salesforce row already loaded (no extra read). Cleanups are
    shown with the clean value to paste back into Salesforce; rejected entries
    block the run until Salesforce is corrected. Nothing is written anywhere.
    """
    info = format_surface_domains(row.get("Main_Domain__c"), row.get("Alternate_Domains__c"))
    chip = "<span class='chip chip-ok'>Ready</span>" if info["result"] == "ok" else "<span class='chip chip-bad'>Blocked</span>"
    body, script = _domains_banners(info)
    return ("<section class='card' aria-labelledby='domains-title'><div class='card-head'><h2 id='domains-title' class='pill'>"
            "Domains for the tenant</h2>" + chip + "</div>" + _domain_lists(info) + body
            + "<p class='note'>Read-only preview from Salesforce; nothing is written back.</p>" + script + "</section>")


def page_detail(reference: str, row: dict[str, str | None], notification: str = "", commercial_readiness: dict[str, object] | None = None) -> str:
    """CO detail (owner-approved layout 2026-10-07), top to bottom: header, stage tracker, pinned banners (never
    inside a fold), ONE "What to do now" card, at most 10 key facts, then folds (Tenant checks, Salesforce record,
    Run history, Diagnostics) and one footer line.

    Renewal COs (owner decision 2026-10-04) have no primary action: the card shows the next CLI step as text, and
    the production sign-in and Leonardo Development manual actions live in Diagnostics.
    """
    rows = "".join(f"<dt>{escape(label)}</dt><dd>{escape(row.get(field) or 'Not populated')}</dd>" for label, field in DETAIL_DISPLAY_FIELDS)
    comment_validation = extract_dealhub_dates(row.get("Onboarding_Comments__c"))
    is_renewal = "renewal" in (row.get("Onboarding_Type__c") or "").casefold()
    case4_panel = ""
    renewal_preflight = ""
    if is_renewal:
        renewal_preflight = (
            "<section class='login-preflight' aria-labelledby='renewal-preflight-title'><div><h2 id='renewal-preflight-title'>Production renewal account validation</h2>"
            "<p>Step 1: open the production BackOffice login and complete SSO/MFA manually. This preflight stops before any tenant search, edit, or save action.</p>"
            "<p class='login-safety'>The subsequent read-only account-existence lookup requires an approved desktop Playwright runtime and a reviewed production page schema. No credentials, cookies, MFA codes, or browser state are read or retained.</p></div>"
            "<form method='post' action='/attended/production-renewal-preflight'><input type='hidden' name='reference' value='" + escape(reference) + "'><button class='ghost' type='submit'>Open production sign-in</button></form></section>"
        )
    is_case4 = row.get("Onboarding_Product__c") == CASE4_PRODUCT and row.get("Onboarding_Type__c") == CASE4_TYPE
    if is_case4:
        case4 = validate_case4_onboarding_comments(row.get("Onboarding_Comments__c"), source_read_available=True)
        if case4["decision"] == "manual_review_required":
            case4_message = "Manual review required: Onboarding Comments do not contain one valid subscription date range."
        else:
            case4_message = "One subscription date range is present. Case 4 remains blocked because its route is not mapped yet."
        case4_panel = (
            "<section class='readiness source-blocked' aria-labelledby='case4-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>!</span><div><h2 id='case4-title'>Case 4 comments validation</h2>"
            "<p>Renew Surface + New Credential Exposure — validation only. " + escape(case4_message) + "</p>"
            "<p class='login-safety'>Status: " + escape(str(case4["decision"])) + "; engine: " + escape(CASE4_ENGINE_VALUE) + ". "
            "No existing-account lookup, Salesforce write, or Leonardo action is performed here.</p></div></div></section>"
        )
    comment_repair_action = ""
    if reference == "CO-0741" and row.get("Onboarding_Product__c") == CASE4_PRODUCT and row.get("Onboarding_Type__c") == CASE4_TYPE:
        repair_eligible = (row.get("Onboarding_Comments__c") in (None, "")
                           and row.get("Onboarding_Stage__c") == "New"
                           and row.get("Onboarding_Approval_Status__c") == "Pending")
        if repair_eligible:
            comment_repair_action = (
            "<section class='login-preflight' aria-labelledby='comment-repair-title'><div><h2 id='comment-repair-title'>Re-run DealHub comment evaluation</h2>"
            "<p>Read CO-0741 and the selected active Surface Go 500-subdomain subscription. A successful result shows a separate, revision-bound Salesforce update confirmation.</p>"
            "<p class='login-safety'>This reads Salesforce only. It cannot overwrite a non-empty comment or make a Leonardo change.</p></div>"
            "<form method='post' action='/attended/rerun-comment-evaluation'><input type='hidden' name='reference' value='CO-0741'><button class='ghost' type='submit'>Re-run evaluation</button></form></section>"
            )
        else:
            comment_repair_action = ("<section class='manual-action'><div><h2>Re-run DealHub comment evaluation</h2>"
                                     "<p>This repair action is unavailable because CO-0741 no longer has the required empty-comment, New, Pending pre-state.</p></div>"
                                     "<button type='button' disabled aria-disabled='true'>Re-run evaluation unavailable</button></section>")
    renewal_comment_evaluation_action = ""
    if reference == CO0745_REFERENCE:
        co0745_eligible = (
            row.get("Onboarding_Comments__c") in (None, "")
            and row.get("Onboarding_Stage__c") == "New"
            and row.get("Onboarding_Approval_Status__c") == "Pending"
            and is_renewal
            and "surface" in (row.get("Onboarding_Product__c") or "").casefold()
        )
        if co0745_eligible:
            renewal_comment_evaluation_action = (
                "<section class='login-preflight' aria-labelledby='co0745-evaluation-title'><div>"
                "<h2 id='co0745-evaluation-title'>Validate DealHub renewal term</h2>"
                "<p>Read CO-0745 and require one active Surface baseline subscription. A successful result only displays a proposed Onboarding Comments date range.</p>"
                "<p class='login-safety'>This is read-only. Production existing-account validation and a separate final approval remain required before any Salesforce update.</p></div>"
                "<form method='post' action='/attended/rerun-co0745-renewal-evaluation'><input type='hidden' name='reference' value='CO-0745'><button class='ghost' type='submit'>Run read-only evaluation</button></form></section>"
            )
        else:
            renewal_comment_evaluation_action = (
                "<section class='manual-action'><div><h2>Validate DealHub renewal term</h2>"
                "<p>This evaluation requires CO-0745 to remain an empty-comment, New, Pending Surface renewal.</p></div>"
                "<button type='button' disabled aria-disabled='true'>Evaluation unavailable</button></section>"
            )
    source_ready = source_ready_to_onboard(row)
    approval = row.get("Onboarding_Approval_Status__c")
    commercial_ready = bool(commercial_readiness and commercial_readiness.get("commercial_ready"))
    commercial_manual_review = bool(commercial_readiness and commercial_readiness.get("manual_review_required"))
    route = route_for(row)
    case = renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    # Automated routes only: exact Product + Type; a renewal and Case 4 never start from this page.
    ce_automated = not is_case4 and ce_only_eligible(row) and not is_renewal
    surface_automated = not is_case4 and not is_renewal and route in SURFACE_ROUTES
    readback = attended_leonardo_readbacks().get(reference)
    # The stage is computed once and reused by every part of the page.
    stage_state = _stage_state(reference, row, readback)
    index = int(stage_state["index"])  # type: ignore[call-overload]
    mirror = readback is not None and readback["surface_account_id"] in dev_mirror_ids()
    sf_id_blocked = (source_ready and (ce_automated or surface_automated) and salesforce_id_already_present(route, row)
                     and readback is None)
    parts: dict[str, Any] | None = None
    if source_ready and (ce_automated or surface_automated) and not sf_id_blocked:
        parts = _ce_only_onboard_parts(reference) if ce_automated else _surface_onboard_parts(reference, route or SURFACE_ENGINE)
    # Diagnostics: the manual start / session check (routes without an automated onboarding), the renewal
    # production sign-in preflight, the one-off CO-0741 / CO-0745 repair actions and the Case 4 route button.
    manual_action = ""
    if source_ready and is_case4:
        manual_action = (
            "<section class='manual-action' aria-labelledby='case4-route-title'><div><h2 id='case4-route-title'>Case 4 Leonardo workflow</h2>"
            "<p>Source is ready to onboard, but the Case 4 route remains unmapped and cannot start Leonardo onboarding.</p></div>"
            "<button type='button' disabled aria-disabled='true'>Case 4 route blocked</button>"
            "<p class='manual-blocker'>Blocked independently from Salesforce source readiness: owner-approved Case 4 mapping is required.</p></section>"
        )
    elif source_ready and not (ce_automated or surface_automated):
        nonce = manual_start_ack_nonce(reference, row)
        # One primary action: the session check until it is recorded, then the manual start.
        # A renewal has no primary action at all.
        check_class = " class='ghost'" if is_renewal or nonce is not None else ""
        session_check = (
            "<section class='login-preflight' aria-labelledby='login-preflight-title'><div><h2 id='login-preflight-title'>Leonardo Development session check</h2>"
            "<p>Open the exact Development tenant-management route. Your browser will show tenant management when the current session is active or redirect to SSO/MFA when it is not.</p>"
            "<p class='login-safety'>This dashboard cannot inspect VPN state, credentials, cookies, or browser state. The resulting browser page is the attended authentication signal; it does not clear any authority, duplicate, mapping, or execution gate.</p></div>"
            "<form method='post' action='/attended/leonardo-session-check'><input type='hidden' name='reference' value='" + escape(reference) + "'><button" + check_class + " type='submit'>Check Leonardo Development session</button></form></section>"
        )
        if nonce is None:
            manual_action = (
                "<section class='manual-action' aria-labelledby='manual-action-title'><div><h2 id='manual-action-title'>Manual Leonardo onboarding</h2>"
                "<p>Available after a fresh attended Leonardo Development session check for this exact source revision.</p></div>"
                "<button type='button' disabled aria-disabled='true' title='Run the attended session check first'>Start manual onboarding</button>"
                "<p class='manual-blocker'>Blocked: session check, explicit admin-session attestation, and the remaining manual gates are required.</p></section>"
            )
        else:
            manual_action = (
                "<section class='manual-action manual-ready' aria-labelledby='manual-action-title'><div><h2 id='manual-action-title'>Manual Leonardo onboarding</h2>"
                "<p>A fresh session check is recorded for this source revision. Attest that your Leonardo Development admin session remains active to open tenant management and begin the attended workflow.</p></div>"
                "<form method='post' action='/attended/start-manual-onboarding'><input type='hidden' name='reference' value='" + escape(reference) + "'><input type='hidden' name='nonce' value='" + escape(nonce) + "'>"
                "<label><input type='checkbox' name='admin_session_active' value='1' required> I attest that my Leonardo Development admin session is active.</label>"
                "<button" + (" class='ghost'" if is_renewal else "") + " type='submit'>Start manual onboarding</button></form>"
                "<p class='manual-blocker'>This opens tenant management only. It does not fill, submit, create, or authorize a tenant.</p></section>"
            )
        manual_action = session_check + manual_action
    plan_data = _renewal_plan_data(row) if is_renewal else (True, None)
    outcome = attended_renewal_outcomes().get(reference) if is_renewal else None
    domain_info = (format_surface_domains(row.get("Main_Domain__c"), row.get("Alternate_Domains__c"))
                   if route in SURFACE_ROUTES or (case or (None,))[0] in RENEWAL_ENGINES else None)
    try:
        validation_all = attended_validations()
    except Exception:  # noqa: BLE001 - local evidence only; absence means none
        validation_all = {}
    scan_obs = stage_state.get("scan") if isinstance(stage_state.get("scan"), dict) else None
    spycloud = attended_spycloud_states().get(reference) if readback is not None and route in (CE_ENGINE, CASE3_ENGINE) else None
    renewal_record, renewal_state_error = None, False
    if is_renewal:
        try:
            renewal_record = load_runner_state().get(reference)
        except RunnerStateUnavailable:
            renewal_state_error = True
    id_route = route or (case[0] if case is not None and case[0] in RENEWAL_ENGINES else None)
    ctx = SimpleNamespace(
        reference=reference, row=row, route=route, case=case, approval=approval, source_ready=source_ready,
        commercial_ready=commercial_ready, commercial_manual_review=commercial_manual_review,
        comment_ready=bool(comment_validation["ready_for_cse_review"]), is_renewal=is_renewal, is_case4=is_case4,
        ce_automated=ce_automated, surface_automated=surface_automated, readback=readback, stage_state=stage_state,
        index=index, mirror=mirror, sf_id_blocked=sf_id_blocked, parts=parts, plan_data=plan_data, outcome=outcome,
        domain_info=domain_info, validation=validation_all.get(reference), scan=scan_obs, spycloud=spycloud,
        id_route=id_route, renewal_record=renewal_record, renewal_state_error=renewal_state_error,
        notification=notification)
    toast = ""
    notifications = {"verified": ("Update verified", "CO-0741 was refreshed from Salesforce. Comments, Stage, and Approval Status are verified."), "blocked": ("Update blocked", "No verified update was completed. Reconcile the current Salesforce value before a new evaluation.")}
    if notification.startswith("id-write:") and notification[9:] in ID_WRITEBACK_RESULTS:
        kind, text = ID_WRITEBACK_RESULTS[notification[9:]]
        notifications[notification] = ("Salesforce ID written" if kind == "verified" else "Salesforce ID not written", text + ".")
    if notification in notifications:
        title, message = notifications[notification]
        toast = "<section class='toast' role='status' aria-live='polite'><strong>" + escape(title) + "</strong><span>" + escape(message) + "</span></section>"
    pinned = _pinned_banners(ctx, case4_panel, commercial_readiness)
    # Tenant checks fold: validation, scan status, SpyCloud and the local readback (no Environment rows).
    health = ""
    if readback is not None:
        tiles = _validation_section(reference, notification, mirror=mirror)
        if route != CE_ENGINE:
            tiles += _scan_status_section(reference, notification)
        if route in (CE_ENGINE, CASE3_ENGINE):
            tiles += _spycloud_section(reference, notification)
        readback_html = ("<div class='sub-block' aria-labelledby='leonardo-readback-title'>"
                         "<h3 id='leonardo-readback-title' class='sub-h'>Leonardo Development readback"
                         + (" " + DEV_MIRROR_CHIP if mirror else "") + "</h3>"
                         "<p class='note'>Local operator evidence only; Salesforce remains unchanged.</p><dl>"
                         f"<dt>Leonardo state</dt><dd>{escape(readback['leonardo_state'])}</dd>"
                         f"<dt>Observed</dt><dd>{escape(readback['observed_on'])}</dd></dl></div>")
        health = _fold("Tenant checks", "Validation · scan status" + (" · SpyCloud" if route in (CE_ENGINE, CASE3_ENGINE) else ""),
                       "<div class='health'>" + tiles + "</div>" + readback_html,
                       open_=bool(notification) or _tenant_checks_need_attention(ctx))
    ids_html = _salesforce_ids_section(id_route, readback, row, reference, "", force_open=index == 4)
    ids_open = "' open " in ids_html[:160]
    domain_lists = ""
    if domain_info is not None:
        domain_lists = "<h3 class='sub-h'>Domains for the tenant</h3>" + _domain_lists(domain_info)
    record_fold = _fold("Salesforce record", str(len(DETAIL_DISPLAY_FIELDS)) + " fields · domains · Salesforce IDs",
                        "<dl>" + rows + "</dl>" + domain_lists + ids_html, open_=ids_open)
    history_html = _run_history_html(ctx)
    history = _fold("Run history", "Outcomes · licence entered · plan · scope", history_html) if history_html else ""
    cli_hint = ""
    if is_renewal and renewal_supported(case):
        cli_hint = ("<p class='note'>" + RENEWAL_CLI_HINT_TEXT + "<code>tools\\attended_ce_only_playwright.py --co "
                    + escape(reference) + " --renew</code> (dry run), <code>--mirror-renewal</code>, "
                    "<code>--confirm-write</code>.</p>")
    diagnostics_html = cli_hint + renewal_preflight + comment_repair_action + renewal_comment_evaluation_action + manual_action
    if parts is not None:
        diagnostics_html += ("" if parts["state"] != "duplicate" else str(parts["reset"])) + str(parts.get("meta", ""))
    diagnostics = (_fold("Diagnostics", "Manual start · sign-in preflight · repair actions · source revision", diagnostics_html)
                   if diagnostics_html else "")
    account = row.get("Account_Name__c")
    approval_chip = ("<span class='chip chip-ok'>Approved</span>" if approval == "Approved"
                     else "<span class='chip chip-warn'>" + escape(approval or "Approval not populated") + "</span>")
    route_chip = ("<span class='chip chip-info'>" + escape(case[1]) + "</span>" if case is not None else _route_chip(row))
    header = ("<div class='page-head'><h1>" + escape(reference) + "</h1>"
              + ("<span class='acct'>" + escape(account) + "</span>" if account else "") + route_chip + approval_chip
              + "<div class='head-meta'><span>Read from Salesforce at " + escape(display_read_at()) + "</span>"
              "<form method='get' action='/co/" + escape(reference) + "'><input type='hidden' name='refresh' value='1'>"
              "<button class='ghost' type='submit'>Refresh</button></form></div></div>")
    # Segmented control (owner 2026-10-07): the Production chip only peeks at the display cache (never reads Salesforce).
    cached_match = peek_display_cache("production_match_display", reference, row.get("Onboarding_Product__c") or "",
                                      row.get("Onboarding_Type__c") or "")
    seg = _env_segment(reference, "dev", _dev_status_chip(dev_evidence_for(reference)),
                       _match_chip(cached_match["status"]) if isinstance(cached_match, dict) and "status" in cached_match
                       else "<span class='chip chip-neutral'>Not checked</span>")
    main_html = ("<a class='crumb' href='/'>&larr; Open Onboardings</a>" + header + seg + _tenant_ids_block(ctx)
                 + _stage_tracker_html(stage_state, reference)
                 + toast + ("<div class='pinned' role='region' aria-label='Alerts'>" + pinned + "</div>" if pinned else "")
                 + _do_now_card(ctx) + _key_facts_grid(_key_facts(ctx)) + health + record_fold + history + diagnostics
                 + "<p class='footline'>" + escape(DETAIL_FOOTER_NOTE) + "</p>")
    # Refresh only while a run is open (CSP allows no script); a finished run ends the refresh by itself.
    return _app_shell(reference, main_html, active="onboardings",
                      refresh="<meta http-equiv='refresh' content='3'>" if _open_run_for(reference) else "")


DETAIL_FOOTER_NOTE = ("Read-only view from Salesforce and local Leonardo Development evidence; nothing here writes to "
                      "Salesforce except the explicit confirmation forms.")
KEY_FACTS_MAX = 10
_SCAN_ATTENTION_STATES = ("scan_failed", "unrecognized")
RENEWAL_DOMAIN_GATE_CODES = frozenset({
    "renewal_new_domain_in_production", "renewal_target_not_in_production_clone",
    "renewal_target_ambiguous_in_production_clone", "production_clone_unavailable", "production_clone_incomplete"})
RENEWAL_CLI_BLOCK_TEXT = {
    # Historical only: no longer produced (owner 2026-10-07: an already-renewed expiry is kept); old stored outcomes still read.
    "renewal_expiration_would_shorten": "The new expiration would shorten the current licence — manual review; nothing was saved.",
    "renewal_domains_mismatch_manual_review": RENEWAL_RESULT_TEXT["renewal_domains_mismatch_manual_review"],
    "renewal_not_yet_applicable": RENEWAL_RESULT_TEXT["renewal_not_yet_applicable"] + ".",
    **{code: "Domain gate (Q3): the added domains did not pass the production duplicate gate; nothing was saved."
       for code in RENEWAL_DOMAIN_GATE_CODES},
}
RENEWAL_ALREADY_TEXT = "Already renewed: the Dev tenant already shows the new term; nothing to change."
_RENEWAL_OK_RESULTS = frozenset({"renewal_dry_run_planned", "renewal_edit_verified", "renewal_already_current"})


def _post_button(action: str, reference: str, label: str, cls: str = "ghost sm") -> str:
    return ("<form method='post' action='" + action + "'><input type='hidden' name='reference' value='" + escape(reference)
            + "'><button type='submit' class='" + cls + "'>" + escape(label) + "</button></form>")


def _step(label: str, done: bool, action: str = "") -> str:
    """One line of a to-do list (``label`` is trusted HTML); the action is an existing guarded form."""
    return ("<li" + (" class='done'" if done else "") + "><span class='chip " + ("chip-ok'>Done" if done else "chip-warn'>To do")
            + "</span><span class='what'>" + label + "</span>" + action + "</li>")


def _fold(title: str, summary: str, body: str, open_: bool = False) -> str:
    return ("<details class='more'" + (" open" if open_ else "") + "><summary><h2 class='sum-h'>" + escape(title) + "</h2>"
            "<span class='note'>" + escape(summary) + "</span></summary><div class='body'>" + body + "</div></details>")


def _validation_counts(result: object) -> tuple[int, int, int]:
    """(drift, unknown, warn) check counts of one stored validation result."""
    checks = result.get("checks") if isinstance(result, dict) else None
    if not isinstance(checks, list):
        return 0, 0, 0
    return tuple(sum(1 for c in checks if c.get("status") == status) for status in ("drift", "unknown", "warn"))  # type: ignore[return-value]


def _tenant_checks_need_attention(ctx: SimpleNamespace) -> bool:
    """A warn / blocked state inside "Tenant checks" opens the fold (a running scan is normal and does not)."""
    drift, unknown, warned = (0, 0, 0) if ctx.mirror else _validation_counts(ctx.validation)
    return bool(drift or unknown or warned
                or (ctx.scan and ctx.scan.get("state") in _SCAN_ATTENTION_STATES)
                or (ctx.spycloud and ctx.spycloud.get("state") != "on"))


def _pinned_banners(ctx: SimpleNamespace, case4_panel: str, commercial_readiness: dict[str, object] | None) -> str:
    """Banners that must never sit inside a fold; each appears only when it applies."""
    reference, parts, banners = ctx.reference, ctx.parts, []
    if not ctx.source_ready:
        if ctx.commercial_ready:
            banners.append("<div class='banner banner-warn'><b>Approval Status is " + escape(ctx.approval or "not populated")
                           + ", not Approved.</b> Nothing can start until Salesforce shows Approved.</div>")
        else:
            reason = " The Product, term, Main Domain, or comments need manual review." if commercial_readiness is not None else ""
            banners.append(
                "<section class='readiness source-blocked' aria-labelledby='readiness-title'><div class='readiness-heading'>"
                "<span class='readiness-icon' aria-hidden='true'>!</span><div><h2 id='readiness-title'>Source requirements need review</h2>"
                "<p>Salesforce onboarding approval is required before this CO is source ready." + escape(reason)
                + "</p></div></div></section>")
    banners.append(case4_panel)
    if ctx.sf_id_blocked:
        banners.append(_SALESFORCE_ID_PRESENT_SECTION)
    if parts is not None or ctx.sf_id_blocked:
        banners.append(_production_marker_for(reference))
    if parts is not None:
        if parts["state"] == "unavailable":
            banners.append(str(parts["unavailable"]))
        if parts["state"] in ("duplicate", "uncertain"):
            banners.append(str(parts["banner"]))
        record = parts.get("record")
        if (record is not None and not parts.get("same_revision") and parts["state"] not in ("onboarded", "running", "uncertain")):
            banners.append("<div class='banner banner-warn'><b>The CO changed in Salesforce since the last attended run</b> (result <code>"
                           + escape(record.get("result", "none recorded")) + "</code>). A new Start re-reads it; the scope is reviewed again.</div>")
    if ctx.domain_info is not None:
        domain_banners, script = _domains_banners(ctx.domain_info)
        banners.append(domain_banners + script)
    last = salesforce_id_writebacks().get(reference)
    if last is not None and last.get("result") == "write_uncertain":
        banners.append("<div class='banner banner-warn'><b>Salesforce ID write-back uncertain.</b> "
                       + escape(ID_WRITEBACK_RESULTS["write_uncertain"][1]) + ".</div>")
    drift = 0 if ctx.mirror else _validation_counts(ctx.validation)[0]
    if drift:
        banners.append("<div class='banner banner-bad'><b>Validation found " + str(drift) + " difference(s)</b> between the tenant and "
                       "the onboarding plan. See Tenant checks below.</div>")
    if ctx.scan and ctx.scan.get("state") in _SCAN_ATTENTION_STATES:
        _cls, title, message = SCAN_STATES[str(ctx.scan["state"])]
        banners.append("<div class='banner banner-bad'><b>" + escape(title) + ".</b> " + escape(message) + "</div>")
    if ctx.spycloud and ctx.spycloud.get("state") == "off":  # informational: ON is the default and shows nothing
        banners.append("<div class='banner banner-warn'><b>SpyCloud is OFF.</b> Leonardo's default is ON and the owner leaves it "
                       "ON; this is informational only, no action is required.</div>")
    if ctx.outcome and ctx.outcome["leonardo_write"] == "attempted_unverified":
        banners.append("<div class='banner banner-warn'><b>The last renewal save is unverified.</b> A write was attempted but "
                       "not confirmed by the read-back. Check the tenant in Leonardo Development before any retry.</div>")
    return "".join(banners)


def _renewal_blockers(ctx: SimpleNamespace) -> list[str]:
    """Plain-text blockers of a renewal CO from its DealHub plan (these keep the Start renewal form away)."""
    readable, plan = ctx.plan_data
    blockers: list[str] = []
    if not readable:
        blockers.append("The DealHub subscriptions could not be read; no plan can be built.")
    elif plan is None:
        blockers.append("No renewal plan could be built from DealHub.")
    else:
        blockers += [RENEWAL_BLOCKER_TEXT.get(code, code) for code in plan["blockers"]]
    return blockers


def _renewal_outcome_notes(ctx: SimpleNamespace) -> list[str]:
    """What the last renewal dry run / apply said when it stopped (informational; a Start re-checks everything)."""
    result = str(ctx.outcome["result"]) if ctx.outcome else ""
    if result in RENEWAL_CLI_BLOCK_TEXT:
        return [RENEWAL_CLI_BLOCK_TEXT[result]]
    if result == "renewal_already_current":
        return [RENEWAL_ALREADY_TEXT]
    if result and result not in _RENEWAL_OK_RESULTS:
        return ["The last renewal run stopped with " + result + "."]
    return []


def _renewal_term(ctx: SimpleNamespace) -> dict[str, Any] | None:
    plan = ctx.plan_data[1]
    return (plan["surface_term"] or plan["ce_term"]) if plan else None


def _renewal_old_expiry(ctx: SimpleNamespace) -> str:
    outcome = ctx.outcome
    if outcome and outcome.get("old_expiration"):
        return str(outcome["old_expiration"])
    tenant = ctx.stage_state.get("tenant")
    lic = tenant.get("license") if isinstance(tenant, dict) else None
    return _inventory_date(lic.get("expiration_date")) if isinstance(lic, dict) and lic.get("expiration_date") else "—"


def _renewal_gate_text(ctx: SimpleNamespace) -> str:
    outcome = ctx.outcome
    if outcome is None:
        return "Checked when the run starts"
    result = str(outcome["result"])
    if result in RENEWAL_DOMAIN_GATE_CODES:
        return "Blocked (" + result + ")"
    added = outcome.get("added_domains")
    if added:
        return str(added) + " added domain(s); gate passed"
    return "No added domains" if added == 0 else "Not recorded"


def _renewal_domains_text(ctx: SimpleNamespace) -> str:
    result = str(ctx.outcome["result"]) if ctx.outcome else ""
    if result == "renewal_domains_mismatch_manual_review":
        return "Mismatch — manual review"
    return "Not checked yet" if not ctx.outcome else "Matches the tenant"


RENEWAL_RUN_SUCCESS = frozenset({"renewal_edit_verified", "renewal_already_current"})
RENEWAL_CLI_HINT_TEXT = "Diagnostic CLI (not needed for a normal renewal): "


def renewal_supported(case: tuple[str, str, bool, bool] | None) -> bool:
    """Only Cases 4-6 renew from the dashboard; single-product renewals (Q10) stay out of scope."""
    return case is not None and case[0] in RENEWAL_ENGINES


def _renewal_plan_summary(ctx: SimpleNamespace) -> str:
    """The plan the operator attests to (static labels plus dates; no domain values)."""
    term = _renewal_term(ctx)
    old = _renewal_old_expiry(ctx)
    if ctx.mirror:
        mirror = "Exists (verified); it is renewed in place"
        old = "read from the tenant" if old == "—" else old
    else:
        mirror = "Will be created from the production clone (the production tenant is only read)"
        old = "read from the mirror"
    case5 = ctx.case is not None and ctx.case[0] == CASE5_ENGINE
    if case5:
        count = str(term["subdomains"]) if term and term.get("subdomains") is not None else "licensed subdomains"
        domains_item = ("Surface added", "profile applied, domains = subdomains (" + count + "); a tenant domain missing from "
                        "Salesforce stops the run (domains check)")
    else:
        domains_item = ("Number of domains", "Kept; if the Salesforce domains differ from the tenant's, the run stops (domains check)")
    items = [("Dev mirror", mirror),
             ("Expiry (Q1)", renewal_expiry_text(old, str(term["end"])) if term
              else old + " \u2192 \u2014 (DealHub term end)"),
             ("Apply from", renewal_apply_text(term)),
             ("Start date (Q2)", "Unchanged"),
             domains_item,
             ("Added subdomains (Q3)", "Each one passes the production duplicate gate before the save"),
             ("Environment", "Leonardo Development only; production BackOffice is never touched"),
             ("Salesforce", "Not changed; the Dev mirror is a separate tenant, so Salesforce IDs are not required to be empty")]
    return "<ul class='plan-summary'>" + "".join("<li><b>" + escape(k) + ":</b> " + escape(v) + "</li>" for k, v in items) + "</ul>"


def _renewal_start_form(ctx: SimpleNamespace) -> str:
    """Start renewal: POST, revision-bound hidden inputs, one-time nonce, two attestations (modelled on Start onboarding)."""
    reference, row = ctx.reference, ctx.row
    revision = row.get("LastModifiedDate")
    nonce = renewal_start_ack_nonce(reference, row)
    if not isinstance(revision, str) or not revision or nonce is None:
        return ""
    return ("<form class='start-form' method='post' action='/attended/start-renewal'>"
            "<input type='hidden' name='reference' value='" + escape(reference) + "'>"
            "<input type='hidden' name='source_revision' value='" + escape(revision) + "'>"
            "<input type='hidden' name='nonce' value='" + escape(nonce) + "'>"
            + _renewal_plan_summary(ctx)
            + "<div class='confirm'><label><input type='checkbox' name='plan_reviewed' value='1' required> "
            "I reviewed this renewal plan for this source revision</label>"
            "<label><input type='checkbox' name='attended_renewal_authorized' value='1' required> "
            "I authorize one Leonardo Development run for this source revision (it may create the Dev mirror and save the renewal)</label></div>"
            "<button type='submit'>Start renewal</button></form>")


def _renewal_do_now(ctx: SimpleNamespace) -> tuple[str, str, str]:
    """(headline, body, chip) of a renewal: a Start renewal form like Start onboarding, or the run's state."""
    reference, outcome, row = ctx.reference, ctx.outcome, ctx.row
    ref = escape(reference)
    result = str(outcome["result"]) if outcome else ""
    blockers = _renewal_blockers(ctx)
    term = _renewal_term(ctx)
    summary = [("Term", (str(term["start"]) + " \u2192 " + str(term["end"])) if term else "\u2014"),
               ("Expiry", renewal_expiry_text(_renewal_old_expiry(ctx), str(term["end"])) if term
                else _renewal_old_expiry(ctx) + " \u2192 \u2014"),
               ("Apply from", renewal_apply_text(term)),
               ("Blockers", str(len(blockers)) if blockers else "None"),
               ("Domain gate (Q3)", _renewal_gate_text(ctx))]
    summary_html = ("<dl class='kv sum5'>" + "".join("<div><dt>" + escape(k) + "</dt><dd>" + escape(v) + "</dd></div>"
                                                   for k, v in summary) + "</dl>")
    notes = "".join("<p class='note'>" + escape(item) + "</p>" for item in _renewal_outcome_notes(ctx))

    def done(headline: str, body: str, chip: str) -> tuple[str, str, str]:
        return headline, body + summary_html, chip

    if not renewal_supported(ctx.case):
        return ("No automated route for this renewal",
                "<p class='note'>Only Cases 4-6 (Surface + Credential Exposure renewals) run from the dashboard; "
                "single-product renewals are out of scope.</p>", "<span class='chip chip-neutral'>Manual</span>")
    if ctx.renewal_state_error:
        return ("Runner state unavailable",
                "<p class='note'>The local runner state could not be read. Nothing can start; inspect the state file.</p>",
                "<span class='chip chip-bad'>Blocked</span>")
    record = ctx.renewal_record
    if record is not None:
        same = record.get("source_revision") == row.get("LastModifiedDate")
        if not record.get("result"):
            note = _running_note(ref, record) if same else _START_IN_PROGRESS_NOTE + _progress_button(ref)
            return done("Renewal is running", note, "<span class='chip chip-warn'>Running</span>")
        if renewal_uncertain(record):
            return done("Verify the Dev tenant in Leonardo", _renewal_uncertain_banner(ref, record),
                        "<span class='chip chip-warn'>Uncertain</span>")
        if same:
            kind, message = runner_result_message(record["result"])
            banner = _outcome_banner(kind, message, record["result"], record.get("completed_on"), compact=True)
            if record["result"] in RENEWAL_RUN_SUCCESS:
                return done("Renewal is current in Leonardo Development",
                            banner + "<p class='note'>Salesforce updates stay manual; production writes need separate approval.</p>"
                            + _renewal_operator_check(),
                            "<span class='chip chip-ok'>Renewed (Dev)</span>")
            return done("The last renewal run stopped", banner + _reset_form(ref, record, "no Dev change is pending verification"),
                        "<span class='chip chip-bad'>Stopped</span>")
    if blockers:
        return done("Resolve the blockers, then start",
                    "<ul class='blockers'>" + "".join("<li><b>Blocked:</b> " + escape(item) + "</li>" for item in blockers)
                    + "</ul>" + notes, "<span class='chip chip-bad'>Blocked</span>")
    if outcome is not None and outcome["leonardo_write"] == "attempted_unverified":
        return done("Verify the Dev tenant in Leonardo before any retry",
                    "<p class='note'>A save was attempted but not confirmed. Check the tenant in Leonardo Development; "
                    "do not re-run blindly.</p>", "<span class='chip chip-warn'>Uncertain</span>")
    if outcome is not None and (outcome["leonardo_write"] == "verified" or result == "renewal_already_current"):
        return done("Renewal is current in Leonardo Development",
                    "<p class='note'>The last renewal run is recorded in Run history. Salesforce updates stay manual and "
                    "production writes need separate approval.</p>" + _renewal_operator_check(),
                    "<span class='chip chip-ok'>Renewed (Dev)</span>")
    old_note = ("" if record is None else "<p class='note'>Previous run for a different source revision: <code>"
                + escape(record.get("result", "no result recorded")) + "</code></p>")
    form = _renewal_start_form(ctx)
    return done("Start renewal",
                "<p class='lede'>Salesforce approval is validated. One run creates the Dev mirror if needed, plans the renewal, "
                "and applies it.</p>" + old_note + notes + form,
                "<span class='chip chip-info'>Ready</span>")


def _stage_steps(ctx: SimpleNamespace) -> list[str]:
    """Open to-do lines after the tenant exists, in order; every button is an existing guarded form."""
    reference, route, index, parts = ctx.reference, ctx.route, ctx.index, ctx.parts
    steps: list[str] = []
    validation = ctx.stage_state.get("validation")
    drift = _validation_counts(validation)[0]
    if not ctx.mirror and index <= 3:
        if not validation:
            steps.append(_step("Verify the tenant: compare it with the onboarding plan (read-only).", False,
                               _post_button("/attended/validate", reference, "Validate in Surface")))
        elif drift:
            steps.append(_step("Review the " + str(drift) + " difference(s) in Tenant checks below.", False))
    # No SpyCloud step (owner 2026-10-08): ON is Leonardo's default and stays; the optional check lives in its own card.
    open_items, done_items, error = (_reminder_items(reference, route, parts["record"], parts["evaluation"].core_plus_present)
                                     if parts is not None and "evaluation" in parts and route in SURFACE_ROUTES
                                     else ([], [], ""))
    by_kind = {kind: (text, action, button) for kind, text, action, button in open_items}
    # Order: scan settings off, then operator (once the scan finished), then Credential Exposure enabled.
    for kind in ("scan_settings_off", "operator_assigned", "ce_enabled"):
        if kind in by_kind and not (kind == "operator_assigned" and index < 3):
            text, action, button = by_kind[kind]
            steps.append(_step(escape(text), False, _post_button(action, reference, button)))
    if route == CE_ENGINE and not ctx.stage_state.get("operator"):
        steps.append(_step("Assign the Operator Account (TA/CSM from Salesforce) in Leonardo Development.", False))
    return steps


def _do_now_card(ctx: SimpleNamespace) -> str:
    """ONE card: the single next action and its blockers, by the stage proven from local evidence."""
    reference, parts, index = ctx.reference, ctx.parts, ctx.index

    label = ("" if ctx.route is None or ctx.is_renewal else
             {CE_ENGINE: "Credential Exposure onboarding", SURFACE_ENGINE: "Surface onboarding",
              CASE3_ENGINE: "Surface + Credential Exposure onboarding"}.get(ctx.route, ""))

    def card(headline: str, body: str, chip: str = "") -> str:
        return ("<section class='card nowcard' aria-labelledby='now-title'><div class='card-head'><h2 id='now-title' class='pill'>"
                "What to do now</h2>" + chip + "</div>"
                + ("<p class='meta-line'>" + escape(label) + "</p>" if label else "")
                + "<p class='next'><b>" + escape(headline) + "</b></p>" + body + "</section>")

    if not ctx.source_ready:
        if ctx.commercial_ready:
            suffix = " — manual review required" if ctx.commercial_manual_review else ""
            message = ("A comment already exists and will not be overwritten automatically. Review it before any approval decision."
                       if ctx.commercial_manual_review else
                       "A current-month or earlier Surface commercial term was found. Salesforce remains Pending until an operator "
                       "reviews and approves the proposed subscription dates.")
            note = ("<p class='note'><b>Source ready to onboard" + suffix + ".</b> Commercial source validation passed. "
                    + escape(message) + " This is not a Salesforce approval, a Leonardo authorization, or a write.</p>")
        else:
            note = "<p class='note'>Salesforce onboarding approval is required before this CO is source ready.</p>"
        return card("Waiting for approval in Salesforce", note, "<span class='chip chip-warn'>Awaiting approval</span>")
    if ctx.is_renewal:
        headline, body, chip = _renewal_do_now(ctx)
        return card(headline, body, chip)
    if index >= 5:
        return card("Done", "<p class='note'>Onboarding is completed in Salesforce.</p>", "<span class='chip chip-ok'>Done</span>")
    if index == 4:
        proof = ctx.stage_state.get("user_proof")
        manual = isinstance(proof, dict) and proof.get("proof") == "manual"
        body = ("<p class='note'>The operator account and the customer user exist. In production, update the Salesforce "
                "Onboarding Stage and the Salesforce IDs — see Salesforce record → Salesforce IDs below (opened). This page "
                "writes nothing to Salesforce.</p>")
        if manual:
            body += "<p class='note'>" + escape(_user_evidence_text(ctx.stage_state)) + "</p>" + _user_confirm_form(ctx.stage_state, reference)
        steps = _stage_steps(ctx)
        if steps:
            body += "<ul class='todo'>" + "".join(steps) + "</ul><p class='note'>" + REMINDER_NOTE + "</p>"
        return card("Update Salesforce stage and IDs (production only)", body, "<span class='chip chip-info'>Next</span>")
    if index >= 2:
        steps = _stage_steps(ctx)
        # Case 2 has no scan stage, so its user confirmation is offered once the tenant exists.
        confirm = ""
        if index >= 3 or ctx.route == CE_ENGINE:
            confirm = _step(escape(_user_evidence_text(ctx.stage_state)), False, _user_confirm_form(ctx.stage_state, reference))
            if "<form" not in confirm:
                confirm = ""
        validated = bool(ctx.stage_state.get("validation")) or ctx.mirror
        if index >= 3:
            headline = ("Confirm the customer user" if ctx.stage_state.get("operator")
                        else "Assign the operator, then confirm the customer user")
        elif not validated:
            headline = "Verify the tenant, then finish the scan settings"
        elif steps:
            headline = "Finish the follow-ups"
        else:
            headline = "Wait for the first scan to finish"
        body = ""
        if steps or confirm:
            body += "<ul class='todo'>" + "".join(steps) + confirm + "</ul><p class='note'>" + REMINDER_NOTE + "</p>"
        if index <= 2 and ctx.route != CE_ENGINE:
            body += ("<p class='note'>The scan is running or pending. Refresh it in Tenant checks; the tracker moves on when "
                     "Leonardo shows it finished.</p>")
        return card(headline, body, "<span class='chip chip-info'>Tenant created</span>")
    # The tenant does not exist yet (stage 0 or 1) and the CO is approved.
    if ctx.sf_id_blocked:
        return card("Blocked: this CO already has an ID in Salesforce",
                    "<p class='note'>See the alert above. Review the existing tenant manually; nothing was sent to Leonardo or "
                    "Salesforce.</p>", "<span class='chip chip-bad'>Blocked</span>")
    if parts is None:
        if ctx.is_case4:
            return card("Case 4 route is blocked", "<p class='note'>The Case 4 route is not mapped; see the alert above.</p>",
                        "<span class='chip chip-bad'>Blocked</span>")
        return card("No automated route for this CO",
                    "<p class='note'>Onboarding for this Product and Type is manual. Use Diagnostics → manual start / session "
                    "check.</p>", "<span class='chip chip-neutral'>Manual</span>")
    if parts["state"] == "unavailable":
        return card("Source could not be read", "<p class='note'>See the alert above. No browser was launched.</p>",
                    "<span class='chip chip-bad'>Blocked</span>")
    state = parts["state"]
    review = ("" if ctx.comment_ready else "<p class='note'><b>The onboarding-comment subscription period needs separate review "
                                          "before execution.</b></p>")
    if state == "uncertain":
        return card("Verify the tenant in Leonardo", "<p class='note'>The create may have succeeded; use the Verify button in the "
                    "alert above. Start and reset stay blocked until it settles.</p>", "<span class='chip chip-warn'>Uncertain</span>")
    if state == "running":
        return card("Onboarding is running", str(parts["runner_note"]), str(parts["chip"]))
    if state == "failed":
        return card("The last run failed", str(parts["runner_note"]) + str(parts["blockers"]) + str(parts["reset"]), str(parts["chip"]))
    if state == "duplicate":
        return card("Blocked: a duplicate tenant exists", "<p class='note'>See the alert above. Review the existing tenant; to "
                    "re-arm this run after the review use Diagnostics → Re-arm this run.</p>", str(parts["chip"]))
    if state == "onboarded":
        return card("Verify the tenant", "<p class='note'>A verified tenant exists; the next steps appear as the readback and "
                    "scan evidence arrive.</p>", str(parts["chip"]))
    if state == "blocked":
        return card("Resolve the blockers, then start", review + str(parts["runner_note"]) + str(parts["blockers"])
                    + str(parts.get("scope_fold", "")), str(parts["chip"]))
    return card("Start onboarding",
                "<p class='lede'>Salesforce approval is validated.</p>" + review + str(parts["runner_note"]) + str(parts["blockers"])
                + str(parts.get("scope_review", "")) + str(parts["start"]), str(parts["chip"]))


def _run_history_html(ctx: SimpleNamespace) -> str:
    """Outcomes, the licence dates entered, the renewal outcome and plan, the full scope, and finished follow-ups."""
    parts, html = ctx.parts, ""
    if parts is not None:
        record = parts.get("record")
        if parts["state"] == "onboarded":
            html += str(parts["runner_note"])
        elif record is not None:
            html += _entered_license_note(record)
        html += str(parts.get("scope_fold", ""))
        if parts.get("followups") and ctx.index >= 2:
            _open, done_items, _error = _reminder_items(ctx.reference, ctx.route, record, parts["evaluation"].core_plus_present)
            html += "".join("<p class='note'>" + escape(label) + " on " + escape(when) + ".</p>" for _kind, label, when in done_items)
    if ctx.is_renewal:
        html += _renewal_outcome_section(ctx.reference) + _renewal_plan_section(ctx.row, data=ctx.plan_data)
    return html


def _licence_fact(ctx: SimpleNamespace) -> str:
    record = ctx.parts.get("record") if ctx.parts is not None else None
    if record and record.get("license_start_entered") and record.get("license_end_entered"):
        return record["license_start_entered"] + " → " + record["license_end_entered"]
    tenant = ctx.stage_state.get("tenant")
    lic = tenant.get("license") if isinstance(tenant, dict) else None
    if isinstance(lic, dict) and (lic.get("start_date") or lic.get("expiration_date")):
        return _inventory_date(lic.get("start_date")) + " → " + _inventory_date(lic.get("expiration_date"))
    return "Not recorded"


def _validation_fact(ctx: SimpleNamespace) -> str:
    if ctx.mirror:
        return "Skipped (Dev mirror)"
    result = ctx.stage_state.get("validation")
    if not result:
        return "Not validated"
    drift, unknown, warned = _validation_counts(result)
    verdict = (str(drift) + " difference(s)" if drift else str(unknown) + " unconfirmed" if unknown
               else str(warned) + " warning(s)" if warned else "Everything matches")
    observed = result.get("observed_at")
    return verdict + (" · " + observed.strftime("%Y-%m-%d") if isinstance(observed, datetime) else "")


def _duplicate_fact(ctx: SimpleNamespace) -> str:
    try:
        check = load_check_state().get(ctx.reference)
    except (ValueError, OSError):
        check = None
    dev = "not run"
    if ctx.parts is not None and ctx.parts["state"] == "duplicate":
        dev = "duplicate found"
    if isinstance(check, dict) and check.get("kind") == "duplicate_check" and check.get("result"):
        dev = str(check["result"]).replace("_", " ")
    prod = "not checked"
    if isinstance(check, dict) and check.get("kind") == "production_duplicate" and check.get("result"):
        prod = str(check["result"]).replace("_", " ")
    return "DEV: " + dev + " · prod clone: " + prod


ID_LABELS = {"surface_account_id": "Account ID (Surface Account ID)", "account_uuid": "Account UUID"}


def _tenant_ids_block(ctx: SimpleNamespace) -> str:
    """The Leonardo Development IDs the route needs, FULL and copyable, from the local readback (never Salesforce).

    Credential Exposure: Account UUID. Surface: Account ID. Surface + CE (Case 3) and renewals (Cases 4-6): both. A value
    missing from the readback says "Not captured". Routes without a mapping show nothing.
    """
    mapping = SALESFORCE_ID_MAPPING.get(ctx.id_route or "")
    if not mapping:
        return ""
    readback = ctx.readback
    rows = ""
    for key, _field, _label in mapping:
        value = (readback or {}).get(key) if isinstance(readback, dict) else None
        shown = ("<code style='user-select:all'>" + escape(value) + "</code>" if isinstance(value, str) and value
                 else "<span class='note'>Not captured</span>")
        rows += "<div><dt>" + escape(ID_LABELS[key]) + "</dt><dd>" + shown + "</dd></div>"
    chips = "<span class='chip chip-info'>Leonardo Development</span>" + (" " + DEV_MIRROR_CHIP if ctx.mirror else "")
    return ("<section class='keyfacts idbar' aria-label='Leonardo Development IDs'><dl class='kv'>" + rows
            + "<div><dt>Environment</dt><dd>" + chips + "</dd></div></dl></section>")


def _key_facts(ctx: SimpleNamespace) -> list[tuple[str, str]]:
    """At most KEY_FACTS_MAX facts for this route and stage, from data the page already loaded."""
    row, index, route = ctx.row, ctx.index, ctx.route
    product, onboarding_type = row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")
    facts = [("Product", _PRODUCT_SHORT.get(product or "", product or "—") + " · " + _TYPE_SHORT.get(onboarding_type or "", onboarding_type or "—")),
             ("Salesforce", (ctx.approval or "Approval not populated") + " · " + (row.get("Onboarding_Stage__c") or "Stage not populated"))]
    if ctx.is_renewal:
        term = _renewal_term(ctx)
        has_id = bool((row.get("Surface_Account_ID__c") or "").strip() or (row.get("Account_UUID__c") or "").strip())
        outcome = ctx.outcome
        last = ("Not run yet" if outcome is None else str(outcome["result"]) + " · write " + str(outcome["leonardo_write"]).replace("_", " "))
        facts += [("Existing tenant", "ID in Salesforce (production)" if has_id else "None in Salesforce \u2014 found by name"),
                  ("Expiry", renewal_expiry_text(_renewal_old_expiry(ctx), str(term["end"])) if term
                   else _renewal_old_expiry(ctx) + " → —"),
                  ("Start date", "Unchanged (Q2)"),
                  ("Apply from", renewal_apply_text(term)),
                  ("Added domains · Q3 gate", _renewal_gate_text(ctx)),
                  ("Domains check", _renewal_domains_text(ctx)),
                  ("Last renewal run", last),
                  ("Dev mirror", "Yes" if ctx.mirror else "No")]
        return facts[:KEY_FACTS_MAX]
    if index <= 1:
        parts = ctx.parts
        evaluation = parts.get("evaluation") if parts is not None else None
        scope = getattr(evaluation, "scope", None) if evaluation is not None else None
        main = (ctx.domain_info["main"] if ctx.domain_info is not None else None) or row.get("Main_Domain__c") or "Not populated"
        facts.append(("Main domain", str(main)))
        if route in SURFACE_ROUTES:
            subdomains = _domain_count(str(ctx.domain_info["subdomains"] or "")).replace("—", "0") if ctx.domain_info is not None else "0"
            facts.append(("Alternate · sub", _domain_count(row.get("Alternate_Domains__c")).replace("—", "0") + " alternate · " + subdomains + " sub"))
            if scope:
                facts += [("Licence plan", str(scope["license_start"]) + " → " + str(scope["license_end"])),
                          ("Tier · interval", str(scope["tier"]).title() + " · " + str(scope["scanning_interval"]) + " · "
                           + str(scope["licensed_subdomains"]) + " licensed")]
            if route == CASE3_ENGINE:
                lc = (" · LC " + str(scope["leaked_credentials_interval"])) if scope and scope.get("leaked_credentials_interval") else ""
                facts.append(("Email domains", _domain_count(row.get("Email_Domains__c")) + lc))
            facts.append(("Duplicate check", _duplicate_fact(ctx)))
            if scope:
                flags = [("large scope" if scope.get("large_scope") else ""),
                         ("Core Plus" if scope.get("core_plus_present") else "")]
                facts.append(("Onboarding day", ("from " + str(scope["production_onboarding_day"])) if scope.get("production_onboarding_day") else "Now (DEV)"))
                facts.append(("Scope flags", ", ".join(flag for flag in flags if flag) or "None"))
        else:
            facts.append(("Email domains", _domain_count(row.get("Email_Domains__c"))))
            facts.append(("Duplicate check", _duplicate_fact(ctx)))
        return facts[:KEY_FACTS_MAX]
    # The tenant exists: licence, checks, scan, SpyCloud, operator, user.
    facts += [("Licence", _licence_fact(ctx)), ("Validation", _validation_fact(ctx))]
    if route != CE_ENGINE:
        scan = ctx.scan
        facts.append(("Scan", (SCAN_STATES[str(scan["state"])][1] + " · " + scan["observed_at"].strftime("%Y-%m-%d %H:%M"))
                      if scan else "Not read"))
    if route in (CE_ENGINE, CASE3_ENGINE):
        spy = ctx.spycloud
        facts.append(("SpyCloud", "Not checked" if spy is None else {"on": "ON", "off": "OFF"}.get(str(spy.get("state")), "Unknown") + " · "
                      + spy["observed_at"].strftime("%Y-%m-%d")))
    operator = ctx.stage_state.get("operator")
    evidence = ctx.stage_state.get("evidence")
    facts.append(("Operator", "Assigned" if operator else "Not assigned" if isinstance(evidence, dict) and evidence.get("operator") is False
                  else "Not confirmed"))
    proof = ctx.stage_state.get("user_proof")
    facts.append(("User created", ("Yes · " + str(proof["proof"])) if isinstance(proof, dict) and proof.get("created") else "No"))
    return facts[:KEY_FACTS_MAX]


def _key_facts_grid(facts: list[tuple[str, str]]) -> str:
    return ("<section class='keyfacts' aria-label='Key facts'><dl class='kv'>"
            + "".join("<div><dt>" + escape(label) + "</dt><dd>" + escape(value) + "</dd></div>" for label, value in facts[:KEY_FACTS_MAX])
            + "</dl></section>")


def render_dashboard(selected_queue: str = "", scan_started: bool = False, sort: str = "", direction: str = "") -> str:
    """Read the open queue (live), the local run records, and the cached
    closed-onboardings history for the history tile, then render the dashboard.

    A queue read failure propagates (ReadUnavailable → the connection page).
    A history or run-record failure only degrades its own part of the page.
    Filtered views only peek at the history cache and never read Salesforce
    for it.
    """
    rows = queue_rows()
    # Owner 2026-10-07: a CO already "onboarded on Dev" (verified evidence) leaves the queue; it lives on its own page.
    try:
        onboarded_dev = set(dev_onboarded_refs())
    except Exception:  # noqa: BLE001 - local evidence only; failing to read it never hides a CO
        onboarded_dev = set()
    rows = [row for row in rows if row.get("Name") not in onboarded_dev]
    try:
        runner_state: dict[str, dict[str, str]] | None = load_runner_state()
        runner_state_unavailable = False
    except RunnerStateUnavailable:
        runner_state, runner_state_unavailable = None, True
    history: ClosedHistory | None = None
    history_failed = False
    if selected_queue not in {key for key, _label, _helper in QUEUE_DEFINITIONS}:
        try:
            history = closed_history()
        except ReadUnavailable:
            history_failed = True
    else:
        history = cached_closed_history()
    start_dates: dict[str, str] | None = None
    start_dates_unavailable = False
    if rows:
        try:
            start_dates = queue_start_dates(tuple(row["Name"] or "" for row in rows))
        except ReadUnavailable:
            start_dates_unavailable = True
    try:
        check_state: dict[str, dict[str, str]] | None = load_check_state()
    except ValueError:
        check_state = None
    page = page_queue(rows, selected_queue, runner_state=runner_state, check_state=check_state,
                      runner_state_unavailable=runner_state_unavailable,
                      history=history, history_failed=history_failed, read_at=display_read_at(),
                      scan_started=scan_started, start_dates=start_dates,
                      start_dates_unavailable=start_dates_unavailable, sort=sort, direction=direction,
                      onboarded_dev=len(onboarded_dev))
    warm_co_pages(rows, runner_state)
    return page


WARM_CO_LIMIT = 12


def dealhub_needed(row: dict[str, str | None] | None) -> bool:
    """Whether a CO page uses DealHub rows (commercial check or renewal plan); unknown row: yes."""
    if row is None:
        return True
    product = row.get("Onboarding_Product__c")
    return product == "Surface & Credential Exposure" or renewal_case(product, row.get("Onboarding_Type__c")) is not None


def _display_entry_live(name: str, *args: Any) -> bool:
    """True when a display read is cached or in flight and not expired or failed."""
    with _display_cache_lock:
        entry = _display_cache.get((name, *args))
    return not (entry is None or entry[0] <= monotonic()
                or (entry[2].done() and entry[2].exception() is not None))


def warm_co_pages(rows: list[dict[str, str | None]], runner_state: dict[str, dict[str, str]] | None) -> int:
    """Schedule background display reads for the COs shown in the queue; never blocks.

    Most relevant first (Ready, Manual review, Needs validation, Follow-up), at
    most WARM_CO_LIMIT COs, skipping reads that are already cached or in flight.
    Salesforce display reads only: no Leonardo, browser, preflight, or write.
    Only runs while a display GET is rendering (the cache applies only then).
    Returns the number of reads scheduled.
    """
    if not _display_reads.get():
        return 0
    state = runner_state or {}
    ranked = sorted(((_QUEUE_RANK.get(classify_queue_row(row, state.get(row.get("Name") or ""))[0], 9), row.get("Name") or "", row)
                     for row in rows if REFERENCE.fullmatch(row.get("Name") or "")), key=lambda item: item[:2])
    scheduled = 0
    for _rank, reference, row in ranked[:WARM_CO_LIMIT]:
        for fn in (detail_row, dealhub_rows_for_co) if dealhub_needed(row) else (detail_row,):
            if not _display_entry_live(fn.__name__, reference):
                prefetch_display_read(fn, reference)
                scheduled += 1
    return scheduled


def page_salesforce_unavailable(failed: bool = True) -> str:
    """The Sessions page: status of each connection and the few actions that change it.

    ``failed`` marks the 503 path (a Salesforce read failed); opening the page
    from the navigation shows the same controls without claiming a failure.
    """
    if os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm":
        card = (
            "<section class='card'><div class='card-head'><h2 class='pill'>Salesforce runner</h2>"
            "<span class='chip chip-warn'>RUNNER REQUIRED</span></div>"
            "<p><strong>Manual Salesforce runner is unavailable.</strong> The VM dashboard cannot launch a browser or hold a "
            "Salesforce session. Queue and CO data come from the snapshot the desktop last published (see the banner above).</p>"
            "<p class='note'>Complete SSO/MFA only in the approved runner. This VM and the Workato OPA "
            "do not receive or store credentials, cookies, MFA codes, or CLI output.</p></section>"
        )
        main_html = "<a class='crumb' href='/'>&larr; All queues</a><div class='page-head'><h1>Salesforce connection</h1></div>" + card
        return _app_shell("Salesforce connection", main_html, active="connection")
    refresh_salesforce_after_login()
    statuses = session_statuses()
    busy = leonardo_worker_running()
    waiting = busy or any(status.state is SessionState.SIGNING_IN for status in statuses.values())
    failure = ("<div class='banner banner-warn'><span class='chip chip-warn'>Connection required</span> A Salesforce read "
               "failed, so no queue data is shown. Select <strong>Prepare sessions</strong> to sign in again.</div>"
               if failed else "")
    main_html = ("<a class='crumb' href='/'>&larr; All queues</a><div class='page-head'><h1>Sessions</h1>"
                 + _environment_chip() + "</div>" + failure + _sessions_card(statuses, waiting, busy))
    return _app_shell("Sessions", main_html, active="connection",
                      refresh="<meta http-equiv='refresh' content='5'>" if waiting else "")


SESSION_CHIPS = {
    SessionState.READY: ("chip-ok", "Ready"), SessionState.STALE: ("chip-neutral", "Re-check due"),
    SessionState.SIGNING_IN: ("chip-neutral", "Signing in…"), SessionState.NOT_SIGNED_IN: ("chip-warn", "Not signed in"),
    SessionState.EXPIRED: ("chip-warn", "Sign-in needed"), SessionState.BLOCKED: ("chip-warn", "Blocked"),
}


def _age_text(checked_at: datetime | None, now: datetime) -> str:
    if checked_at is None:
        return ""
    minutes = int(max((now - checked_at).total_seconds(), 0) // 60)
    return "just now" if minutes < 1 else f"{minutes} min ago"


def _environment_chip() -> str:
    """Leonardo Development is the only environment; production shows as locked (with why, on hover)."""
    problem = readiness.production_unlock_problem(os.environ, datetime.now(timezone.utc))
    locked = (" · <span title='" + escape(GATE_REFUSAL_TEXT.get(problem, problem)) + "'>BackOffice production locked</span>"
              if problem else "")
    return "<span class='chip chip-neutral'>Leonardo Development" + locked + "</span>"


def _sessions_card(statuses: dict[str, SessionStatus], waiting: bool, busy: bool = False) -> str:
    """One row per connection, then Prepare / Re-check; rare recovery actions stay folded away."""
    now = readiness_now()
    rows = ""
    for system, label in (("salesforce", "Salesforce"), ("leonardo", "Leonardo Development")):
        status = readiness.effective(statuses[system], now, readiness.READ_TTL)
        css, text = SESSION_CHIPS[status.state]
        detail = "" if status.state is SessionState.READY else escape(
            SESSION_REASON_TEXT.get(status.reason, status.reason.replace("_", " ")))
        rows += ("<tr><th>" + label + "</th><td><span class='chip " + css + "'>" + text + "</span></td><td>" + detail
                 + " <span class='note'>" + _age_text(status.checked_at, now) + "</span></td></tr>")
    preflight = preflight_snapshot()
    if preflight:
        problems = [PREFLIGHT_LABELS.get(key, key) + ": " + value for key, value in preflight["results"].items()
                    if value not in ("ok", "installed", "bundled")]
        chip = ("<span class='chip chip-warn'>Check</span>" if problems else "<span class='chip chip-ok'>Passed</span>")
        rows += ("<tr><th>Preflight</th><td>" + chip + "</td><td>" + escape("; ".join(problems))
                 + " <span class='note'>" + _age_text(preflight["checked_at"], now) + "</span></td></tr>")
    # Only a running Leonardo check disables the buttons; a Salesforce sign-in the
    # operator abandoned must not lock the page.
    disabled = " disabled" if busy else ""
    return (
        "<section class='card'><table class='sessions'>" + rows + "</table>"
        "<div class='actions'>"
        "<form method='post' action='/attended/prepare-sessions'><input type='hidden' name='environment' value='"
        + readiness.LEONARDO_DEVELOPMENT + "'><button type='submit'" + disabled + ">Prepare sessions</button></form>"
        "<form method='post' action='/attended/session-recheck'><button class='ghost' type='submit'" + disabled
        + ">Re-check</button></form></div>"
        "<p class='note'>Prepare signs in only where needed. Complete SSO/MFA on the real Salesforce and Leonardo pages; "
        "the dashboard never receives credentials, MFA codes, or cookies."
        + (" This page refreshes every 5 seconds while a sign-in or check runs." if waiting else "") + "</p>"
        "<details class='trouble'><summary>Troubleshooting</summary><div class='actions'>"
        "<form method='post' action='/attended/leonardo-dev-browser-close'><button class='ghost' type='submit'>"
        "Close automation browser</button></form>"
        "<form method='post' action='/attended/leonardo-dev-session-reset'><button class='ghost' type='submit'>"
        "Reset Leonardo session</button></form></div>"
        "<p class='note'>Close the automation browser at the end of the day; if this dashboard is open in that window, it "
        "closes too (start it again with the launcher). Reset also wipes its profile and forces a fresh Leonardo "
        "SSO/MFA.</p></details></section>"
    )


def _session_chip() -> str:
    """Sidebar chip: the worse of the two sessions, from memory only (no probe, no file read)."""
    statuses = session_statuses(include_runs=False)
    now = readiness_now()
    states = [readiness.effective(statuses[system], now, readiness.READ_TTL).state for system in ("salesforce", "leonardo")]
    order = [SessionState.BLOCKED, SessionState.EXPIRED, SessionState.NOT_SIGNED_IN, SessionState.SIGNING_IN,
             SessionState.STALE, SessionState.READY]
    worst = min(states, key=order.index)
    css, text = SESSION_CHIPS[worst]
    return "<a class='session-chip' href='/connection'><span class='chip " + css + "'>Sessions: " + text + "</span></a>"


def page_salesforce_login_opened() -> str:
    main_html = (
        "<a class='crumb' href='/connection'>&larr; Back to Sessions</a>"
        "<div class='page-head'><h1>Complete Salesforce sign-in</h1></div>"
        "<section class='card'><p>The Salesforce browser sign-in was opened. Complete SSO/MFA there.</p>"
        "<p class='note'>The dashboard does not receive or store browser credentials, cookies, or MFA codes.</p>"
        "<div class='actions'><form method='get' action='/'><button type='submit'>Return to queues</button></form></div>"
        "</section>"
    )
    return _app_shell("Salesforce sign-in opened", main_html, active="connection")


def page_comment_update_confirmation(evaluation: CommentUpdateEvaluation, nonce: str) -> str:
    return (
        "<!doctype html><title>Confirm CO-0741 comment update</title><style>body{max-width:760px;margin:48px auto;padding:0 22px;font:16px/1.5 system-ui,sans-serif;color:#181818}section{border:1px solid #0176d3;border-left:4px solid #0176d3;border-radius:4px;padding:18px;background:#fff}code{font-weight:700}button{padding:9px 13px;border:1px solid #0176d3;border-radius:4px;background:#0176d3;color:#fff;font:inherit;font-weight:600;cursor:pointer}.note{color:#514f4d;font-size:.9rem}</style>"
        "<main><h1>Confirm Onboarding Comments update</h1><section><p>CO-0741 passed the selected DealHub term validation.</p>"
        "<p>Proposed value: <code>" + escape(evaluation.proposed_comment) + "</code></p>"
        "<p class='note'>Confirming updates only three CO-0741 fields: Onboarding Comments, Stage to Request Approved, and Approval Status to Approved. It then immediately reads all three back. This expires in 10 minutes and is invalidated by a CO or selected subscription revision change.</p>"
        "<form method='post' action='/attended/confirm-comment-update'><input type='hidden' name='reference' value='CO-0741'><input type='hidden' name='nonce' value='" + escape(nonce) + "'><button type='submit'>Confirm update in Salesforce</button></form>"
        "<p><a href='/co/CO-0741'>Cancel and return to CO-0741</a></p></section></main>"
    )


def page_co0745_renewal_evaluation(evaluation: RenewalCommentEvaluation) -> str:
    """Show a term proposal while making the remaining renewal gate explicit."""
    return (
        "<!doctype html><title>CO-0745 renewal evaluation</title><style>body{max-width:760px;margin:48px auto;padding:0 22px;font:16px/1.5 system-ui,sans-serif;color:#181818}section{border:1px solid #2e844a;border-left:4px solid #2e844a;border-radius:4px;padding:18px;background:#fff}code{font-weight:700}.note{color:#514f4d;font-size:.9rem}</style>"
        "<main><h1>CO-0745 DealHub term validated</h1><section><p>One active Surface baseline subscription was selected for this CO revision.</p>"
        "<p>Product: <code>" + escape(evaluation.product_name) + "</code></p>"
        "<p>Proposed Onboarding Comments value: <code>" + escape(evaluation.proposed_comment) + "</code></p>"
        "<p>Required production tenant: <code>" + escape(evaluation.expected_tenant_name) + "</code></p>"
        "<p class='note'>No Salesforce field was changed. Production validation must find exactly this tenant name; a base-name, domain-only, missing, or duplicate result requires manual review. A separate final approval is required before an update can be prepared.</p>"
        "<p><a href='/co/CO-0745'>Return to CO-0745</a></p></section></main>"
    )


def _ce_only_start_form(evaluation: CredentialExposureFillPreflight) -> str:
    """Render the one-time, revision-bound Start Onboarding form when eligible."""
    if not evaluation.eligible_for_fill_review:
        return ""
    return ("<form class='start-form' method='post' action='/attended/start-ce-only-runner'>"
            "<input type='hidden' name='reference' value='" + escape(evaluation.reference) + "'>"
            "<input type='hidden' name='source_revision' value='" + escape(evaluation.source_revision) + "'>"
            "<div class='confirm'><label><input type='checkbox' name='attended_create_authorized' value='1' required> "
            "I authorize one Leonardo Development run for this source revision</label></div>"
            "<button type='submit'>Start Onboarding</button></form>" + _START_DUPLICATE_NOTE
            + _duplicate_precheck_row(evaluation.reference))


def page_ce_only_fill_preflight(evaluation: CredentialExposureFillPreflight,
                                runner_state: dict[str, dict[str, str]] | None = None) -> str:
    blockers = ("<li>" + "</li><li>".join(escape(item.replace("_", " ")) for item in evaluation.blockers) + "</li>"
                if evaluation.blockers else "<li>None from the local source check.</li>")
    state = "ready for attended duplicate review" if evaluation.eligible_for_fill_review else "blocked for manual review"
    record = runner_state.get(evaluation.reference) if runner_state is not None else None
    if record is not None and record.get("source_revision") == evaluation.source_revision:
        if record.get("result"):
            kind, message = RUNNER_RESULT_MESSAGES.get(record["result"], ("blocked", "Result code <code>" + escape(record["result"]) + "</code>."))
            color = {"success": "#2e844a", "blocked": "#c0392b", "info": "#8a6d1a"}.get(kind, "#c0392b")
            completed = " (completed " + escape(record["completed_on"]) + ")" if record.get("completed_on") else ""
            runner_note = ("<div style='border-left:4px solid " + color + ";padding:10px 14px;background:#fff;border-radius:4px;margin:10px 0'>"
                           "<strong>" + escape(kind.upper()) + ".</strong> " + message +
                           " <span class='note'>Result code: <code>" + escape(record["result"]) + "</code>" + completed + "</span></div>")
            start_action = ""
        else:
            started = " at " + escape(record["started_on"]) if record.get("started_on") else ""
            runner_note = ("<p>An attended run for this source revision was started" + started +
                            " and has not reported a result. Do not start another.</p>")
            start_action = ""
    elif start_blocker(record) is not None:
        runner_note = ("<p>An earlier run is still in progress or already created a tenant (<code>"
                       + escape(start_blocker(record) or "") + "</code>). No new run can start.</p>")
        start_action = ""
    elif record is not None:
        runner_note = ("<p>Previous attended run for a different source revision: <code>" +
                        escape(record.get("result", "no result recorded")) + "</code></p>")
        start_action = _ce_only_start_form(evaluation)
    else:
        runner_note = ""
        start_action = _ce_only_start_form(evaluation)
    note = ("<p class='note'>No browser, Leonardo, duplicate lookup, form fill, tenant creation, or Salesforce update was performed.</p>"
            if record is None else
            "<p class='note'>The attended runner never clicks Confirm and never updates Salesforce. The result above is local evidence only.</p>")
    reference = escape(evaluation.reference)
    return (
        "<!doctype html><title>" + reference + " fill preflight</title><style>body{max-width:760px;margin:48px auto;padding:0 22px;font:16px/1.5 system-ui,sans-serif;color:#181818}section{border:1px solid #0176d3;border-left:4px solid #0176d3;border-radius:4px;padding:18px;background:#fff}code{font-weight:700}.note{color:#514f4d;font-size:.9rem}</style>"
        "<main><h1>" + reference + " Credential Exposure fill preflight</h1><section><p>Local state: <code>" + state + "</code></p>"
        "<p>Source revision: <code>" + escape(evaluation.source_revision) + "</code></p>"
        "<p>Email Domains configured: <code>" + str(evaluation.email_domain_count) + "</code></p>"
        "<p>Blockers:</p><ul>" + blockers + "</ul>" + runner_note + note + start_action + "<p><a href='/co/" + reference + "'>Return to " + reference + "</a></p></section></main>"
    )


# Run results that mean "a tenant like this may already exist" (live check or inventory pre-check).
DUPLICATE_RESULTS = frozenset({"duplicate_found", "duplicate_ambiguous", "duplicate_inventory_match",
                               "duplicate_production_match"})
RUNNER_RESULT_MESSAGES: dict[str, tuple[str, str]] = {
    "duplicate_production_match": ("blocked", "The production tenant list already has a tenant with this tenant name, primary domain, or one of its alternate domains. No browser was opened and nothing was created in Leonardo. Review the production tenant; this CO should not be onboarded again."),
    "production_clone_unavailable": ("blocked", "The production tenant list could not be used (missing, stale, or incomplete), so the production duplicate check could not answer. No browser was opened and nothing was created. Refresh it with tools\\redash_inventory_collector.py --collect, then try again."),
    "duplicate_inventory_match": ("blocked", "The latest DEV tenant inventory already has a tenant with this tenant name, primary domain, or one of its alternate domains. No browser was opened and nothing was created. Review that tenant; if it was removed, refresh the inventory and try again."),
    "readback_verified": ("success", "Tenant created and read back. Surface Account ID, Account UUID, and Account Scanning state were captured locally."),
    "duplicate_found": ("blocked", "A tenant with this tenant name or primary domain already exists in Leonardo Development. Nothing was created. Review the existing tenant; this CO should not be onboarded again."),
    "duplicate_ambiguous": ("blocked", "A tenant with a similar name exists, or the search returned more results than could be checked. Nothing was created. Review it in Leonardo Development before retrying."),
    "duplicate_search_schema_unavailable": ("blocked", "The Tenant Management search control could not be found (page-layout/selector issue). No tenant was created."),
    "duplicate_schema_unavailable": ("blocked", "The tenant table could not be classified (unexpected row layout). No tenant was created. Diagnostics were captured; retry after review."),
    "readback_tenant_ambiguous": ("blocked", "The tenant could not be identified uniquely (the name, or name and primary domain, matched zero or several tenants). Nothing was recorded. Check it in Leonardo Development."),
    "readback_id_conflict": ("blocked", "Leonardo returned different IDs than the ones already captured for this CO. The captured IDs were kept. Check which tenant is correct in Leonardo Development."),
    "runner_crashed": ("blocked", "The runner stopped on an unexpected error; the run log has the details. Check Leonardo Development before any retry."),
    "runner_launch_failed": ("blocked", "The desktop runner could not be launched. Nothing was started in Leonardo."),
    "case3_route_mismatch": ("blocked", "This CO is not a new Surface &amp; Credential Exposure onboarding. Nothing was started."),
    "case3_ce_email_domain_invalid": ("blocked", "Case 3 needs exactly one valid CE email domain in Email Domains. Correct it in Salesforce."),
    "case3_core_plus_missing": ("blocked", "No Core Plus (Credential Exposure) subscription was found on the account. Nothing was started."),
    "case3_term_mismatch": ("blocked", "The Surface and Core Plus (Credential Exposure) licence terms differ, so one tenant cannot carry both. Manual review required; nothing was created."),
    "case3_source_unavailable": ("blocked", "The Case 3 source could not be read. Nothing was started."),
    "salesforce_id_already_present": ("blocked", "The Salesforce ID field for this route is already set, so this CO was probably onboarded in production. Nothing was created; review the existing tenant manually."),
    "source_revision_drift": ("blocked", "The Salesforce source changed during the run. No tenant was created. Re-run the fill preflight."),
    "development_login_timeout": ("blocked", "Tenant Management was not reached within the wait window. No tenant was created. Re-run and complete SSO/MFA."),
    "ce_route_mismatch": ("blocked", "This CO is not a new Credential Exposure onboarding (Onboarding Product and Type), so the CE-only runner stopped before opening the browser. Nothing was created."),
    "leonardo_session_expired": ("blocked", "The Leonardo Development session has expired (the server answered 401). Nothing was filled or created. On the Salesforce connection page click Re-establish Leonardo session, complete SSO/MFA, then reset this run and retry."),
    "operator_review_timeout_no_create": ("blocked", "The review window closed before Confirm. No tenant was created."),
    "operator_cancelled_no_create": ("info", "The Add Account form was closed without Confirm. No tenant was created."),
    "fill_form_schema_unavailable": ("blocked", "An Add Account form field could not be filled or was refused. No tenant was created. The run log names the field."),
    "ce_license_dates_unavailable": ("blocked", "The CE license expiration (from the Salesforce subscription) is not after today, so no valid license can start today. No browser was opened and no tenant was created."),
    "add_account_schema_unavailable": ("blocked", "The Add Account button could not be found. No tenant was created."),
    "readback_schema_unavailable": ("blocked", "The created tenant could not be read back. Verify manually in Leonardo Development."),
    "readback_value_mismatch": ("blocked", "The read-back values did not match the expected patterns. Verify manually in Leonardo Development."),
    "readback_write_unavailable": ("blocked", "The read-back evidence could not be written to the local file."),
    "playwright_runtime_unavailable": ("blocked", "The Playwright runtime was unavailable."),
    "salesforce_fill_source_unavailable": ("blocked", "The Salesforce source could not be read."),
    "attended_ce_runner_unavailable": ("blocked", "The runner encountered an unexpected error."),
    "leonardo_profile_unavailable": ("blocked", "The dedicated automation profile could not be created. Free the path or reset the session."),
    "browser_tab_unavailable": ("blocked", "The automation browser did not open a new tab. No tenant was created or changed. Close the automation browser and retry."),
    "browser_cdp_unavailable": ("blocked", "The isolated automation browser did not start a debug endpoint. Close stray automation Chrome windows and retry."),
    "login_page_schema_unavailable": ("blocked", "A live tenant-management page could not be located after login. Retry the attended run."),
    "confirm_button_schema_unavailable": ("blocked", "The Add Account Confirm control could not be located (page-layout/selector issue). No tenant was created."),
    "confirm_button_not_enabled": ("blocked", "The Confirm control could not be clicked (disabled or not ready). No tenant was created; do not retry blindly."),
    "confirm_no_create": ("blocked", "The form was submitted but the created tenant could not be confirmed. No retry was performed. Verify manually in Leonardo Development."),
    "ce_subscription_unavailable": ("blocked", "No Pentera Core Plus subscription (Commercial or Enterprise) was found for this account. No tenant was created. Verify the DealHub subscription."),
    "ce_subscription_ambiguous": ("blocked", "The subscription dates are missing or conflicting. No tenant was created. Verify the DealHub subscription."),
    "fill_value_mismatch": ("blocked", "A form control did not keep the value the runner set. No tenant was created. Diagnostics were captured; review before retrying."),
    "readback_only_verified": ("success", "The existing tenant was verified read-only. Surface Account ID, Account UUID, and observed Account Scanning state were captured locally. No tenant was created or changed."),
    "readback_only_tenant_not_found": ("blocked", "No tenant matched the expected tenant name. No tenant was created or changed. Verify manually in Leonardo Development."),
    "readback_only_state_unrecognized": ("blocked", "The observed Account Scanning state is not recognized. No tenant was created or changed. Verify manually in Leonardo Development."),
    "invalid_co_reference": ("blocked", "The customer onboarding reference is invalid. No action was performed."),
    # Surface-only route (Case 1).
    "route_unsupported": ("blocked", "The runner was started with an unsupported route. No browser was opened and nothing was created."),
    "surface_route_mismatch": ("blocked", "This CO is not exactly a new Surface onboarding (Onboarding Product \"Surface\", Type \"New Product Onboarding\"), so the Surface runner stopped before opening the browser. Nothing was created."),
    "surface_source_unavailable": ("blocked", "The Surface source (CO fields or DealHub subscriptions) could not be read or is incomplete (account, country). Nothing was created."),
    "surface_product_unrecognized": ("blocked", "A DealHub product row is missing a name or is an unrecognized Pentera Surface product. Nothing was created. Review the subscriptions manually."),
    "surface_baseline_unavailable": ("blocked", "No counted Surface baseline subscription was found (Active, or Pending starting within 14 days). Nothing was created."),
    "surface_baseline_ambiguous": ("blocked", "More than one counted Surface baseline subscription was found. Nothing was created. Review the subscriptions manually."),
    "surface_tier_unknown": ("blocked", "The Surface baseline product has no approved tier (Prime, Go, Enterprise, Essentials, Professional), so no scanning interval can be chosen. Nothing was created."),
    "surface_subscription_invalid": ("blocked", "A Surface subscription row has no status or invalid dates. Nothing was created. Verify the DealHub subscription."),
    "surface_main_domain_invalid": ("blocked", "The Main Domain must be one valid registrable root domain (not a subdomain, public suffix, or network). Nothing was created."),
    "surface_domains_invalid": ("blocked", "An Alternate Domains entry is malformed or a wildcard. Nothing was created. Correct the Salesforce value."),
    "surface_domains_exceed_license": ("blocked", "The CO lists more domains (main + alternate) than the licensed subdomains allow. Nothing was created. Check the Salesforce domains and the DealHub subdomain add-ons."),
    "surface_networks_not_supported": ("blocked", "Alternate Domains contains a network or IP address; networks are not supported by this route yet. Nothing was created."),
    "surface_license_dates_unavailable": ("blocked", "The Surface license expiration (from the baseline subscription) is not after today, so no valid license can start today. Nothing was created."),
    "max_scan_duration_schema_unavailable": ("blocked", "The Advanced options \"Maximum scan Duration (hours)\" control could not be found. Nothing was created. The run log records the lookup."),
    "scope_review_missing": ("blocked", "The scope review acknowledgement for this source revision is missing. No browser was launched."),
}

LEONARDO_SESSION_MESSAGES: dict[str, tuple[str, str]] = {
    "leonardo_session_active": ("success", "The Leonardo Development automation session is open and valid. The next attended run will skip SSO/MFA."),
    "leonardo_session_expired": ("blocked", "The Leonardo Development session is expired. Complete SSO/MFA on the next attended run, or reset the session."),
    "leonardo_session_unavailable": ("blocked", "No Leonardo Development page was reachable. Check the RND VPN and network, then retry."),
    "leonardo_session_bootstrapped": ("success", "The Leonardo Development automation session is established. The next attended run will skip SSO/MFA."),
    "browser_cdp_unavailable": ("blocked", "The isolated automation browser did not start a debug endpoint. Close stray automation Chrome windows and retry."),
    "leonardo_profile_unavailable": ("blocked", "The dedicated automation profile could not be created. Free the path or reset the session."),
    "playwright_runtime_unavailable": ("blocked", "The Playwright runtime is unavailable on this host."),
    "leonardo_profile_reset": ("info", "The dedicated Leonardo Development automation profile was wiped. The next attended run will require a fresh SSO/MFA."),
    "leonardo_profile_reset_unavailable": ("blocked", "The automation profile could not be wiped. Close stray automation Chrome windows and retry."),
    "browser_tab_unavailable": ("blocked", "The automation browser did not open a new tab. Close the automation browser and retry."),
    "automation_browser_closed": ("info", "The automation browser was closed. The persisted session is kept; the next attended run reopens the window."),
    "automation_browser_not_running": ("info", "No automation browser is running. Nothing was closed."),
    "automation_browser_close_unavailable": ("blocked", "The automation browser could not be closed. Close the automation Chrome window manually."),
}


RENEWAL_RUN_MESSAGES: dict[str, tuple[str, str]] = {
    "renewal_edit_verified": ("success", "The renewal was saved in Leonardo Development and read back: the new expiration and counts are on the Dev tenant. Salesforce was not changed."),
    "renewal_already_current": ("success", "The Dev tenant already shows the new term, so nothing needed to change. Salesforce was not changed."),
    "renewal_dry_run_planned": ("info", "The dry run planned the change but it was not applied."),
    "renewal_domains_mismatch_manual_review": ("blocked", "The Salesforce domains differ from the domains on the tenant. Manual review is required; nothing was saved."),
    "renewal_expiration_would_shorten": ("blocked", "The new expiration would be earlier than the tenant's current one (it may already be renewed). Manual review; nothing was saved."),
    **{code: ("blocked", text) for code, text in RENEWAL_CLI_BLOCK_TEXT.items()
       if code not in ("renewal_expiration_would_shorten", "renewal_domains_mismatch_manual_review",
                       "renewal_not_yet_applicable")},
    "renewal_not_yet_applicable": ("blocked", "A production renewal can be applied only from 14 days before the new term starts. Nothing was saved or opened."),
    "renewal_not_approved": ("blocked", "Onboarding Approval Status is not Approved in Salesforce. Nothing was started."),
    "renewal_route_mismatch": ("blocked", "This CO is not one of the renewal cases. Nothing was started."),
    "renewal_route_not_supported": ("blocked", "Single-product renewals are out of scope (only Cases 4-6). Nothing was started."),
    "renewal_ce_email_domain_invalid": ("blocked", "A renewal needs exactly one valid Credential Exposure email domain in Email Domains. Correct it in Salesforce."),
    "renewal_source_unavailable": ("blocked", "The renewal source (Salesforce / DealHub) could not be read. Nothing was started."),
    "renewal_not_onboarded": ("blocked", "No Dev tenant (mirror) is recorded for this CO, so there is nothing to renew. Nothing was changed."),
    "renewal_environment_not_supported": ("blocked", "Renewals run in Leonardo Development only. Nothing was started."),
    "renewal_form_mismatch": ("blocked", "The tenant's Edit form did not match what was expected. Nothing was saved."),
    "renewal_form_unreadable": ("blocked", "The tenant's Edit form could not be read. Nothing was saved."),
    "renewal_edit_unavailable": ("blocked", "The tenant's Edit action could not be opened. Nothing was saved."),
    "renewal_row_schema_unexpected": ("blocked", "The tenant row had an unexpected layout. Nothing was saved."),
    "renewal_start_date_changed": ("blocked", "The tenant's start date would change, which is never allowed. Nothing was saved."),
    "renewal_confirm_unavailable": ("blocked", "The Confirm control could not be found. Nothing was saved."),
    "renewal_confirm_disabled": ("blocked", "Confirm stayed disabled, so nothing was saved."),
    "renewal_cancel_unavailable": ("blocked", "The Edit form could not be closed cleanly. Check the tenant in Leonardo Development."),
    "renewal_save_no_signal": ("info", "Confirm was clicked but Leonardo gave no answer. The save may or may not have happened: verify in Leonardo."),
    "renewal_save_id_mismatch": ("info", "Confirm was clicked and an edit of a different tenant was observed. Verify in Leonardo before any retry."),
    "renewal_save_failed": ("info", "Leonardo answered the save with an error. Verify in Leonardo before any retry."),
    "renewal_saved_unverified": ("info", "The save was accepted but the read-back could not confirm it. Verify in Leonardo."),
    "renewal_readback_changed_mismatch": ("info", "The save was accepted but the tenant does not show the planned values. Verify in Leonardo."),
    "renewal_readback_preserved_mismatch": ("info", "The save was accepted but a value that must not change differs. Verify in Leonardo."),
    "source_revision_drift": ("blocked", "The Salesforce source changed during the run. Nothing further was done. Review the CO and start again."),
    "mirror_already_exists": ("blocked", "A Dev mirror was already created, or its create was attempted earlier and is not verified. Check Leonardo Development."),
    "mirror_readback_exists": ("blocked", "A Dev tenant is already recorded for this CO but it is not a verified mirror. Nothing was created."),
    "mirror_not_renewal": ("blocked", "This CO is not a renewal. No mirror was created."),
    "mirror_environment_not_supported": ("blocked", "Mirrors are created in Leonardo Development only."),
    "mirror_clone_unavailable": ("blocked", "The production tenant list could not be used (missing, stale, or incomplete), so the Dev mirror could not be planned. Refresh it and start again."),
    "mirror_co_identity_unavailable": ("blocked", "The CO's account identity could not be read for the Dev mirror. Nothing was created."),
    "mirror_prod_tenant_not_found": ("blocked", "No live, paid production tenant with this exact name was found in the production clone. No mirror was created."),
    "mirror_prod_tenant_ambiguous": ("blocked", "More than one production tenant matched, so the Dev mirror could not be planned. No mirror was created."),
    "mirror_prod_license_incomplete": ("blocked", "The production tenant's licence data is incomplete. No mirror was created."),
    "mirror_license_type_unmapped": ("blocked", "The production licence type has no Dev equivalent yet. No mirror was created."),
    "mirror_interval_unmapped": ("blocked", "The production scanning interval has no Dev equivalent yet. No mirror was created."),
    "mirror_lc_domains_unavailable": ("blocked", "The Leaked Credentials domains for the mirror could not be determined. No mirror was created."),
    "mirror_duplicate_inventory_match": ("blocked", "The Dev tenant inventory already has a tenant that matches the mirror. No mirror was created."),
    "mirror_duplicate_found": ("blocked", "Leonardo Development already has a tenant that matches the mirror. No mirror was created."),
    "mirror_duplicate_ambiguous": ("blocked", "A similar tenant exists in Leonardo Development. No mirror was created; review it."),
    "mirror_duplicate_schema_unavailable": ("blocked", "The tenant table could not be classified for the mirror duplicate check. No mirror was created."),
    "mirror_confirm_no_create": ("info", "Confirm was clicked for the Dev mirror but no create was observed. A tenant may exist: verify in Leonardo."),
    "mirror_create_unverified": ("info", "The Dev mirror may have been created but could not be read back. Verify in Leonardo."),
    "mirror_readback_value_mismatch": ("info", "The Dev mirror was created but its read-back values were unexpected. Verify in Leonardo."),
    "mirror_readback_write_unavailable": ("info", "The Dev mirror was created but its ids could not be recorded locally. Verify in Leonardo."),
    "mirror_readback_id_conflict": ("info", "The Dev mirror's ids differ from ids already recorded for this CO. Verify in Leonardo."),
    "mirror_record_write_unavailable": ("info", "The Dev mirror was created but its local record could not be written. Verify in Leonardo."),
    "mirror_record_write_unavailable_before_create": ("blocked", "The local mirror record could not be written, so no mirror was created."),
    "mirror_search_unavailable": ("info", "The Dev mirror may have been created but the tenant search was unavailable. Verify in Leonardo."),
}
RENEWAL_RUN_HEADLINES = {
    "renewal_edit_verified": "Renewal applied (Dev)", "renewal_already_current": "Already renewed (Dev)",
    "renewal_domains_mismatch_manual_review": "Domains differ - manual review",
    "renewal_expiration_would_shorten": "Would shorten the licence - manual review",
    "renewal_not_yet_applicable": "Not yet applicable (production apply window)",
}


def runner_result_message(result: str) -> tuple[str, str]:
    """(kind, trusted HTML message) of any runner result, including renewal and mirror codes."""
    if result in RUNNER_RESULT_MESSAGES:
        return RUNNER_RESULT_MESSAGES[result]
    if result in RENEWAL_RUN_MESSAGES:
        return RENEWAL_RUN_MESSAGES[result]
    if result.startswith("renewal_blocked_") and result[len("renewal_blocked_"):] in RENEWAL_BLOCKER_TEXT:
        return "blocked", escape(RENEWAL_BLOCKER_TEXT[result[len("renewal_blocked_"):]])
    return "blocked", "The runner finished with result code <code>" + escape(result) + "</code>."


def page_leonardo_session_result(result: str) -> str:
    """Render the read-only Leonardo Development session check/reset outcome."""
    kind, message = LEONARDO_SESSION_MESSAGES.get(result, ("blocked", "Result code <code>" + escape(result) + "</code>."))
    border = {"success": "#2e844a", "blocked": "#c0392b", "info": "#8a6d1a"}.get(kind, "#c0392b")
    return (
        "<!doctype html><title>Leonardo Development session</title>"
        "<body style='max-width:760px;margin:48px auto;padding:0 22px;font:16px/1.5 system-ui,sans-serif;color:#181818'>"
        "<h1 style='font-size:24px'>Leonardo Development session</h1>"
        "<div style='border-left:4px solid " + border + ";padding:12px 16px;background:#fff;border-radius:4px;margin:16px 0'>"
        "<strong>" + escape(kind.upper()) + ".</strong> " + message + "</div>"
        "<p>Result code: <code>" + escape(result) + "</code></p>"
        "<p><a href='/connection'>Back to Sessions</a> · <a href='/'>Return to queues</a></p>"
        "</body></html>"
    )


RUNNER_REDIRECT_SECONDS = 6
# kind -> (icon glyph, default headline, css modifier)
OUTCOME_STYLES: dict[str, tuple[str, str, str]] = {
    "success": ("&#10003;", "Onboarded successfully", "success"),
    "blocked": ("&#10007;", "Onboarding failed", "failed"),
    "info": ("i", "Not onboarded", "info"),
}
# Runner results that deserve a more specific headline than "Onboarding failed".
RUNNER_HEADLINES = {
    "duplicate_found": "Already exists — duplicate",
    "duplicate_ambiguous": "Possible duplicate — review",
    "duplicate_inventory_match": "Already in the tenant inventory — duplicate",
    "duplicate_production_match": "Already in production — duplicate",
    "production_clone_unavailable": "Production check unavailable",
    "leonardo_session_expired": "Leonardo session expired",
}

# Design tokens matched to the Pentera platform UI (navy sidebar, light-grey
# canvas, white rounded cards, blue primary action, pastel status chips).
PENTERA_CSS = (
    ":root{--nav:#0f1626;--nav-active:#283043;--canvas:#f4f5f7;--card:#fff;--line:#e5e7eb;--text:#2e3440;"
    "--muted:#6b7280;--heading:#4b5563;--primary:#1f7ae0;--primary-dark:#1664c0;"
    "--ok-bg:#d9f7be;--ok:#237804;--bad-bg:#ffd6d6;--bad:#b42318;--warn-bg:#fff1b8;--warn:#9a5b00;"
    "--info-bg:#dbe7ff;--info:#1d4ed8;--pill:#f0f1f3}"
    "*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--text);"
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    ".shell{display:flex;min-height:100vh}"
    ".side{flex:0 0 190px;background:var(--nav);color:#fff;padding:22px 14px;display:flex;flex-direction:column;gap:6px}"
    ".brand{font:800 26px/1 'Arial Black','Segoe UI Black',sans-serif;letter-spacing:.5px;background:#fff;color:var(--nav);"
    "padding:4px 6px;display:inline-block;margin:0 0 4px 4px;align-self:flex-start}"
    ".brand-sub{color:#aeb6c4;font-size:12px;margin:0 0 22px 6px}"
    ".side a{color:#e5e9f0;text-decoration:none;padding:10px 10px;border-radius:6px;"
    "font:600 15px/1.2 'Barlow Condensed','Oswald','Arial Narrow','Segoe UI',sans-serif;font-stretch:condensed}"
    ".side a:hover,.side a.active{background:var(--nav-active);color:#fff}"
    ".side-foot{margin-top:auto;color:#8b94a5;font-size:11px;padding:0 8px}"
    "main{flex:1;min-width:0;max-width:1040px;padding:28px 36px 48px}"
    "a{color:var(--primary-dark);text-decoration:none;font-weight:600}a:hover{text-decoration:underline}"
    "a:focus-visible,button:focus-visible,summary:focus-visible,input:focus-visible{outline:2px solid var(--primary);"
    "outline-offset:2px;border-radius:6px}.side a:focus-visible{outline-color:#fff}"
    ".crumb{font-size:13px}"
    ".page-head{display:flex;align-items:center;gap:12px;margin:10px 0 20px}"
    "h1{margin:0;font-size:24px;color:var(--text)}"
    ".chip{display:inline-block;padding:2px 9px;border-radius:5px;font-size:12px;font-weight:700}"
    ".chip-ok{background:var(--ok-bg);color:var(--ok)}.chip-bad{background:var(--bad-bg);color:var(--bad)}"
    ".chip-warn{background:var(--warn-bg);color:var(--warn)}.chip-info{background:var(--info-bg);color:var(--info)}"
    ".chip-neutral{background:var(--pill);color:var(--heading)}"
    ".card,.readiness,.login-preflight,.manual-action{background:var(--card);border:1px solid var(--line);border-radius:10px;"
    "padding:18px 20px;margin:0 0 16px;box-shadow:0 1px 2px #1018280a}"
    ".pill{display:inline-flex;align-items:center;gap:6px;background:var(--pill);color:var(--heading);border-radius:6px;"
    "padding:4px 10px;font-weight:700;font-size:14px}"
    ".card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 12px}"
    ".facts{display:flex;flex-wrap:wrap;gap:6px 22px;margin:4px 0 14px;color:var(--muted);font-size:13px}"
    ".facts b{color:var(--text);font-weight:600}"
    ".note,.login-safety,.manual-blocker{color:var(--muted);font-size:12.5px}"
    "button{font:inherit;font-weight:600;border-radius:6px;cursor:pointer;padding:8px 16px;border:1px solid var(--primary);"
    "background:var(--primary);color:#fff}button:hover{background:var(--primary-dark)}"
    "button[disabled]{background:var(--pill);border-color:var(--line);color:#9aa1ad;cursor:not-allowed}"
    "button.ghost{background:#fff;color:var(--primary)}button.ghost:hover{background:#f3f7fe}"
    ".start-form{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}"
    ".start-form label,.reset-form label{color:var(--muted);font-size:13px}"
    ".reset-form{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin-top:10px}"
    ".outcome{display:flex;gap:14px;align-items:flex-start;border-radius:8px;padding:14px 16px;margin:0 0 14px}"
    ".outcome-success{background:#f1fbe9}.outcome-failed{background:#fff3f2}.outcome-info{background:#fffbe6}"
    ".outcome-icon{flex:none;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:#fff;"
    "font:700 18px/1 system-ui,sans-serif}"
    ".outcome-success .outcome-icon{background:#52a31f}.outcome-failed .outcome-icon{background:#d92d20}"
    ".outcome-info .outcome-icon{background:#d4a106}"
    ".outcome strong{font-size:15px}.outcome-success strong{color:var(--ok)}.outcome-failed strong{color:var(--bad)}"
    ".outcome-info strong{color:var(--warn)}.outcome p{margin:2px 0 4px}"
    ".outcome .meta{color:var(--muted);font-size:12px}"
    ".runprog{margin:8px 0}.runprog-head{display:flex;justify-content:space-between;gap:12px;font-weight:600}"
    ".runprog-bar{height:10px;margin:6px 0 8px;border-radius:5px;background:var(--line);overflow:hidden}"
    ".runprog-fill{display:block;height:100%;background:var(--primary)}.runprog-failed .runprog-fill{background:var(--bad)}"
    ".runprog-done .runprog-fill{background:var(--ok)}"
    ".runprog-steps{margin:0;padding-left:20px;font-size:13px;color:var(--muted)}"
    ".runprog-steps .s-done{color:var(--ok)}.runprog-steps .s-active{color:var(--text);font-weight:700}"
    ".runprog-steps .s-failed{color:var(--bad);font-weight:700}"
    "code{font:12px/1.4 Consolas,'SFMono-Regular',monospace;background:var(--pill);padding:1px 5px;border-radius:4px}"
    ".readiness h2,.login-preflight h2,.manual-action h2{margin:0 0 4px;font-size:15px;color:var(--heading)}"
    ".readiness p,.login-preflight p,.manual-action p{margin:2px 0 0;color:var(--muted)}"
    ".readiness-heading{display:flex;gap:12px;align-items:flex-start}"
    ".readiness-icon{display:grid;place-items:center;flex:0 0 24px;height:24px;border-radius:50%;color:#fff;font-weight:800;font-size:13px}"
    ".source-ready .readiness-icon{background:#52a31f}.source-blocked .readiness-icon{background:#d92d20}"
    ".source-warn .readiness-icon{background:#d4a106}"
    ".readiness details{margin:10px 0 0}.readiness summary{cursor:pointer;color:var(--primary);font-weight:600}"
    ".login-preflight,.manual-action{display:grid;grid-template-columns:1fr auto;gap:8px 18px;align-items:center}"
    ".login-preflight form{margin:0}.login-safety,.manual-blocker{grid-column:1/-1}"
    "dl{display:grid;grid-template-columns:minmax(180px,240px) 1fr;margin:0}"
    "dt,dd{margin:0;padding:10px 4px;border-bottom:1px solid var(--line)}"
    "dt{color:var(--muted);font-size:13px}dd{white-space:pre-wrap;overflow-wrap:anywhere}"
    "dt:last-of-type,dd:last-of-type{border-bottom:0}"
    ".toast{position:fixed;z-index:2;right:24px;top:20px;display:grid;gap:2px;max-width:430px;padding:13px 16px;"
    "border-radius:8px;background:#fff;box-shadow:0 6px 20px #0002}"
    "@media(max-width:760px){.shell{display:block}.side{flex-direction:row;flex-wrap:wrap;align-items:center;padding:12px}"
    ".brand-sub,.side-foot{display:none}main{padding:18px 16px}.login-preflight,.manual-action{grid-template-columns:1fr}"
    "dl{display:block}dt{border-bottom:0;padding-bottom:0}}"
    # Queue page (tiles that are both counts and filters, aligned table rows).
    ".head-meta{margin-left:auto;display:flex;align-items:center;gap:10px;color:var(--heading);font-size:12.5px}"
    ".head-meta form{margin:0}"
    ".tiles{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:0 0 16px}"
    "@media(max-width:1180px){.tiles{grid-template-columns:repeat(3,minmax(0,1fr))}}"
    ".tile.hist span i{font-style:normal;color:var(--primary-dark)}"
    ".tile{display:block;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;"
    "color:var(--text);box-shadow:0 1px 2px #1018280a}.tile:hover{border-color:var(--primary);text-decoration:none}"
    ".tile b{display:block;font-size:24px;line-height:1.15}.tile span{display:block;font-weight:600;color:var(--heading)}"
    ".tile small{display:block;color:var(--muted);font-size:12px;font-weight:400}"
    ".tile[aria-current=page]{border-color:var(--primary);box-shadow:inset 0 -3px 0 var(--primary)}"
    ".tile.zero b{color:var(--muted)}.tile.alert b{color:var(--bad)}"
    ".pill .n{color:var(--muted);font-weight:600}.chip{white-space:nowrap}"
    ".q{width:100%;border-collapse:collapse;table-layout:fixed}"
    ".q th{padding:0 10px 8px;text-align:left;color:var(--muted);font-size:12px;font-weight:600;border-bottom:1px solid var(--line)}"
    ".q td{padding:9px 10px;vertical-align:top;border-bottom:1px solid var(--line);font-size:13px}"
    ".q th:nth-child(1){width:25%}.q th:nth-child(2){width:17%}.q th:nth-child(3){width:14%}"
    ".q th:nth-child(4){width:17%}.q th:nth-child(6){width:7%}"
    ".q tbody tr:last-child td{border-bottom:0}.q tbody tr:hover{background:#f8fafc}"
    ".q .num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}"
    ".q .co{display:block;font-weight:700}.q .co .sub{color:var(--text);font-weight:400}.q .run{display:block;margin-top:4px}"
    ".q abbr{text-decoration:none}.sub{display:block;color:var(--muted);font-size:12px}.wait{color:var(--muted)}"
    ".empty{margin:4px 0;color:var(--muted)}.queue-card{overflow-x:auto}"
    ".actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 4px}.actions form{margin:0}"
    "@media(max-width:760px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.q .opt{display:none}"
    ".q{table-layout:auto}.head-meta{margin-left:0}.page-head{flex-wrap:wrap}}"
    # Closed-onboardings history (server-rendered SVG; CSS-only hover; validated palette:
    # CE #2a78d6, Surface & CE #eb6834, Surface #1baf7a; created ticks #4b5563).
    ".qh{--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--so:#9aa1ad;--cr:#4b5563;margin:0}"
    ".qh .s1{fill:var(--s1);background:var(--s1)}.qh .s2{fill:var(--s2);background:var(--s2)}"
    ".qh .s3{fill:var(--s3);background:var(--s3)}.qh .so{fill:var(--so);background:var(--so)}.qh .cr{background:var(--cr)}"
    ".qh-stats{display:flex;flex-wrap:wrap;gap:6px 32px;margin:0 0 12px}.qh-stat span{display:block;color:var(--muted);font-size:12.5px}"
    ".qh-stat b{display:block;font-size:20px;font-weight:600;color:var(--text);line-height:1.3}"
    ".qh-stat small{display:block;color:var(--muted);font-size:12px}"
    ".qh-legend{display:flex;flex-wrap:wrap;gap:4px 18px;margin:0 0 6px;padding:0;list-style:none;color:var(--heading);font-size:12.5px}"
    ".qh-legend li{display:flex;align-items:center;gap:6px}.qh-sw{width:10px;height:10px;border-radius:2px}"
    ".qh-lk{width:16px;height:2px;border-radius:1px;background:var(--cr)}"
    # rtl on the scroller opens a narrow window on the newest months; the plot itself stays ltr.
    ".qh-scroll{overflow-x:auto;direction:rtl}"
    ".qh-plot{direction:ltr;margin:0 auto 0 0;position:relative;min-width:748px;max-width:880px}"
    ".qh svg{display:block;width:100%;height:auto;overflow:visible}"
    ".qh svg text{fill:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;text-anchor:middle}"
    ".qh svg .yt{text-anchor:end}.qh svg .yr{font-size:11px}"
    ".qh svg .cap{fill:var(--text);font-weight:600;paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round}"
    ".qh svg .grid{stroke:var(--line)}.qh svg .base{stroke:#9aa1ad}.qh svg .pb{fill:#f4f5f7}"
    ".qh svg .th{fill:none;stroke:#fff;stroke-width:4;stroke-linecap:round}"
    ".qh svg .tk{fill:none;stroke:var(--cr);stroke-width:2;stroke-linecap:round}"
    ".qh-hits{position:absolute;top:0;bottom:0;left:4.5455%;width:94.5455%}"
    ".qh-hit{position:absolute;top:0;bottom:0;border-radius:6px}"
    ".qh-hits:hover .qh-hit{background:#ffffff8c}.qh-hits .qh-hit:hover{background:none}"
    ".qh-tt{position:absolute;top:4px;z-index:3;visibility:hidden;pointer-events:none;width:max-content;max-width:280px;"
    "padding:8px 10px;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 20px #0000001f;"
    "font-size:12px;color:var(--heading);line-height:1.35}.qh-tt>b{display:block;margin:0 0 4px;color:var(--muted)}"
    ".qh-tt.r{left:calc(100% + 4px)}.qh-tt.l{right:calc(100% + 4px)}.qh-hit:hover .qh-tt{visibility:visible}"
    ".qh-g{display:grid;grid-template-columns:12px auto auto;gap:2px 8px;align-items:center}"
    ".qh-g i{height:3px;border-radius:2px}.qh-g b{color:var(--text);text-align:right;font-variant-numeric:tabular-nums}"
    ".qh-g .tot{border-top:1px solid var(--line);padding-top:3px;margin-top:2px}"
    ".qh-table{margin:10px 0 0}.qh-table summary{cursor:pointer;color:var(--primary-dark);font-weight:600;font-size:13px}"
    ".qh-table table{border-collapse:collapse;margin:8px 0 0;font-size:12.5px;width:100%}"
    ".qh-table th,.qh-table td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}"
    ".qh-table th:first-child,.qh-table td:first-child{text-align:left}.qh-table thead th{color:var(--muted);font-weight:600}"
    ".qh-table tfoot td{font-weight:600}.qh-note{margin:8px 0 0;color:var(--muted);font-size:12px}"
    ".session-chip{display:block;margin:14px 0 0;text-decoration:none}.sessions{border-collapse:collapse;margin:10px 0;width:100%}"
    ".sessions th,.sessions td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}"
        ".muted{color:var(--muted)}.section-title{font-size:15px;margin:22px 0 8px}"
    ".sessions th{width:190px}.trouble{margin-top:14px}.trouble summary{cursor:pointer;color:var(--muted);font-size:13px}"
    ".banner{background:var(--info-bg);color:var(--text);border-radius:6px;padding:10px 14px;margin:0 0 14px}"
    ".operator{margin:14px 0 0;font-size:12px;color:#c9d1e0;word-break:break-all}.operator form{margin-top:6px}"
    ".banner-bad{background:var(--bad-bg)}.banner-warn{background:var(--warn-bg)}.inv{overflow-x:auto}.inv th,.inv td{text-align:left!important;white-space:nowrap}"
    ".inv td:first-child{white-space:normal;min-width:160px}"
    # Wide pages (2026-10-05): full-width main, filter bar, scrolling table with sticky header and first column.
    "main.wide{max-width:none}.inv-card{padding:14px 16px}"
    ".filterbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0 0 12px}"
    ".filterbar input[name=q]{width:220px;padding:6px 10px;border:1px solid var(--line);border-radius:6px;font:inherit}"
    ".fgroup{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}"
    ".fchip{padding:5px 11px;font-size:12.5px;font-weight:600;color:var(--heading);background:#fff;"
    "border-right:1px solid var(--line)}.fchip:last-child{border-right:0}"
    ".fchip:hover{background:#f3f7fe;text-decoration:none}.fchip.on{background:var(--info-bg);color:var(--info)}"
    ".filterbar .count{margin-left:auto;color:var(--muted);font-size:12.5px}.filterbar .count b{color:var(--text)}"
    ".inv-card .inv{max-height:70vh;min-height:320px;overflow:auto}"
    ".inv-card table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}"
    ".inv-card th{position:sticky;top:0;z-index:2;background:#fff;color:var(--muted);font-size:12px;font-weight:600;"
    "padding:7px 10px;border-bottom:1px solid var(--line)}"
    ".inv-card td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}"
    ".inv-card .stick{position:sticky;left:0;z-index:1;background:#fff;border-right:1px solid var(--line);font-weight:600}"
    ".inv-card th.stick{z-index:3}.inv-card tbody tr:hover td{background:#f8fafc}"
    ".inv-card abbr{text-decoration:none;cursor:help}.inv-card td.stick{max-width:300px}"
    ".inv-card td.dom{max-width:230px;overflow:hidden;text-overflow:ellipsis}.inv-card td .sub code{font-size:11px}"
    ".sortlink{color:inherit;font-weight:600}.sortlink:hover{color:var(--primary-dark);text-decoration:none}"
    # Open onboardings (2026-10-05): one filter bar and one dense, sortable table.
    ".page-head>.note{margin-top:4px}.tbl-card{padding:0;overflow:hidden}.tbl-card .empty{padding:18px 20px}"
    ".fbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0 0 14px}"
    ".fbar .fchip b{font-weight:700;color:var(--text)}.fchip.ok b{color:var(--ok)}.fchip.bad b{color:var(--bad)}"
    ".fchip.ok{background:var(--ok-bg)}.fchip.on{background:var(--info-bg)}"
    ".fbar>.fchip{border:1px solid var(--line);border-radius:8px;margin-left:auto}"
    ".fbar>.fchip small{color:var(--muted);font-weight:400}"
    ".tbl-wrap{max-height:calc(100vh - 210px);overflow:auto}"
    ".dense{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}"
    ".dense th{position:sticky;top:0;z-index:2;background:#f9fafb;color:var(--muted);font-size:12px;font-weight:600;"
    "padding:8px 10px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--line)}"
    ".dense td{padding:8px 10px;vertical-align:top;border-bottom:1px solid var(--line)}"
    ".dense th a.sort{color:inherit;font-weight:600}.dense th a.sort:hover{color:var(--primary-dark);text-decoration:none}"
    ".dense th a.sort::after{content:' \\2195';opacity:.3}"
    ".dense th[aria-sort=ascending] a.sort::after{content:' \\2191';opacity:1;color:var(--primary)}"
    ".dense th[aria-sort=descending] a.sort::after{content:' \\2193';opacity:1;color:var(--primary)}"
    ".dense .stick{position:sticky;left:0;z-index:1;background:var(--card);border-right:1px solid var(--line)}"
    ".dense th.stick{z-index:3;background:#f9fafb}.dense .num{text-align:right;white-space:nowrap}"
    ".dense .co{display:block;font-weight:700}.dense .run{display:block;margin-top:4px}"
    ".dense .c-start{white-space:nowrap}.dense .c-start b{display:block}.dense .c-act{white-space:nowrap;text-align:right}"
    ".dense tbody tr:hover td{background:#f8fafc}"
    ".dense tr.is-ready td{background:#f6fdf0}.dense tr.is-ready td:first-child{box-shadow:inset 3px 0 0 var(--ok)}"
    ".dense tbody tr.is-ready:hover td{background:#eef9e4}"
    ".btn{display:inline-block;background:var(--primary);color:#fff;border-radius:6px;padding:4px 12px;font-weight:600}"
    ".btn:hover{background:var(--primary-dark);color:#fff;text-decoration:none}"
    ".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}"
    "details.menu{position:relative}details.menu>summary{cursor:pointer;list-style:none;padding:8px 14px;"
    "border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--heading);font-weight:600}"
    "details.menu>summary::-webkit-details-marker{display:none}"
    ".menu-pop{position:absolute;right:0;top:calc(100% + 4px);z-index:5;display:grid;gap:6px;padding:10px;"
    "background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 20px #0000001f;width:max-content}"
    ".menu-pop form{margin:0}"
    "@media(max-width:1300px){.dense .c-prod,.dense .c-sf{display:none}}"
    "@media(max-width:1100px){.dense .c-auto{display:none}}"
    # CO detail page (2026-10-04): summary strip, one next-step card, follow-ups,
    # tenant-health tiles, and folded secondary panels. Existing tokens only.
    ".page-head .acct{color:var(--muted);font-size:15px;font-weight:600}"
    # CO detail layout (2026-10-07): pinned alerts, one "What to do now" card, key-facts grid, one footer line.
    ".pinned{margin:0 0 14px}.pinned>.banner,.pinned>.readiness,.pinned>.manual-action,.pinned>.outcome{margin-bottom:10px}"
    ".nowcard .next{margin:0 0 10px;font-size:15px;color:var(--heading)}.nowcard .clistep{margin:0 0 12px}"
    ".nowcard .todo{margin:0 0 8px}.nowcard>.note{margin:0 0 8px}"
    ".keyfacts{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 20px;margin:0 0 12px}"
    "dl.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px 24px;margin:0}"
    "dl.kv>div{min-width:0}dl.kv dt,dl.kv dd{border:0;padding:0;white-space:normal}"
    "dl.kv dt{font-size:12px}dl.kv dd{font-weight:600;color:var(--text);font-size:13.5px}"
    "dl.kv.sum5{margin:10px 0 0;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}"
    "details.more details.more{box-shadow:none;margin:12px 0 0}"
    ".footline{margin:16px 0 0;color:var(--muted);font-size:12px}"
    ".idbar{padding:10px 20px}.idbar dd code{word-break:break-all;font-size:13px}"
    ".plan-summary{margin:0 0 12px;padding-left:18px;font-size:13px}.plan-summary li{margin:2px 0}"
    ".lede{margin:0 0 12px;color:var(--heading)}"
    ".sub-h{margin:16px 0 4px;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}"
    "dl.compact{grid-template-columns:minmax(120px,170px) minmax(0,1fr) minmax(120px,170px) minmax(0,1fr);column-gap:12px}"
    "dl.compact dt,dl.compact dd{padding:7px 4px}"
    ".start-form{display:grid;justify-items:start;gap:10px;margin:14px 0 8px}"
    ".confirm{display:grid;gap:8px;justify-self:stretch;padding:12px 14px;background:var(--canvas);border-radius:8px}"
    ".confirm label{display:flex;gap:8px;align-items:flex-start;color:var(--text);font-size:13.5px}.confirm input{margin-top:3px}"
    ".start-row{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:10px 0 0}.start-row form{margin:0}"
    ".meta-line{margin:10px 0 0;color:var(--muted);font-size:12px}"
    ".blockers{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:6px}"
    ".blockers li{background:#fff3f2;border-radius:6px;padding:8px 12px}"
    ".outcome.compact{padding:10px 14px;margin:0 0 12px}.outcome.compact .outcome-icon{width:26px;height:26px;font-size:14px}"
    ".outcome form{margin:6px 0}"
    ".todo{list-style:none;margin:0;padding:0}"
    ".todo li{display:flex;align-items:center;gap:12px;padding:9px 0;border-bottom:1px solid var(--line)}"
    ".todo li:last-child{border-bottom:0}.todo .what{flex:1}.todo form{margin:0}.todo li.done{color:var(--muted)}"
    "button.sm{padding:4px 12px;font-size:12.5px}"
    ".health{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:0 0 16px}"
    ".stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;"
    "box-shadow:0 1px 2px #1018280a;min-width:0}"
    ".stat-head{display:flex;align-items:center;gap:8px}.stat-head h2{margin:0;font-size:14px;color:var(--heading)}"
    ".stat p{margin:4px 0 10px;color:var(--muted);font-size:12.5px}.stat form{margin:0}"
    ".stat dl{grid-template-columns:110px minmax(0,1fr);margin:0 0 10px;font-size:12.5px}.stat dt,.stat dd{padding:5px 2px}"
    ".fold{margin:10px 0 0}.fold>summary{cursor:pointer;color:var(--primary-dark);font-weight:600;font-size:13px}"
    ".fold[open]>summary{margin-bottom:6px}"
    "details.more{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:0 0 10px;"
    "box-shadow:0 1px 2px #1018280a}"
    "details.more>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;padding:12px 20px}"
    "details.more>summary::-webkit-details-marker{display:none}"
    "details.more>summary::before{content:'\\25B8';color:var(--muted);font-size:12px}"
    "details.more[open]>summary::before{content:'\\25BE'}"
    "details.more>summary .note{margin-left:auto}.sum-h{margin:0;font-size:15px;color:var(--heading)}"
    "details.more>.body{padding:0 20px 14px}"
    "details.more .readiness-icon{flex:0 0 20px;height:20px;font-size:11px}"
    "details.more .login-preflight,details.more .manual-action{box-shadow:none;margin:10px 0 0;padding:12px 14px}"
    # Stage tracker (2026-10-06): horizontal stepper, plain CSS, no assets.
    ".tracker{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 20px 10px;margin:8px 0 8px;"
    "box-shadow:0 1px 2px #1018280a}"
    ".stages{list-style:none;margin:0;padding:0;display:flex;gap:0}"
    ".st{flex:1 1 0;min-width:0;position:relative;display:flex;flex-direction:column;align-items:center;gap:6px;"
    "text-align:center;font-size:12.5px;color:var(--muted)}"
    ".st::before{content:'';position:absolute;top:13px;left:-50%;width:100%;height:2px;background:var(--line);z-index:0}"
    ".st:first-child::before{display:none}.st.done::before,.st.current::before{background:var(--ok)}"
    ".dot{position:relative;z-index:1;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;"
    "font-weight:700;font-size:13px;background:var(--pill);color:var(--muted);border:2px solid var(--line)}"
    ".st.done .dot{background:var(--ok);border-color:var(--ok);color:#fff}"
    ".st.current .dot{background:#fff;border-color:var(--primary);color:var(--primary-dark)}"
    ".st.current .lbl{color:var(--text);font-weight:700}.st.done .lbl{color:var(--heading)}"
    ".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}"
    ".tracker-note{margin:10px 0 0;font-size:12.5px}.tracker-note+.tracker-note{margin-top:4px}"
    "@media(max-width:760px){.stages{flex-direction:column;gap:8px}.st{flex-direction:row;text-align:left}"
    ".st::before{display:none}}"
    "@media(max-width:760px){dl.compact,.stat dl{display:block}.health{grid-template-columns:1fr}"
    ".todo li{flex-wrap:wrap}}"
    # Two areas (2026-10-07): environment tokens, top bar, header pill, grouped sidebar, segmented control.
    ":root{--env-dev:#1e3a8a;--env-dev-line:#6f8fdc;--env-prod:var(--warn);--env-prod-bg:var(--warn-bg)}"
    ".envbar{position:fixed;top:0;left:0;right:0;height:3px;z-index:5;background:var(--nav-active)}"
    ".envbar.dev{background:var(--env-dev)}.envbar.prod{background:var(--env-prod)}"
    ".envrow{position:sticky;top:3px;z-index:4;margin:-8px 0 6px;padding:4px 0;background:var(--canvas)}"
    ".envpill{display:inline-block;padding:3px 12px;border-radius:999px;font:800 12px/1.4 -apple-system,'Segoe UI',sans-serif;"
    "letter-spacing:.4px;background:var(--pill);color:var(--heading)}"
    ".envpill.dev{background:var(--env-dev);color:#fff}.envpill.prod{background:var(--env-prod-bg);color:var(--env-prod);"
    "border:1px solid var(--env-prod)}"
    ".navgroup{display:flex;flex-direction:column;gap:2px;margin:0 0 12px}"
    ".navhead{font:800 11px/1.3 -apple-system,'Segoe UI',sans-serif;letter-spacing:.6px;color:#aeb6c4;margin:2px 8px 4px}"
    ".navgroup.dev .navhead{color:#fff;background:var(--env-dev);border:1px solid var(--env-dev-line);border-radius:5px;padding:3px 8px;margin:2px 0 4px}"
    ".navgroup.prod .navhead{color:var(--env-prod);background:var(--env-prod-bg);border-radius:5px;padding:3px 8px;margin:2px 0 4px}"
    ".navhead .ro{font-weight:600;letter-spacing:0;margin-left:6px}"
    ".navgroup a::before{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:9px;vertical-align:1px;"
    "border:2px solid #8b94a5;box-sizing:border-box}"
    ".navgroup.dev a::before{background:var(--env-dev-line);border-color:var(--env-dev-line)}"
    ".navgroup.prod a::before{background:transparent;border-color:var(--env-prod-bg)}"
    ".seg{display:inline-flex;gap:8px;margin:0 0 14px}.seg a{display:inline-flex;align-items:center;gap:8px;background:#fff;"
    "color:var(--primary);border:1px solid var(--primary);border-radius:6px;padding:6px 14px;font-weight:600}"
    ".seg a:hover{background:#f3f7fe;text-decoration:none}.seg a.on{background:var(--primary);color:#fff}"
    ".seg a.on .chip{outline:1px solid #fff}"
    ".strip{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}"
    ".prodnote{background:var(--env-prod-bg);color:var(--env-prod);border-radius:8px;padding:8px 12px;margin:0 0 14px;font-size:13px}"
    # Sidebar search (GET /search, no script).
    ".sidesearch{display:flex;flex-direction:column;gap:6px;margin:0 0 14px}"
    ".sidesearch input{width:100%;padding:7px 9px;border:1px solid var(--line);border-radius:6px;font:inherit;font-size:13px;"
    "background:#fff;color:var(--text)}.sidesearch button{padding:6px 12px;font-size:13px}"
    "@media(max-width:760px){.sidesearch{flex-direction:row;flex:1 1 100%;margin:0 0 8px}.sidesearch input{flex:1}}"
    "@media(max-width:760px){.navgroup{flex-direction:row;flex-wrap:wrap;align-items:center;margin:0 8px 0 0}"
    ".navhead{display:none}.envrow{margin:-8px 0 6px}}"
)


# Two areas (owner decision 2026-10-07): Leonardo Development (navy, solid dot) and Production through BackOffice
# (amber from the warn palette, hollow dot, read-only). Red is never an environment colour.
NAV_GROUPS = (
    ("dev", "LEONARDO · DEV", "", (("/", "Onboardings", "onboardings"), ("/dev/onboarded", "Onboarded on Dev", "dev_onboarded"),
                                    ("/history", "History", "history"), ("/inventory", "Tenants", "inventory"))),
    ("prod", "PRODUCTION · BO", "read-only", (("/prod", "Readiness", "prod"), ("/prod/matches", "Matches", "prod_matches"),
                                                  ("/prod/tenants", "Tenants", "prod_tenants"))),
    ("tools", "TOOLS", "", (("/connection", "Sessions", "connection"),)),
)
ENV_BY_ACTIVE = {"onboardings": "dev", "dev_onboarded": "dev", "history": "dev", "inventory": "dev",
                 "prod": "prod", "prod_matches": "prod", "prod_tenants": "prod"}
ENV_PILLS = {"dev": "DEV · Leonardo", "prod": "PRODUCTION · read-only", "tools": "TOOLS"}


SEARCH_FORM = ("<form class='sidesearch' method='get' action='/search' role='search'><input type='text' name='q' maxlength='80' "
               "placeholder='Search CO or account' aria-label='Search CO or account' autocomplete='off'>"
               "<button class='ghost' type='submit'>Search</button></form>")


def _app_shell(title: str, main_html: str, refresh: str = "", active: str = "", wide: bool = False,
               env: str = "") -> str:
    """Wrap a page in the Pentera-styled shell (navy sidebar + light canvas, environment bar and pill)."""
    env = env if env in ("dev", "prod") else ENV_BY_ACTIVE.get(active, "tools")

    def nav(href: str, label: str, key: str) -> str:
        return "<a href='" + href + "'" + (" class='active'" if key == active else "") + ">" + label + "</a>"

    groups = "".join(
        "<div class='navgroup " + key + "'><div class='navhead'>" + head
        + ("<span class='ro'>" + note + "</span>" if note else "") + "</div>"
        + "".join(nav(*link) for link in links) + "</div>" for key, head, note, links in NAV_GROUPS)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>" + refresh +
        "<title>" + escape(title) + "</title><style>" + PENTERA_CSS + (SNAPSHOT_BANNER_CSS if vm_mode() else "") + "</style></head><body>"
        "<div class='envbar " + env + "' aria-hidden='true'></div><div class='shell'>"
        "<aside class='side'><span class='brand'>PENTERA.</span><div class='brand-sub'>Surface Onboarding</div>"
        + SEARCH_FORM + groups + ("" if vm_mode() else _session_chip()) + _operator_block() +
        "<div class='side-foot'>" + ("Read-only copy · published from the desktop" if vm_mode() else "Attended · localhost only") + "</div></aside>"
        "<main" + (" class='wide'" if wide else "") + "><div class='envrow'><span class='envpill " + env + "'>"
        + escape(ENV_PILLS[env]) + "</span></div>" + (snapshot_banner() + vm_strip_actions(main_html) if vm_mode() else main_html)
        + "</main></div></body></html>"
    )


def _operator_block() -> str:
    operator = _current_operator.get()
    if not operator:
        return ""
    if vm_mode():
        return ("<div class='operator'>Signed in as<br><strong>" + escape(operator) + "</strong>"
                "<br><a href='" + VM_SIGN_OUT_PATH + "'>Sign out</a></div>")
    return ("<div class='operator'>Signed in as<br><strong>" + escape(operator) + "</strong>"
            "<form method='post' action='/logout'><button class='ghost' type='submit'>Sign out</button></form></div>")


def _onboarding_chip(reference: str) -> str:
    """Header chip for the CO's latest attended CE onboarding run (if any)."""
    try:
        record = load_runner_state().get(reference)
    except RunnerStateUnavailable:
        return ""
    if record is None:
        return ""
    result = record.get("result")
    if result is None:
        return "<span class='chip chip-warn'>Running</span>"
    if result == "readback_verified":
        return "<span class='chip chip-ok'>Onboarded</span>"
    if create_uncertain(record):
        return "<span class='chip chip-warn'>Create uncertain</span>"
    if result in DUPLICATE_RESULTS:
        return "<span class='chip chip-bad'>Duplicate</span>"
    return "<span class='chip chip-bad'>Onboarding failed</span>"


PRODUCTION_GATE_NOTE = ("<p class='note'>Every onboarding Start checks this production list first; a match stops it.</p>")
PRODUCTION_COLLECT_HINT = "tools\\redash_inventory_collector.py --collect"


def _production_mark(check: dict[str, str] | None) -> tuple[str, list[dict], str] | None:
    """(result, matches, reason) for a recorded production duplicate check that stopped the Start; else None."""
    if not check or check.get("kind") != "production_duplicate":
        return None
    result = check.get("result")
    if result not in ("duplicate_production_clone_match", "production_clone_unavailable", "production_clone_incomplete"):
        return None
    try:
        detail = json.loads(check.get("detail") or "{}")
    except ValueError:
        detail = {}
    detail = detail if isinstance(detail, dict) else {}
    matches = [m for m in detail.get("matches") or () if isinstance(m, dict)]
    return result, matches, str(detail.get("reason") or result)


def _production_chip(check: dict[str, str] | None) -> str:
    """Queue-row chip for a CO whose last Start was stopped by the production duplicate check."""
    mark = _production_mark(check)
    if mark is None:
        return ""
    if mark[0] == "duplicate_production_clone_match":
        return "<span class='chip chip-bad'>Already in production</span>"
    return "<span class='chip chip-warn'>Production check unavailable</span>"


def _production_marker(check: dict[str, str] | None) -> str:
    """CO-page marker: the tenant(s) already in production, or why the production check could not answer."""
    mark = _production_mark(check)
    if mark is None:
        return ""
    result, matches, reason = mark
    if result == "duplicate_production_clone_match":
        items = "".join(
            "<li>" + escape(str(m.get("name") or "—")) + " · <code>" + escape(str(m.get("id") or "—")) + "</code> · created "
            + escape(_created_date(m.get("created"))) + "</li>" for m in matches)
        return ("<div class='banner banner-warn'><span class='chip chip-bad'>Already in production</span> "
                "<strong>This CO already has a tenant in production.</strong> Nothing was created in Leonardo."
                "<ul>" + items + "</ul></div>")
    return ("<div class='banner banner-warn'><strong>Production check unavailable — " + escape(reason) + "</strong> "
            "Start stops until the production list is fresh. Run <code>" + PRODUCTION_COLLECT_HINT + "</code>.</div>")


def _production_marker_for(reference: str) -> str:
    try:
        return _production_marker(load_check_state().get(reference))
    except ValueError:
        return ""


def _outcome_banner(kind: str, message: str, result: str, completed: str | None = None,
                    headline: str | None = None, compact: bool = False) -> str:
    """Render one attended-run outcome with a green check or red cross icon.

    ``kind`` comes from RUNNER_RESULT_MESSAGES: success is green with a check,
    blocked (any failure, including an unknown result code) is red with a
    cross, and info is amber. ``message`` is trusted static text from
    RUNNER_RESULT_MESSAGES; ``result`` and ``completed`` are escaped here.
    ``headline`` overrides the default (RUNNER_HEADLINES supplies it for
    results such as a duplicate).
    """
    icon, default_headline, modifier = OUTCOME_STYLES.get(kind, OUTCOME_STYLES["blocked"])
    headline = headline or RUNNER_HEADLINES.get(result) or RENEWAL_RUN_HEADLINES.get(result) or default_headline
    when = " · " + escape(completed) if completed else ""
    return (
        "<div class='outcome outcome-" + modifier + (" compact" if compact else "") + "' role='status' aria-label='"
        + escape(headline) + "'>"
        "<span class='outcome-icon' aria-hidden='true'>" + icon + "</span>"
        "<div><strong>" + escape(headline) + "</strong><p>" + message + "</p>"
        "<span class='meta'>Result <code>" + escape(result) + "</code>" + when + "</span></div></div>"
    )


def _entered_license_note(record: dict[str, str] | None) -> str:
    """The licence dates the runner actually entered before Confirm (if recorded).

    They can differ from the planned scope: Leonardo Development may refuse the
    run day as start, and the runner then enters the day before (2026-09-30).
    """
    if not record or not record.get("license_start_entered") or not record.get("license_end_entered"):
        return ""
    return ("<p class='note'>Licence dates entered in Leonardo Development: <b>"
            + escape(record["license_start_entered"]) + " → " + escape(record["license_end_entered"]) + "</b></p>")


def _uncertain_banner(ref: str, record: dict[str, str]) -> str:
    """Amber banner for a failed run that may have created a tenant (review item 2)."""
    return ("<div class='outcome outcome-info' role='status' aria-label='Create uncertain'>"
            "<span class='outcome-icon' aria-hidden='true'>?</span><div><strong>Create uncertain — verify in Leonardo</strong>"
            "<p>The run stopped after the licence dates were entered, at or after Confirm (<code>"
            + escape(record.get("result", "")) + "</code>). A tenant may exist. Start and reset stay blocked until a "
            "read-only check settles it: a found tenant is recorded as onboarded; no tenant re-arms the run.</p>"
            "<form method='post' action='/attended/verify-uncertain'><input type='hidden' name='reference' value='" + ref + "'>"
            "<button type='submit'>Verify in Leonardo (read-only)</button></form>"
            "<span class='meta'>Result <code>" + escape(record.get("result", "")) + "</code>"
            + (" · " + escape(record["completed_on"]) if record.get("completed_on") else "") + "</span></div></div>")


def _renewal_uncertain_banner(ref: str, record: dict[str, str]) -> str:
    """Amber banner for a renewal run that stopped after a Leonardo write that may have happened (ref is escaped)."""
    result = escape(record.get("result", ""))
    meta = ("<span class='meta'>Result <code>" + result + "</code>"
            + (" · " + escape(record["completed_on"]) if record.get("completed_on") else "") + "</span>")
    if record.get("uncertain") == "mirror_create":
        return ("<div class='outcome outcome-info' role='status' aria-label='Dev mirror uncertain'>"
                "<span class='outcome-icon' aria-hidden='true'>?</span><div><strong>Dev mirror uncertain - verify in Leonardo</strong>"
                "<p>The run stopped at or after the Dev mirror's Confirm (<code>" + result + "</code>). A mirror tenant may exist. "
                "Check Leonardo Development by hand; start and reset stay blocked until it is settled.</p>" + meta + "</div></div>")
    return ("<div class='outcome outcome-info' role='status' aria-label='Renewal uncertain'>"
            "<span class='outcome-icon' aria-hidden='true'>?</span><div><strong>Renewal save uncertain - verify in Leonardo</strong>"
            "<p>The run stopped at or after the renewal's Confirm (<code>" + result + "</code>). The Dev tenant may carry the new "
            "term. Start and reset stay blocked until a read-only check settles it: a tenant that already shows the new term is "
            "recorded as renewed; a tenant that does not re-arms the run.</p>"
            "<form method='post' action='/attended/verify-uncertain'><input type='hidden' name='reference' value='" + ref + "'>"
            "<button type='submit'>Verify in Leonardo (read-only)</button></form>" + meta + "</div></div>")


_START_IN_PROGRESS_NOTE = (
    "<p class='note'><b>A run for an earlier source revision has not reported a result.</b> The CO changed in "
    "Salesforce while it was running, so no new run can start until it finishes.</p>")


def _progress_button(ref: str) -> str:
    """The one action while a run is open: the read-only status page (GET; ``ref`` is already escaped)."""
    return ("<form method='get' action='/attended/ce-only-runner-status'><input type='hidden' name='ref' value='"
            + ref + "'><button type='submit'>View progress</button></form>")


def _progress_events(ref: str, record: dict[str, str]) -> list[dict[str, str]]:
    """Milestone events recorded since this run started (read-only; unreadable means none)."""
    import tools.attended_ce_only_playwright as runner_module
    try:
        return run_progress.read_milestones(runner_module.PROGRESS_PATH, ref, record.get("started_on", ""))
    except Exception:
        return []


def _progress_bar(ref: str, record: dict[str, str]) -> str:
    """CSS-only progress bar of an open run: step labels, percent and state only (no script, no payload)."""
    progress = run_progress.build_progress(record.get("route"), _progress_events(ref, record))
    items = "".join("<li class='s-" + step.state + "'>" + escape(step.label)
                    + (" (running)" if step.state == "active" else "") + "</li>" for step in progress.steps)
    return ("<div class='runprog' aria-live='polite'><div class='runprog-head'><span>Onboarding in progress: "
            + escape(progress.current) + "</span><span>" + str(progress.percent) + "%</span></div>"
            "<div class='runprog-bar' role='progressbar' aria-label='Onboarding progress' aria-valuemin='0' "
            "aria-valuemax='100' aria-valuenow='" + str(progress.percent) + "'><span class='runprog-fill' style='width:"
            + str(progress.percent) + "%'></span></div><ol class='runprog-steps'>" + items + "</ol></div>")


def _running_note(ref: str, record: dict[str, str]) -> str:
    started = " at " + escape(record["started_on"]) if record.get("started_on") else ""
    return (_progress_bar(ref, record) + "<p class='note'>An attended run for this source revision was started" + started +
            " and has not reported a result. Do not start another.</p>" + _progress_button(ref))


def _open_run_for(reference: str) -> bool:
    """True while a run for ``reference`` has been claimed and has no result (the page then refreshes itself)."""
    try:
        record = load_runner_state().get(reference)
    except (RunnerStateUnavailable, AttributeError):
        return False
    return record is not None and not record.get("result")


def _reset_form(ref: str, record: dict[str, str], what: str = "nothing was created") -> str:
    """Re-arm a failed run; folded away for a duplicate, where the next step is a manual review."""
    form = ("<form class='reset-form' method='post' action='/attended/reset-ce-only-runner'>"
            "<input type='hidden' name='reference' value='" + ref + "'>"
            "<label><input type='checkbox' name='reset_authorized' value='1' required> "
            "Re-arm this failed run (" + escape(what) + ")</label>"
            "<button type='submit' class='ghost'>Reset runner record</button></form>")
    if record.get("result") in DUPLICATE_RESULTS:
        return "<details class='fold'><summary>Re-arm this run</summary>" + form + "</details>"
    return form


def _onboard_card(title: str, status_chip: str, body: str) -> str:
    return ("<section class='card onboard' aria-labelledby='onboard-title'>"
            "<div class='card-head'><h2 id='onboard-title' class='pill'>" + title + "</h2>" + status_chip + "</div>"
            + body + "</section>")


def _ce_only_onboard_section(reference: str, lede: str = "") -> str:
    """Render the primary Onboard action for one approved CE-only CO.

    This inlines the fill preflight and the one-time, revision-bound start form so the
    operator can trigger the attended automation directly from the CO detail page.
    The runner opens an isolated browser, checks for duplicates, fills the Add Account form,
    and auto-confirms after a clear duplicate check. It never updates Salesforce.
    ``lede`` is trusted static HTML (the source-readiness sentence from page_detail).
    """
    parts = _ce_only_onboard_parts(reference)
    if "unavailable" in parts:
        return str(parts["unavailable"])
    return _onboard_card(
        "Credential Exposure onboarding", str(parts["chip"]),
        ("<p class='lede'>" + lede + "</p>" if lede else "") + str(parts["runner_note"]) + str(parts["blockers"])
        + str(parts["start"]) + str(parts["reset"]) + str(parts["meta"]))


def _ce_only_onboard_parts(reference: str) -> dict[str, Any]:
    """The pieces of the CE-only onboard card (the CO page composes them; ``state`` classifies the run).

    ``state`` is one of: unavailable, uncertain, running, duplicate, failed, onboarded, ready, blocked.
    ``banner`` is the compact outcome banner of a recorded run (inside ``runner_note``).
    """
    ref = escape(reference)
    try:
        evaluation = evaluate_ce_only_fill_preflight(reference)
    except ReadUnavailable:
        return {"state": "unavailable", "unavailable": (
            "<section class='readiness source-blocked' aria-labelledby='onboard-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>!</span><div><h2 id='onboard-title'>Onboard " + ref + " (Credential Exposure)</h2>"
            + ("<p>This VM shows published data only: onboarding runs start on the owner's desktop and the live source is not read here.</p>"
               if vm_mode() else
               "<p>The CE-only source could not be read. No browser was launched. Reconnect the attended Salesforce session and retry.</p>")
            + "</div></div></section>"
        )}
    try:
        state = load_runner_state()
    except RunnerStateUnavailable:
        state = None
    record = state.get(reference) if state is not None else None
    same_revision = record is not None and record.get("source_revision") == evaluation.source_revision
    if not evaluation.eligible_for_fill_review:
        status_chip = "<span class='chip chip-bad'>Blocked</span>"
    elif same_revision and record.get("result") == "readback_verified":
        status_chip = "<span class='chip chip-ok'>Onboarded</span>"
    elif same_revision and record.get("result") in DUPLICATE_RESULTS:
        status_chip = "<span class='chip chip-bad'>Duplicate</span>"
    elif same_revision and record.get("result"):
        status_chip = "<span class='chip chip-bad'>Failed</span>"
    elif same_revision:
        status_chip = "<span class='chip chip-warn'>Running</span>"
    else:
        status_chip = "<span class='chip chip-info'>Ready</span>"
    runner_note = ""
    banner = ""
    start_action = ""
    state_name = "ready"
    if same_revision and record.get("result"):
        kind, message = RUNNER_RESULT_MESSAGES.get(record["result"], ("blocked", "Result code <code>" + escape(record["result"]) + "</code>."))
        banner = _outcome_banner(kind, message, record["result"], record.get("completed_on"), compact=True)
        runner_note = banner + _entered_license_note(record) + _production_marker_for(reference)
        state_name = ("onboarded" if record["result"] == "readback_verified"
                      else "duplicate" if record["result"] in DUPLICATE_RESULTS else "failed")
    elif same_revision:
        runner_note = _running_note(ref, record)
        state_name = "running"
    elif start_blocker(record) == "run_in_progress":
        runner_note = _START_IN_PROGRESS_NOTE + _progress_button(ref)
        state_name = "running"
    elif start_blocker(record) == "tenant_already_verified":
        banner = _outcome_banner("success", RUNNER_RESULT_MESSAGES["readback_verified"][1],
                                 "readback_verified", record.get("completed_on"), compact=True)
        runner_note = banner + _entered_license_note(record)
        state_name = "onboarded"
    else:
        if record is not None:
            runner_note = ("<p class='note'>Previous attended run for a different source revision: <code>" +
                           escape(record.get("result", "no result recorded")) + "</code></p>")
        start_action = _ce_only_start_form(evaluation)
        state_name = "ready" if start_action else "blocked"
    blockers = ""
    if evaluation.blockers:
        blockers = ("<ul class='blockers'>" + "".join(
            "<li><b>Blocked:</b> " + escape(item.replace("_", " ")) + "</li>" for item in evaluation.blockers) + "</ul>")
    reset_action = ""
    if create_uncertain(record):
        runner_note = _uncertain_banner(ref, record)
        banner = runner_note
        start_action = ""
        state_name = "uncertain"
    elif record is not None and record.get("result") and record["result"] != "readback_verified":
        reset_action = _reset_form(ref, record)
    return {"state": state_name, "chip": status_chip, "runner_note": runner_note, "banner": banner,
            "blockers": blockers, "start": start_action, "reset": reset_action, "record": record,
            "evaluation": evaluation, "same_revision": same_revision,
            "meta": ("<p class='meta-line'>Email domains <b>" + str(evaluation.email_domain_count) + "</b> · Source revision <code>"
                     + escape(evaluation.source_revision) + "</code></p>")}


SCAN_REMINDER_TEXT = ("Scan now / scanning interval are ON for this Leonardo Development tenant — "
                      "turn them off later")
CE_REMINDER_TEXT = "Core Plus (Credential Exposure) purchased — enable Credential Exposure later"
# Owner decision 2026-09-30: the runner leaves Operator Account empty; it is
# assigned (TA/CSM from Salesforce) after the first scan finishes.
OPERATOR_REMINDER_TEXT = "Operator Account left empty — assign the TA/CSM from Salesforce after the first scan finishes"


def _surface_start_form(evaluation: SurfaceScopePreflight) -> str:
    """One-time Start form bound to the source revision AND the reviewed scope."""
    if not evaluation.eligible_for_fill_review:
        return ""
    return ("<form class='start-form' method='post' action='/attended/start-surface-runner'>"
            "<input type='hidden' name='reference' value='" + escape(evaluation.reference) + "'>"
            "<input type='hidden' name='route' value='" + escape(evaluation.route) + "'>"
            "<input type='hidden' name='source_revision' value='" + escape(evaluation.source_revision) + "'>"
            "<input type='hidden' name='scope_digest' value='" + escape(evaluation.scope_digest) + "'>"
            "<div class='confirm'><label><input type='checkbox' name='scope_reviewed' value='1' required> "
            "I reviewed the scope for this source revision</label>"
            "<label><input type='checkbox' name='attended_create_authorized' value='1' required> "
            "I authorize one Leonardo Development run for this source revision</label></div>"
            "<button type='submit'>Start Onboarding</button></form>" + _START_DUPLICATE_NOTE
            + _duplicate_precheck_row(evaluation.reference))


def _surface_scope_facts(evaluation: SurfaceScopePreflight) -> str:
    """Counts-only scope summary for the manual scope review (no domain values)."""
    scope = evaluation.scope
    if scope is None:
        return ""
    tier = str(scope["tier"]).title()
    licensed = (f"{scope['licensed_subdomains']} ({scope['baseline_subdomains']} baseline + "
                f"{scope['addon_subdomains']} add-on)")
    rows = [
        ("Tier", tier), ("Scanning interval", str(scope["scanning_interval"])),
        ("Main domain", str(scope["main_domains"])),
        ("Alternate root domains", str(scope["alternate_root_domains"])),
        ("Requested subdomains", str(scope["requested_subdomains"])),
        ("Number of domains", str(scope["number_of_domains"])),
        ("Licensed subdomains", licensed),
        ("Assets", str(scope["assets"])),
        ("License dates (planned)", f"{scope['license_start']} → {scope['license_end']}"),
        ("Onboarding day", (f"DEV: now · production: from {scope['production_onboarding_day']} (2 days before the "
                            f"{scope['subscription_start']} subscription start, or earlier on a CSM request)")
         if scope.get("production_onboarding_day") else "—"),
        ("Large scope (&gt;60)", "yes — review carefully" if scope["large_scope"] else "no"),
        ("Core Plus on account", "yes — CE to be enabled later" if scope.get("core_plus_present") else "no"),
    ]
    if scope.get("product_domains") is not None:
        rows.insert(6, ("Product domain allowance", str(scope["product_domains"])))
    if scope.get("leaked_credentials_domains") is not None:
        rows.append(("Leaked Credentials", f"ON · {scope['leaked_credentials_interval']} · "
                                           f"{scope['leaked_credentials_domains']} CE email domain"))
    return ("<dl class='scope compact'>" + "".join(
        "<dt>" + label + "</dt><dd>" + escape(value) + "</dd>" for label, value in rows) + "</dl>")


REMINDER_NOTE = "Records a local acknowledgement only; nothing is changed in Leonardo or Salesforce."


def _reminder(text: str, action: str, reference: str, button: str) -> str:
    """One open follow-up as a list item (the shared REMINDER_NOTE is shown once per list)."""
    return ("<li><span class='chip chip-warn'>To do</span><span class='what'>" + escape(text) + "</span>"
            "<form method='post' action='" + action + "'><input type='hidden' name='reference' value='"
            + escape(reference) + "'><button type='submit' class='ghost sm'>" + escape(button) + "</button></form></li>")


def _reminder_items(reference: str, route: str | None, record: dict[str, str] | None,
                    core_plus_present: bool) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str, str]], str]:
    """Shared follow-up state: (open items, done items, read-error note).

    Open items are (kind, text, POST action, button label); done items (kind, label, when). The same rules feed the
    Surface Follow-ups card and the "What to do now" card, so the buttons and their guards stay identical.
    """
    try:
        reminders = load_attended_reminders().get(reference, {})
        error = ""
    except ReadUnavailable:
        reminders = {}
        error = "<p class='note'>The local reminders file could not be read; reminders are shown until it is repaired.</p>"
    open_items: list[tuple[str, str, str, str]] = []
    if route == SURFACE_ENGINE and core_plus_present and not reminders.get(REMINDER_FIELDS["ce_enabled"]):
        open_items.append(("ce_enabled", CE_REMINDER_TEXT, "/attended/mark-ce-enabled", "Mark CE enabled"))
    verified = (record is not None and record.get("route") in SURFACE_ROUTES and record.get("result") == "readback_verified")
    if verified and not reminders.get(REMINDER_FIELDS["scan_settings_off"]):
        open_items.append(("scan_settings_off", SCAN_REMINDER_TEXT, "/attended/mark-scan-settings-off",
                           "Mark scan settings turned off"))
    if verified and not reminders.get(REMINDER_FIELDS["operator_assigned"]):
        open_items.append(("operator_assigned", OPERATOR_REMINDER_TEXT, "/attended/mark-operator-assigned",
                           "Mark Operator Account assigned"))
    done_items = [(kind, label, reminders[REMINDER_FIELDS[kind]])
                  for kind, label in (("scan_settings_off", "Scan settings marked off"),
                                      ("ce_enabled", "Credential Exposure marked enabled"),
                                      ("operator_assigned", "Operator Account marked assigned"))
                  if reminders.get(REMINDER_FIELDS[kind])]
    return open_items, done_items, error


def _scope_summary_line(evaluation: SurfaceScopePreflight) -> str:
    scope = evaluation.scope or {}
    return escape(f"{str(scope.get('tier', '')).title()} · {scope.get('scanning_interval', '')} · "
                  f"{scope.get('licensed_subdomains', '')} licensed subdomains · "
                  f"{scope.get('license_start', '')} → {scope.get('license_end', '')}")


def _surface_onboard_section(reference: str, route: str = SURFACE_ENGINE, lede: str = "") -> str:
    """Render the Surface-only (Case 1) Onboard card: scope review + one Start action.

    Mirrors the CE-only card (chip, outcome banner, reset for failed runs) and
    adds the counts-only scope summary, the required revision-bound scope
    review, and the local Scan-now / Credential Exposure reminders (a
    Follow-ups list after the card). ``lede`` is trusted static HTML.
    """
    parts = _surface_onboard_parts(reference, route)
    return _onboard_card(
        "Surface + Credential Exposure onboarding" if route == CASE3_ENGINE else "Surface onboarding", str(parts["chip"]),
        ("<p class='lede'>" + lede + "</p>" if lede else "") + str(parts["runner_note"]) + str(parts["blockers"])
        + str(parts["scope_html"]) + str(parts["start"]) + str(parts["reset"]) + str(parts["meta"])) + str(parts["followups"])


def _surface_onboard_parts(reference: str, route: str = SURFACE_ENGINE) -> dict[str, Any]:
    """The pieces of the Surface / Case 3 onboard card (the CO page composes them; ``state`` classifies the run).

    ``state`` is one of: uncertain, running, duplicate, failed, onboarded, ready, blocked. ``scope_review`` is the
    counts-only scope (with its "Scope to review" heading) shown only next to the Start form; ``scope_fold`` is the
    same scope once Start is not offered.
    """
    ref = escape(reference)
    evaluation = evaluate_surface_fill_preflight(reference, route)
    try:
        state = load_runner_state()
    except RunnerStateUnavailable:
        state = None
    record = state.get(reference) if state is not None else None
    same_revision = (record is not None and bool(evaluation.source_revision)
                     and record.get("source_revision") == evaluation.source_revision)
    if record is not None and record.get("result") == "readback_verified":
        status_chip = "<span class='chip chip-ok'>Onboarded</span>"
    elif not evaluation.eligible_for_fill_review:
        status_chip = "<span class='chip chip-bad'>Blocked</span>"
    elif same_revision and record.get("result") in DUPLICATE_RESULTS:
        status_chip = "<span class='chip chip-bad'>Duplicate</span>"
    elif same_revision and record.get("result"):
        status_chip = "<span class='chip chip-bad'>Failed</span>"
    elif same_revision:
        status_chip = "<span class='chip chip-warn'>Running</span>"
    else:
        status_chip = "<span class='chip chip-info'>Ready</span>"
    runner_note = ""
    banner = ""
    start_action = ""
    state_name = "ready"
    if same_revision and record.get("result"):
        kind, message = RUNNER_RESULT_MESSAGES.get(record["result"], ("blocked", "Result code <code>" + escape(record["result"]) + "</code>."))
        banner = _outcome_banner(kind, message, record["result"], record.get("completed_on"), compact=True)
        runner_note = banner + _entered_license_note(record) + _production_marker_for(reference)
        state_name = ("onboarded" if record["result"] == "readback_verified"
                      else "duplicate" if record["result"] in DUPLICATE_RESULTS else "failed")
    elif same_revision:
        runner_note = _running_note(ref, record)
        state_name = "running"
    elif record is not None and record.get("result") == "readback_verified":
        # A verified tenant exists; a later source revision never re-creates it.
        banner = _outcome_banner("success", RUNNER_RESULT_MESSAGES["readback_verified"][1],
                                 "readback_verified", record.get("completed_on"), compact=True)
        runner_note = banner + _entered_license_note(record)
        state_name = "onboarded"
    elif start_blocker(record) == "run_in_progress":
        runner_note = _START_IN_PROGRESS_NOTE + _progress_button(ref)
        state_name = "running"
    else:
        if record is not None:
            runner_note = ("<p class='note'>Previous attended run for a different source revision: <code>" +
                           escape(record.get("result", "no result recorded")) + "</code></p>")
        start_action = _surface_start_form(evaluation)
        state_name = "ready" if start_action else "blocked"
    blockers = ""
    if evaluation.blockers:
        blockers = ("<ul class='blockers'>" + "".join(
            "<li><b>Blocked:</b> " + RUNNER_RESULT_MESSAGES.get(code, ("blocked", ""))[1]
            + " <code>" + escape(code) + "</code></li>" for code in evaluation.blockers)
            + "".join("<li><b>" + escape(detail) + "</b></li>" for detail in evaluation.blocker_details) + "</ul>")
    open_items, done_items, reminder_error = _reminder_items(reference, route, record, evaluation.core_plus_present)
    reminder_html = "".join(_reminder(text, action, reference, button) for _kind, text, action, button in open_items)
    for _kind, label, when in done_items:
        reminder_html += ("<li class='done'><span class='chip chip-ok'>Done</span><span class='what'>" + label
                          + " on " + escape(when) + ".</span></li>")
    followups = ""
    if reminder_html or reminder_error:
        followups = ("<section class='card' aria-labelledby='followups-title'><div class='card-head'>"
                     "<h2 id='followups-title' class='pill'>Follow-ups</h2><span class='note'>" + REMINDER_NOTE + "</span></div>"
                     + reminder_error + ("<ul class='todo'>" + reminder_html + "</ul>" if reminder_html else "") + "</section>")
    reset_action = ""
    if create_uncertain(record):
        runner_note = _uncertain_banner(ref, record)
        banner = runner_note
        start_action = ""
        state_name = "uncertain"
    elif record is not None and record.get("result") and record["result"] != "readback_verified":
        reset_action = _reset_form(ref, record)
    scope_html = _surface_scope_facts(evaluation)
    scope_review = scope_fold = ""
    if scope_html and start_action:
        scope_review = "<h3 class='sub-h'>Scope to review</h3>" + scope_html
        scope_html = scope_review
    elif scope_html:
        scope_fold = ("<details class='fold'><summary>Planned scope · " + _scope_summary_line(evaluation)
                      + "</summary>" + scope_html + "</details>")
        scope_html = scope_fold
    revision = evaluation.source_revision or "unavailable"
    return {"state": state_name, "chip": status_chip, "runner_note": runner_note, "banner": banner,
            "blockers": blockers, "scope_html": scope_html, "scope_review": scope_review, "scope_fold": scope_fold,
            "start": start_action, "reset": reset_action, "record": record, "evaluation": evaluation,
            "same_revision": same_revision, "followups": followups,
            "meta": ("<p class='meta-line'>Route <code>" + escape(route) + "</code> · Source revision <code>"
                     + escape(revision) + "</code></p>")}


def page_ce_only_runner_status(state: dict[str, dict[str, str]] | None, reference: str) -> str:
    """Show the attended runner outcome; auto-refresh until a result is recorded."""
    ref = escape(reference)
    record = state.get(reference) if state is not None else None
    if record is None:
        refresh = ""
        body = ("<p>No attended run is recorded for " + ref + ".</p>"
                "<p><a href='/co/" + ref + "'>Return to " + ref + " and run the fill preflight.</a></p>")
    elif "result" not in record:
        started = record.get("started_on", "unknown")
        refresh = "<meta http-equiv='refresh' content='5'>"
        bar = _progress_bar(ref, record)
        if record.get("route") in RENEWAL_ENGINES:
            body = ("<div class='card-head'><span class='pill'>Renewal in progress</span><span class='chip chip-warn'>Running</span></div>"
                    "<p>Started at <code>" + escape(started) + "</code>. This page refreshes every 5 seconds.</p>"
                    "<p class='note'>In the automation Chrome window, complete SSO/MFA if prompted. The runner creates the Dev mirror "
                    "if it is missing, plans the renewal (dry run), and applies it. Leonardo Development only; "
                    "it never updates Salesforce.</p>" + bar)
        else:
            body = ("<div class='card-head'><span class='pill'>Onboarding in progress</span><span class='chip chip-warn'>Running</span></div>"
                    "<p>Started at <code>" + escape(started) + "</code>. This page refreshes every 5 seconds.</p>"
                    "<p class='note'>In the automation Chrome window, complete SSO/MFA if prompted. The runner first checks Leonardo "
                    "for an existing tenant, then fills and confirms the Add Account form. It never updates Salesforce.</p>" + bar)
    else:
        result = record["result"]
        completed = record.get("completed_on", "unknown")
        kind, message = runner_result_message(result)
        # A finished run returns the operator to the CO page, where the same
        # green/red outcome is shown; the link works immediately.
        refresh = "<meta http-equiv='refresh' content='" + str(RUNNER_REDIRECT_SECONDS) + ";url=/co/" + ref + "'>"
        banner = (_uncertain_banner(ref, record) if create_uncertain(record)
                  else _renewal_uncertain_banner(ref, record) if renewal_uncertain(record)
                  else _outcome_banner(kind, message, result, completed))
        body = (banner +
                "<p class='note'>Returning to " + ref + " in " + str(RUNNER_REDIRECT_SECONDS) + " seconds. "
                "<a href='/co/" + ref + "'>Return to " + ref + " now</a></p>")
    return _app_shell(reference + " onboarding",
                      "<a class='crumb' href='/co/" + ref + "'>&larr; " + ref + "</a>"
                      "<div class='page-head'><h1>" + ref + "</h1></div>"
                      "<section class='card'>" + body + "</section>", refresh=refresh, active="onboardings")


START_BLOCKED_MESSAGES = {
    "revision_acknowledgement_missing": "The source-revision acknowledgement is missing. No browser was launched.",
    "preflight_blocked": "The preflight is blocked. No browser was launched.",
    "source_revision_changed": "The source revision changed since this page was shown. No browser was launched. Review it again.",
    "scope_changed": "The computed scope changed since it was reviewed. No browser was launched. Review the scope again.",
    "run_in_progress": "A run for this CO (an earlier source revision) has not reported a result yet. No new run was started.",
    "tenant_already_verified": "A tenant was already created and verified for this CO. No new run was started.",
    "create_uncertain": "An earlier run may have created a tenant. Verify it read-only on the CO page first. No new run was started.",
    "renewal_uncertain": "An earlier renewal run may have written to Leonardo Development. Verify it on the CO page first. No new run was started.",
    "run_in_progress_other": "Another attended run is in progress (one run at a time). No new run was started.",
    "renewal_acknowledgement_missing": "Both confirmations are required. No browser was launched.",
    "renewal_nonce_invalid": "The start confirmation expired, was already used, or the CO changed. Reload the CO page and try again. No browser was launched.",
    "renewal_not_approved": "Onboarding Approval Status is not Approved. No browser was launched.",
    "renewal_not_supported": "This CO is not a Case 4-6 renewal. No browser was launched.",
}


# --- Tenant inventory page (2026-10-03) --------------------------------------
# Shows the allow-listed snapshot written by the runner's --export-tenants.
# Informational only: a create or duplicate decision always re-checks Leonardo live.
INVENTORY_STALE_AFTER = timedelta(hours=6)
INVENTORY_DISPLAY_MAX_AGE = timedelta(days=3650)  # load any snapshot; staleness is shown, not hidden
INVENTORY_ERROR_TEXT = {
    "inventory_snapshot_missing": "No tenant inventory has been exported yet.",
    "inventory_snapshot_tampered": "The latest snapshot failed its integrity check (sha256, name, or counts). It is not shown.",
    "inventory_snapshot_schema_version": "The latest snapshot was written by a different version. Refresh the inventory.",
    "inventory_snapshot_wrong_environment": "The latest snapshot belongs to another environment. It is not shown.",
}


def inventory_root() -> Path:
    return inventory.default_root()


def start_attended_inventory_export() -> bool:
    return _start_runner_mode("--export-tenants", "--env", "dev", "--with-sweeps", "--csv")


def _inventory_date(value: object) -> str:
    if type(value) is int:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return "—"
    return escape(value[:10]) if isinstance(value, str) and value else "—"


def _inventory_duration(value: object) -> str:
    if type(value) is not int or value < 0:
        return "—"
    minutes = value // 60000
    return f"{minutes // 60} h {minutes % 60:02d} m"


PRECHECK_REASON_TEXT = {"tenant_name": "same tenant name", "primary_domain": "same primary domain",
                        "alternate_domain": "a CO domain is this tenant's alternate domain"}


def duplicate_precheck_form(reference: str) -> str:
    return ("<form method='post' action='/attended/duplicate-precheck'><input type='hidden' name='reference' value='"
            + escape(reference) + "'><button class='ghost' type='submit'>Duplicate pre-check (DEV inventory)</button></form>")


_START_DUPLICATE_NOTE = (
    "<p class='note'>Checks the DEV tenant inventory, then Leonardo itself, for an existing tenant (name, primary "
    "domain, alternate domains) first. If one exists, nothing is created and this CO is marked as a duplicate.</p>")


def _duplicate_precheck_row(reference: str) -> str:
    """The optional read-only pre-check, secondary to Start (a separate form; forms cannot nest)."""
    return ("<div class='start-row'>" + duplicate_precheck_form(reference)
            + "<span class='note'>Optional and read-only; Start runs the live duplicate check anyway.</span></div>")


def _created_date(value: object) -> str:
    """A tenant's created date as YYYY-MM-DD (UTC): epoch milliseconds (Dev) or an ISO string (Redash); else "—"."""
    try:
        if type(value) is int:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", value):
            return value[:10]
    except (OverflowError, OSError, ValueError):
        pass
    return "—"


def _clone_precheck_section(clone: object) -> str:
    """The production duplicate check: whether the CO already has a tenant in production (Redash clone)."""
    if not isinstance(clone, dict):
        return ""
    title = "<h2 class='section-title'>Production duplicate check</h2>"
    result = clone.get("result")
    if result == "duplicate_production_clone_match":
        rows = "".join(
            "<tr><td>" + escape(str(m.get("account_name") or "")) + "</td><td><code>" + escape(str(m.get("id") or ""))
            + "</code></td><td>" + escape(_created_date(m.get("created"))) + "</td><td>"
            + escape(", ".join(PRECHECK_REASON_TEXT.get(r, r) for r in m.get("reasons") or ()))
            + ("" if not m.get("is_deleted") else " <span class='chip chip-warn'>deleted</span>") + "</td></tr>"
            for m in clone.get("matches") or () if isinstance(m, dict))
        return (title + "<div class='banner banner-warn'><strong>This CO already has a tenant in production.</strong> "
                "A production onboarding of it would be blocked as a duplicate.</div><div class='qh-table inv'><table>"
                "<thead><tr><th>Tenant</th><th>ID</th><th>Created</th><th>Why</th>"
                "</tr></thead><tbody>" + rows + "</tbody></table></div>"
                "<p class='note'>Production clone data as of " + escape(str(clone.get("captured_at") or "—")) + ".</p>")
    if result == "production_clone_no_match":
        return (title + "<p class='note'>No match among " + escape(str(clone.get("tenants_checked"))) + " production "
                "tenants (data as of " + escape(str(clone.get("captured_at") or "—")) + "). This is not a clearance: "
                "the clone can be up to a day behind, and a production onboarding still runs its own live check.</p>")
    return (title + "<div class='banner banner-warn'><strong>No answer:</strong> the production clone is not usable "
            "(<code>" + escape(str(clone.get("reason") or result))
            + "</code>). A production onboarding would be blocked until it is (fail closed).</div>")


def page_duplicate_precheck(reference: str, report: dict[str, object]) -> str:
    """Result of the read-only pre-check; a "no match" is never a clearance."""
    ref = escape(reference)
    result = str(report.get("result"))
    label = escape(str(report.get("environment_label") or inventory.ENVIRONMENTS["dev"].label))
    if result == "inventory_match":
        rows = "".join(
            "<tr><td>" + escape(str(m.get("account_name") or "")) + "</td><td><code>" + escape(str(m.get("id") or ""))
            + "</code></td><td>" + escape(", ".join(PRECHECK_REASON_TEXT.get(r, r) for r in m.get("reasons") or ()))
            + ("" if not m.get("is_deleted") else " <span class='chip chip-warn'>deleted</span>") + "</td></tr>"
            for m in report.get("matches") or () if isinstance(m, dict))
        body = ("<div class='banner banner-warn'><strong>Possible duplicate.</strong> Start will stop before opening a "
                "browser while the inventory shows this match.</div><div class='qh-table inv'><table><thead><tr>"
                "<th>Tenant</th><th>ID</th><th>Why</th></tr></thead><tbody>" + rows + "</tbody></table></div>")
    elif result == "inventory_no_match":
        body = ("<div class='banner'><strong>No match</strong> among " + escape(str(report.get("tenants_checked")))
                + " tenants. This is not a clearance: Start still runs the live duplicate check in Leonardo.</div>"
                + ("" if report.get("alternate_domains_available") else
                   "<p class='note'>This inventory predates alternate domains; refresh it to include them.</p>"))
    elif result == "inventory_unavailable":
        body = ("<div class='banner banner-warn'>No usable tenant inventory (<code>" + escape(str(report.get("reason")))
                + "</code>). Refresh it on the DevOps tab. Start still runs the live duplicate check.</div>")
    else:
        body = "<div class='banner banner-warn'>The CO source could not be read (<code>" + escape(result) + "</code>).</div>"
    main_html = ("<a class='crumb' href='/co/" + ref + "'>&larr; " + ref + "</a><div class='page-head'><h1>Duplicate "
                 "pre-check</h1><span class='chip chip-info'>" + label + "</span></div><section class='card'>" + body
                 + _clone_precheck_section(report.get("production_clone"))
                 + "<p class='note'>Inventory captured " + escape(str(report.get("captured_at") or "—")) + ". Rules: same "
                 "tenant name or primary domain (as the live check), plus the CO's domains against each tenant's "
                 "alternate domains.</p></section>")
    return _app_shell(reference + " duplicate pre-check", main_html, active="onboardings")


INVENTORY_SCAN_FILTERS = (("", "All"), ("COMPLETED", "Completed"), ("RUNNING", "Running"),
                          ("INCOMPLETE", "Incomplete"), ("NONE", "No status"))
INVENTORY_CO_FILTERS = (("", "Any CO"), ("linked", "With CO"), ("none", "No CO"))
INVENTORY_SORTS = frozenset({"name", "co", "licence", "end", "domain", "scan", "status"})
_SCAN_CHIP = {"COMPLETED": "chip-ok", "RUNNING": "chip-warn", "INCOMPLETE": "chip-bad"}


def _inventory_sort_date(value: object) -> str:
    if type(value) is int:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return value[:10] if isinstance(value, str) else ""


def _inventory_sort_value(tenant: dict, key: str, co_ref: str | None) -> str:
    licence, scan = tenant.get("license") or {}, tenant.get("scan") or {}
    if key == "co":
        return co_ref or ""
    if key == "licence":
        return str(licence.get("type") or "").casefold()
    if key == "end":
        return _inventory_sort_date(licence.get("expiration_date"))
    if key == "domain":
        return str(tenant.get("account_domain") or "").casefold()
    if key == "scan":
        return str(scan.get("last_recon_scan_utc") or "")
    if key == "status":
        return str(scan.get("status") or "")
    return str(tenant.get("account_name") or "").casefold()


INVENTORY_ENVIRONMENTS = ("dev", "prod-clone")
PROD_CLONE_MISSING_TEXT = ("No production-clone inventory has been collected yet. The collector "
                           "(tools/redash_inventory_collector.py --collect) writes it; it needs no sign-in.")


def render_inventory(query: str = "", notice: str = "", *, sort: str = "", direction: str = "",
                     scan: str = "", co: str = "", env: str = "dev", top: str = "") -> str:
    """Server-rendered tenant table (the CSP blocks scripts); sort and filters are query parameters.

    One environment per page: "dev" is Leonardo Development > Tenants (/inventory), "prod-clone" is Production > Tenants (/prod/tenants).
    ``top`` is extra HTML shown above the table (the Tenants tab's production-gate note).
    """
    now = datetime.now(timezone.utc)
    env = env if env in INVENTORY_ENVIRONMENTS else "dev"
    clone = env == "prod-clone"
    environment = inventory.ENVIRONMENTS[env]
    refresh_form = "" if clone else ("<form method='post' action='/attended/inventory-refresh'><button type='submit'>"
                                     "Refresh inventory</button></form>")
    base = "/prod/tenants" if clone else "/inventory"
    active = "prod_tenants" if clone else "inventory"
    title = "Tenants — Production (Redash clone)" if clone else "Tenants — Leonardo Development"
    head = ("<div class='page-head'><h1>" + escape(title) + "</h1><span class='chip chip-info'>"
            + escape(environment.label) + "</span>" + refresh_form + "</div>")
    if clone:
        note = ("<p class='note'>Read-only copy of the <b>production</b> tenant list through Redash (saved query 251 on the "
                "cloned database), with personal data removed. It refreshes on a schedule and needs no sign-in. It is "
                "the Start gate: a match stops an onboarding, a missing match never clears one, and Start also "
                "re-checks Leonardo live. Dashboard CO links are not shown for production tenants.</p>")
    else:
        note = ("<p class='note'>Read-only copy of Leonardo Development's tenant list, with personal data removed (no user "
                "names, emails, phones, or domain lists). It is informational: Start always re-checks Leonardo live for "
                "duplicates. Refresh also re-validates every onboarded CO from the same read.</p>")
    started = ("<div class='banner'><strong>Export started.</strong> It runs read-only in the automation browser; reload "
               "this page in a minute.</div>" if notice == "started" and not clone else "")
    try:
        payload = inventory.load_latest(inventory_root(), env, max_age=INVENTORY_DISPLAY_MAX_AGE, now=now)
    except inventory.InventoryError as error:
        text = (PROD_CLONE_MISSING_TEXT if clone and error.reason == "inventory_snapshot_missing"
                else INVENTORY_ERROR_TEXT.get(error.reason, error.reason))
        body = "<section class='card'><p>" + escape(text) + "</p></section>"
        return _app_shell(title, head + top + started + body + note, active=active)
    try:
        readbacks = {} if clone else attended_leonardo_readbacks()
    except ReadUnavailable:
        readbacks = {}
    matched = inventory.match_readbacks(payload, readbacks)
    co_by_tenant = {tenant_id: ref for ref, tenant_id in matched["by_reference"].items()
                    if tenant_id not in (None, "conflict")}
    conflicts = sorted(ref for ref, tenant_id in matched["by_reference"].items() if tenant_id == "conflict")
    age = timedelta(seconds=int(payload.get("age_seconds", 0)))
    stale = age > INVENTORY_STALE_AFTER
    drift = payload.get("schema_drift") if isinstance(payload.get("schema_drift"), dict) else {}
    drift_count = sum(len(drift.get(key) or []) for key in ("unknown", "missing", "type_changed"))
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    newest = source.get("data_latest_modified")
    summary = ("<div class='banner" + (" banner-warn" if stale else "") + "'><strong>"
               + escape(str(payload.get("environment_label") or environment.label))
               + "</strong> · " + ("Data as of " if clone else "Captured ") + escape(str(payload.get("captured_at")))
               + (" · newest change in the source " + escape(str(newest)) if clone and newest else "")
               + " (" + str(int(age.total_seconds() // 3600)) + " h ago" + ("; stale, " + ("check that the collector is running" if clone else "refresh before relying on it") if stale else "")
               + ") · " + escape(str(payload.get("row_count"))) + " tenants (" + escape(str(payload.get("deleted_count")))
               + " deleted, " + escape(str(payload.get("uuid_missing_count", 0))) + " without UUID) · "
               + str(drift_count) + " schema change(s)"
               + (" · <strong>ID conflict for " + escape(", ".join(conflicts)) + "</strong>: re-check live" if conflicts else "")
               + "</div>")
    needle = " ".join(query.split()).casefold()[:80]
    sort = sort if sort in INVENTORY_SORTS else "name"
    descending = direction == "desc"
    scan = scan if scan in {value for value, _ in INVENTORY_SCAN_FILTERS} else ""
    co = co if co in {value for value, _ in INVENTORY_CO_FILTERS} else ""
    selected = []
    for tenant in payload.get("tenants") or []:
        name = str(tenant.get("account_name") or "")
        ref = co_by_tenant.get(tenant.get("id"))
        status = (tenant.get("scan") or {}).get("status")
        if needle and needle not in name.casefold():
            continue
        if scan and (status or "NONE").upper() != scan:
            continue
        if (co == "linked" and not ref) or (co == "none" and ref):
            continue
        selected.append((tenant, ref, _inventory_sort_value(tenant, sort, ref)))
    name_key = lambda item: str(item[0].get("account_name") or "").casefold()  # noqa: E731
    filled = sorted((item for item in selected if item[2]), key=lambda item: (item[2], name_key(item)), reverse=descending)
    empty = sorted((item for item in selected if not item[2]), key=name_key)  # blanks always last
    rows_html = ""
    mirrors = frozenset() if clone else dev_mirror_ids()
    for tenant, ref, _value in filled + empty:
        licence = tenant.get("license") or {}
        tenant_scan = tenant.get("scan") or {}
        status = tenant_scan.get("status")
        co_cell = ("<a href='/co/" + escape(ref) + "'>" + escape(ref) + "</a>") if ref else "<span class='muted'>no CO</span>"
        state = "deleted" if tenant.get("is_deleted") else ("enabled" if tenant.get("enabled") else "disabled")
        alt = tenant.get("alternate_domains")
        domain = escape(str(tenant.get("account_domain") or "—"))
        if alt:
            domain += "<span class='sub'>+" + str(len(alt)) + " alternate</span>"
        quota = [licence.get(key) for key in ("assets_number", "domains_number", "subdomains_number")]
        quota_cell = " / ".join("—" if value is None else str(value) for value in quota) if any(
            value is not None for value in quota) else "—"
        scanned = str(tenant_scan.get("last_recon_scan_utc") or "")[:16].replace("T", " ")
        scan_cell = (escape(scanned) + "<span class='sub'>" + _inventory_duration(tenant_scan.get("duration_ms")) + "</span>"
                     if scanned else "<span class='muted'>never</span>")
        status_cell = ("<span class='chip " + _SCAN_CHIP.get(status.upper(), "chip-neutral") + "'>" + escape(status.title()) + "</span>"
                       if status else "<span class='muted'>—</span>")
        interval = str(tenant.get("scanning_interval") or "—").replace("_", " ").casefold()
        spy = tenant.get("spycloud_enabled")  # informational (ON is the default); boolean only
        spy_cell = {True: "<span class='chip chip-ok'>ON</span>", False: "<span class='chip chip-warn'>OFF</span>"}.get(
            spy, "<span class='muted'>—</span>")
        rows_html += (
            "<tr><td class='stick'>" + escape(str(tenant.get("account_name") or "")) + "<span class='sub'><code>"
            + escape(str(tenant.get("id") or "")) + "</code>"
            + ("" if tenant.get("account_uuid") else " <span class='chip chip-warn'>no UUID</span>")
            + (" " + DEV_MIRROR_CHIP if tenant.get("id") in mirrors else "")
            + "</span></td><td>" + co_cell
            + "</td><td>" + escape(str(licence.get("type") or "—")) + "<span class='sub'>"
            + _inventory_date(licence.get("start_date")) + " → " + _inventory_date(licence.get("expiration_date"))
            + "</span></td><td class='dom' title='" + escape(str(tenant.get("account_domain") or ""), quote=True) + "'>"
            + domain + "</td><td class='num'>" + escape(quota_cell) + "</td><td>" + scan_cell
            + "</td><td>" + status_cell + "</td><td>" + escape(interval) + "</td><td>" + spy_cell + "</td><td>" + state + "</td></tr>")

    def link(**changes: str) -> str:
        params = {"q": query[:80], "sort": sort, "dir": "desc" if descending else "asc", "scan": scan, "co": co,
                  **changes}
        return base + "?" + urlencode({key: value for key, value in params.items() if value})

    def header(key: str, label: str, numeric: bool = False) -> str:
        active = key == sort
        arrow = (" ▼" if descending else " ▲") if active else ""
        target = link(sort=key, dir="desc" if active and not descending else "asc")
        aria = (" aria-sort='" + ("descending" if descending else "ascending") + "'") if active else ""
        return ("<th scope='col'" + (" class='num'" if numeric else "") + aria + "><a class='sortlink' href='"
                + escape(target) + "'>" + escape(label) + arrow + "</a></th>")

    def chips(options: tuple, current: str, param: str) -> str:
        return "".join("<a class='fchip" + (" on" if value == current else "") + "' href='" + escape(link(**{param: value}))
                       + "'" + (" aria-current='true'" if value == current else "") + ">" + escape(label) + "</a>"
                       for value, label in options)

    hidden = "".join("<input type='hidden' name='" + key + "' value='" + escape(value) + "'>"
                     for key, value in (("sort", sort if sort != "name" else ""), ("dir", direction if direction == "desc" else ""),
                                        ("scan", scan), ("co", co)) if value)
    search = ("<form method='get' action='" + base + "' class='filterbar'><input name='q' value='" + escape(query[:80])
              + "' placeholder='Search tenant name' aria-label='Search tenant name'>" + hidden
              + "<button class='ghost' type='submit'>Search</button><span class='fgroup' aria-label='Scan status'>"
              + chips(INVENTORY_SCAN_FILTERS, scan, "scan") + "</span><span class='fgroup' aria-label='Onboarding'>"
              + chips(INVENTORY_CO_FILTERS, co, "co") + "</span><span class='count'><b>" + str(len(selected))
              + "</b> of " + str(len(payload.get("tenants") or [])) + " tenants</span></form>")
    table = ("<section class='card inv-card'>" + search
             + "<div class='inv'><table><thead><tr>" + header("name", "Tenant · ID").replace("<th ", "<th class='stick' ", 1)
             + header("co", "CO") + header("licence", "Licence · start → end") + header("domain", "Domain")
             + "<th scope='col' class='num'><abbr title='Assets / domains / subdomains in the licence'>Quota</abbr></th>"
             + header("scan", "Last scan (UTC)") + header("status", "Scan status") + "<th scope='col'>Interval</th>"
             "<th scope='col'><abbr title='SpyCloud flag of the Leaked Credentials settings; ON is the default, OFF is informational'>SpyCloud</abbr></th>"
             "<th scope='col'>Account</th></tr></thead><tbody>" + rows_html
             + "</tbody></table></div><p class='note'>Last scan times are UTC. Snapshot: "
             + escape(str(payload.get("environment"))) + " folder under %LOCALAPPDATA%\\SurfaceOnboarding\\leonardo-inventory "
             "(CSV beside it).</p></section>")
    return _app_shell(title, head + top + started + summary + table + note, active=active, wide=True)


# ---- Two areas: "Onboarded on Dev" and Production readiness (owner decision 2026-10-07) -----------------------
# Everything below is read-only: GET pages only, no Leonardo or BackOffice session, no refresh button for the clone.
# Dev rows (local run/readback/outcome evidence) and Production rows (the Redash clone) never share a table.
PROD_ACTION_REFRESH = "Refresh clone: "
MATCH_CHIPS = {"exists_matches": ("chip-ok", "Matches"), "exists_differs": ("chip-warn", "Differs"),
               "not_in_production": ("chip-neutral", "Not in production"), "exists_other": ("chip-bad", "Other tenants only"),
               "ambiguous": ("chip-bad", "Ambiguous"), "clone_unavailable": ("chip-warn", "Clone unavailable")}
MATCH_FILTERS = {"matches": ("exists_matches",), "differs": ("exists_differs",), "not_in_production": ("not_in_production",),
                 "blocked": ("exists_other", "ambiguous"), "clone_unavailable": ("clone_unavailable",)}
MATCH_FILTER_LABELS = (("matches", "Matches"), ("differs", "Differs"), ("not_in_production", "Not in production"),
                       ("blocked", "Blocked"), ("clone_unavailable", "Clone unavailable"))
MATCH_STATUS_ORDER = ("exists_other", "ambiguous", "exists_differs", "clone_unavailable", "not_in_production", "exists_matches")
FIELD_RESULT_CHIPS = {"match": ("chip-ok", "Match"), "differs": ("chip-warn", "Differs"), "unknown": ("chip-warn", "Unknown"),
                      "info": ("chip-neutral", "Info"), "na": ("chip-neutral", "Not required"),
                      "not_checked": ("chip-neutral", "Not checked")}
ENGINE_LABELS = {CE_ENGINE: "CE-only", SURFACE_ENGINE: "Surface-only", CASE3_ENGINE: "Surface + CE",
                 "case_4_renew_surface_new_ce": "Case 4 renewal", "case_5_renew_ce_new_surface": "Case 5 renewal",
                 "case_6_renew_both": "Case 6 renewal"}
DEV_STATUS_CHIPS = {"onboarded": ("chip-ok", "Onboarded"), "mirror": ("chip-info", "Dev mirror"),
                    "none": ("chip-neutral", "Not onboarded")}
BEFORE_MIGRATION = (
    "The production match shows Matches or Not in production, with no unresolved duplicate (Other tenants only and "
    "Ambiguous block the migration).",
    "The production clone is fresh: collected within the last 6 hours (" + PRODUCTION_COLLECT_HINT + ").",
    "A separate, explicit owner approval exists for any production BackOffice write; this dashboard never writes there.",
    "The Salesforce account id is not checked yet (the Redash accounts query is pending); compare it by hand.",
    "On the move to production, ask the owner again: Salesforce Onboarding Stage must then be updated.",
)


def _evidence_for(reference: str, state: dict, readbacks: dict, outcomes: dict, mirrors: frozenset[str]) -> list[dict[str, Any]]:
    record = state.get(reference)
    return dev_onboarded_evidence(
        reference, record=record, readback=readbacks.get(reference), outcome=outcomes.get(reference), mirror_ids=mirrors,
        renewal_engines=RENEWAL_ENGINES, uncertain=bool(create_uncertain(record) or renewal_uncertain(record)))


def _evidence_inputs() -> tuple[dict, dict, dict, frozenset[str]]:
    try:
        state = load_runner_state()
    except RunnerStateUnavailable:
        state = {}
    try:
        readbacks = attended_leonardo_readbacks()
    except ReadUnavailable:
        readbacks = {}
    return state, readbacks, attended_renewal_outcomes(), dev_mirror_ids()


def dev_evidence_for(reference: str) -> list[dict[str, Any]]:
    """Local, verified-only Dev evidence for one CO (see integration/onboarding/dev_onboarded.py)."""
    return _evidence_for(reference, *_evidence_inputs())


def dev_evidence_map() -> dict[str, list[dict[str, Any]]]:
    """Evidence per CO that has any (a CO needs a Dev readback to appear here)."""
    state, readbacks, outcomes, mirrors = _evidence_inputs()
    found = {ref: _evidence_for(ref, state, readbacks, outcomes, mirrors) for ref in sorted(readbacks)}
    return {ref: items for ref, items in found.items() if items}


def dev_onboarded_refs(evidence: dict[str, list[dict[str, Any]]] | None = None) -> list[str]:
    evidence = dev_evidence_map() if evidence is None else evidence
    return [ref for ref, items in evidence.items() if dev_status(items) == "onboarded"]


@_display_cached
def production_match_display(reference: str, product: str = "", onboarding_type: str = "") -> dict[str, Any]:
    """The production match of one CO as a cached display read (read-only Salesforce source + clone snapshot)."""
    return production_match_for(reference, product or None, onboarding_type or None)


def _unreadable_match(reason: str = "co_source_unreadable") -> dict[str, Any]:
    return {"status": "clone_unavailable", "reason": reason, "fields": [], "differs": [], "candidates": [], "target": None,
            "captured_at": None, "age_seconds": None, "tenants_checked": 0, "route": None}


def _match_of(reference: str, product: str | None, onboarding_type: str | None) -> dict[str, Any]:
    try:
        return production_match_display(reference, product or "", onboarding_type or "")
    except Exception:  # noqa: BLE001 - a failed read never clears a CO: it shows as unavailable
        return _unreadable_match()


def _match_chip(status: str) -> str:
    css, label = MATCH_CHIPS.get(status, ("chip-neutral", status))
    return "<span class='chip " + css + "'>" + escape(label) + "</span>"


def _dev_status_chip(items: list[dict[str, Any]]) -> str:
    css, label = DEV_STATUS_CHIPS[dev_status(items)]
    return "<span class='chip " + css + "'>" + escape(label) + "</span>"


def _prod_action(result: dict[str, Any]) -> str:
    status, reason = result["status"], str(result.get("reason") or "")
    if status == "exists_differs":
        return "Review fields"
    if status in ("exists_other", "ambiguous"):
        return "Resolve duplicate"
    if status == "clone_unavailable":
        if reason.startswith("inventory_snapshot") or reason == "alternate_domains_missing":
            return PROD_ACTION_REFRESH + "<code>" + escape(PRODUCTION_COLLECT_HINT) + "</code>"
        return "Check CO source data"
    return "None"


def _prod_action_rank(result: dict[str, Any]) -> int:
    action = _prod_action(result)
    return 0 if action == "Resolve duplicate" else 1 if action == "Review fields" else 3 if action == "None" else 2


def _differing_text(result: dict[str, Any]) -> str:
    labels = {row["code"]: row for row in result.get("fields") or ()}
    parts = [escape(labels[code]["label"]) + (" (unknown)" if labels[code]["result"] == "unknown" else "")
             for code in result.get("differs") or () if code in labels]
    if parts:
        return ", ".join(parts)
    reason = str(result.get("reason") or "")
    return "<span class='sub'>" + escape(reason) + "</span>" if reason and result["status"] != "exists_matches" else "—"


def _clone_info() -> dict[str, Any]:
    """Age and trust of the production clone for display (any age is loaded; staleness is shown, not hidden)."""
    try:
        payload = inventory.load_latest(inventory_root(), "prod-clone", max_age=INVENTORY_DISPLAY_MAX_AGE,
                                        now=datetime.now(timezone.utc))
    except inventory.InventoryError as error:
        return {"reason": error.reason}
    age = int(payload.get("age_seconds") or 0)
    return {"captured_at": str(payload.get("captured_at") or ""), "age_seconds": age,
            "trusted": age <= inventory.PRODUCTION_GATE_MAX_AGE.total_seconds(),
            "label": str(payload.get("environment_label") or "")}


def _age_words(seconds: int) -> str:
    hours, minutes = divmod(max(0, seconds) // 60, 60)
    return (str(hours) + " h " + str(minutes) + " min") if hours else (str(minutes) + " min")


def _clone_line(info: dict[str, Any]) -> str:
    if "reason" in info:
        return ("Production clone unusable: <code>" + escape(info["reason"]) + "</code>. Refresh it with <code>"
                + escape(PRODUCTION_COLLECT_HINT) + "</code>.")
    return ("Production clone captured " + escape(info["captured_at"]) + " · data age " + _age_words(info["age_seconds"])
            + ("" if info["trusted"] else " · <strong>older than 6 h: not trusted</strong>")
            + (" · " + escape(info["label"]) if info["label"] else ""))


def _co_link(reference: str, env: str = "") -> str:
    return ("<a class='co' href='/co/" + escape(reference) + ("?env=" + env if env else "") + "'>" + escape(reference) + "</a>")


def _route_label(reference: str, row: dict[str, str | None] | None, record: dict[str, str] | None) -> str:
    engine = (record or {}).get("route")
    if engine is None and row is not None:
        case = renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
        engine = case[0] if case is not None else route_for(row)
    label = ENGINE_LABELS.get(engine or "", "Route unknown" if record is None and row is None else "CE-only")
    return "<span class='chip chip-info'>" + escape(label) + "</span>"


def _prod_scope(all_open: bool) -> tuple[list[tuple[str, dict[str, str | None] | None]], dict[str, list[dict[str, Any]]], str]:
    """(CO, queue row or None) in scope, the Dev evidence per CO, and a note. Default: only COs onboarded on Dev."""
    evidence = dev_evidence_map()
    note = ""
    try:
        rows = queue_source_rows()
    except ReadUnavailable:
        rows, note = [], "The open queue could not be read from Salesforce; only COs with local Dev evidence are listed."
    by_ref = {row["Name"]: row for row in rows if row.get("Name")}
    onboarded = dev_onboarded_refs(evidence)
    refs = sorted(set(by_ref) | set(onboarded)) if all_open else sorted(onboarded)
    return [(ref, by_ref.get(ref)) for ref in refs], evidence, note


def _prod_results(scope: list[tuple[str, dict[str, str | None] | None]]) -> list[tuple[str, dict[str, str | None] | None, dict[str, Any]]]:
    for ref, row in scope:  # overlap the read-only source reads (shared, cached display reads)
        prefetch_display_read(production_match_display, ref, (row or {}).get("Onboarding_Product__c") or "",
                              (row or {}).get("Onboarding_Type__c") or "")
    return [(ref, row, _match_of(ref, (row or {}).get("Onboarding_Product__c"), (row or {}).get("Onboarding_Type__c")))
            for ref, row in scope]


def _scope_toggle(base: str, all_open: bool, flt: str) -> str:
    params = {"filter": flt, "all": "" if all_open else "1"}
    href = base + ("?" + urlencode({k: v for k, v in params.items() if v}) if any(params.values()) else "")
    return ("<p class='note'>" + ("Showing every open CO." if all_open else "Showing only COs onboarded on Dev.")
            + " <a href='" + escape(href) + "'>" + ("Show only onboarded on Dev" if all_open else "Show all open COs")
            + " &rarr;</a></p>")


def render_prod_readiness(flt: str = "", all_open: bool = False) -> str:
    """/prod: per-CO production match with filter counts. GET only; no form, no refresh button."""
    flt = flt if flt in MATCH_FILTERS else ""
    scope, evidence, note = _prod_scope(all_open)
    results = _prod_results(scope)
    info = _clone_info()

    def count(key: str) -> int:
        return sum(1 for _r, _w, res in results if res["status"] in MATCH_FILTERS[key])

    def link(key: str) -> str:
        query = urlencode({k: v for k, v in (("filter", key), ("all", "1" if all_open else "")) if v})
        return "/prod" + ("?" + query if query else "")

    chips = ("<a class='fchip" + (" on" if not flt else "") + "' href='" + escape(link("")) + "'>All <b>" + str(len(results))
             + "</b></a>")
    for key, label in MATCH_FILTER_LABELS:
        extra = (" · clone age " + _age_words(info["age_seconds"])) if key == "clone_unavailable" and "age_seconds" in info else ""
        chips += ("<a class='fchip" + (" on" if flt == key else "") + "' href='" + escape(link(key)) + "'>" + escape(label)
                  + " <b>" + str(count(key)) + "</b>" + escape(extra) + "</a>")
    shown = [item for item in results if not flt or item[2]["status"] in MATCH_FILTERS[flt]]
    shown.sort(key=lambda item: (_prod_action_rank(item[2]), item[0]))
    body = ""
    for ref, row, res in shown:
        body += ("<tr><td class='stick'>" + _co_link(ref, "prod") + "</td><td>"
                 + escape((row or {}).get("Account__r.Name") or "—") + "</td><td>" + _route_label(ref, row, None) + "</td><td>"
                 + _dev_status_chip(evidence.get(ref, [])) + "</td><td>" + _match_chip(res["status"]) + "</td><td>"
                 + _differing_text(res) + "</td><td>" + _prod_action(res) + "</td></tr>")
    head = ("<thead><tr><th scope='col' class='stick'>CO</th><th scope='col'>Account</th><th scope='col'>Route</th>"
            "<th scope='col'>Dev status</th><th scope='col'>Production match</th><th scope='col'>Differing fields</th>"
            "<th scope='col'>Action needed</th></tr></thead>")
    table = ("<div class='tbl-wrap'><table class='dense'>" + head + "<tbody>" + body + "</tbody></table></div>" if body
             else "<p class='empty'>No CO in this view" + ("" if all_open else " (only COs onboarded on Dev are listed)") + ".</p>")
    page = ("<div class='page-head'><h1>Production readiness</h1><span class='chip chip-warn'>read-only</span></div>"
            "<p class='prodnote'>Read-only comparison of each CO with the production clone (Redash). Nothing here writes to "
            "production, Leonardo or Salesforce. No tenant found is never a clearance.</p>"
            "<p class='note'>" + _clone_line(info) + "</p><nav class='strip' aria-label='Production match filters'>" + chips
            + "</nav>" + _scope_toggle("/prod", all_open, flt) + ("<p class='note'>" + escape(note) + "</p>" if note else "")
            + "<section class='card tbl-card'>" + table + "</section>")
    return _app_shell("Production readiness", page, active="prod", wide=True)


def render_prod_matches(all_open: bool = False) -> str:
    """/prod/matches: the current match of every CO in scope (no stored history) plus the recorded Start-gate checks."""
    scope, _evidence, note = _prod_scope(all_open)
    results = sorted(_prod_results(scope), key=lambda item: (MATCH_STATUS_ORDER.index(item[2]["status"])
                                                              if item[2]["status"] in MATCH_STATUS_ORDER else 9, item[0]))
    info = _clone_info()
    body = ""
    for ref, row, res in results:
        target = res.get("target") or {}
        body += ("<tr><td>" + _match_chip(res["status"]) + "</td><td class='stick'>" + _co_link(ref, "prod") + "</td><td>"
                 + escape((row or {}).get("Account__r.Name") or "—") + "</td><td>" + _differing_text(res) + "</td><td>"
                 + escape(str(target.get("name") or "—")) + "</td></tr>")
    table = ("<div class='tbl-wrap'><table class='dense'><thead><tr><th scope='col'>Match</th><th scope='col' class='stick'>CO</th>"
             "<th scope='col'>Account</th><th scope='col'>Differing fields / reason</th><th scope='col'>Production tenant</th>"
             "</tr></thead><tbody>" + body + "</tbody></table></div>" if body
             else "<p class='empty'>No CO in this view" + ("" if all_open else " (only COs onboarded on Dev are listed)") + ".</p>")
    try:
        checks = sorted(((ref, check) for ref, check in load_check_state().items() if check.get("kind") == "production_duplicate"),
                        key=lambda item: item[1].get("completed_on") or item[1].get("started_on") or "", reverse=True)
        checks_error = False
    except ValueError:
        checks, checks_error = [], True
    gate_rows = ""
    for ref, check in checks:
        gate_rows += ("<tr><td>" + _co_link(ref, "prod") + "</td><td>"
                      + (_production_chip(check) or "<span class='chip chip-neutral'>No match</span>") + " <code>"
                      + escape(check.get("result") or "—") + "</code></td><td>"
                      + escape((check.get("completed_on") or check.get("started_on") or "—").replace("T", " ")[:19]) + "</td></tr>")
    gates = ("<p class='note'>The recorded production duplicate check could not be read.</p>" if checks_error
             else ("<div class='tbl-wrap'><table class='dense'><thead><tr><th scope='col'>CO</th><th scope='col'>Start-gate result"
                   "</th><th scope='col'>When</th></tr></thead><tbody>" + gate_rows + "</tbody></table></div>" if gate_rows
                   else "<p class='empty'>No production duplicate check has been recorded yet.</p>"))
    page = ("<div class='page-head'><h1>Production matches</h1><span class='chip chip-warn'>read-only</span></div>"
            "<p class='prodnote'>Current match only; nothing is stored. Sorted by status, blocked first.</p>"
            "<p class='note'>" + _clone_line(info) + "</p>" + _scope_toggle("/prod/matches", all_open, "")
            + ("<p class='note'>" + escape(note) + "</p>" if note else "")
            + "<section class='card tbl-card'>" + table + "</section>"
            "<section class='card tbl-card'><div class='card-head'><h2 class='pill'>Latest Start-gate checks</h2></div>"
            "<p class='note'>The latest recorded production duplicate check per CO (written when a Start was stopped by it).</p>"
            + gates + "</section>")
    return _app_shell("Production matches", page, active="prod_matches", wide=True)


def _ids_cell(route: str | None, readback: dict[str, str] | None) -> str:
    mapping = SALESFORCE_ID_MAPPING.get(route or "") or (("surface_account_id", "", "Account ID"), ("account_uuid", "", "Account UUID"))
    out = ""
    for key, _field, _label in mapping:
        value = (readback or {}).get(key)
        short = "UUID" if key == "account_uuid" else "ID"
        out += ("<span class='sub'>" + short + " <code style='user-select:all'>" + escape(value) + "</code></span>"
                if isinstance(value, str) and value else "<span class='sub'>" + short + " not captured</span>")
    return out


def render_dev_onboarded() -> str:
    """/dev/onboarded: COs with VERIFIED Dev evidence (a verified Dev mirror alone does not count). GET only."""
    state, readbacks, outcomes, mirrors = _evidence_inputs()
    evidence = {ref: _evidence_for(ref, state, readbacks, outcomes, mirrors) for ref in sorted(readbacks)}
    onboarded = [ref for ref, items in evidence.items() if dev_status(items) == "onboarded"]
    mirrors_only = sum(1 for items in evidence.values() if dev_status(items) == "mirror")
    note = ""
    try:
        rows = {row["Name"]: row for row in queue_source_rows() if row.get("Name")}
    except ReadUnavailable:
        rows, note = {}, "The open queue could not be read from Salesforce; account names are not shown."
    for ref in onboarded:
        prefetch_display_read(production_match_display, ref, (rows.get(ref) or {}).get("Onboarding_Product__c") or "",
                              (rows.get(ref) or {}).get("Onboarding_Type__c") or "")
    body = ""
    for ref in onboarded:
        row, record, outcome = rows.get(ref), state.get(ref), outcomes.get(ref)
        items = [item for item in evidence[ref] if item["counts"]]
        dated = next((item["on"] for item in items if item.get("on")), None)
        renewal = bool(record and record.get("route") in RENEWAL_ENGINES) or any(i["code"].startswith("renewal_") for i in items)
        case = renewal_case((row or {}).get("Onboarding_Product__c"), (row or {}).get("Onboarding_Type__c"))
        route = (record or {}).get("route") or (case[0] if case is not None else (CE_ENGINE if not renewal else None))
        entered = ("— → " + str(outcome["new_expiration"]) if renewal and outcome and outcome.get("new_expiration")
                   else (str(record["license_start_entered"]) + " → " + str(record["license_end_entered"])
                         if record and record.get("license_start_entered") and record.get("license_end_entered") else "—"))
        match = _match_of(ref, (row or {}).get("Onboarding_Product__c"), (row or {}).get("Onboarding_Type__c"))
        chips = " ".join("<span class='chip chip-ok'>" + escape(item["label"]) + "</span>" for item in items)
        body += ("<tr><td class='stick'>" + _co_link(ref) + "</td><td>" + escape((row or {}).get("Account__r.Name") or "—")
                 + "</td><td>" + _route_label(ref, row, record) + "</td><td>" + escape(dated or "—") + "</td><td>"
                 + _ids_cell(route, readbacks.get(ref)) + "</td><td>" + escape(entered)
                 + "<span class='sub'>entered in Leonardo Development</span></td><td>" + chips
                 + "</td><td><a href='/co/" + escape(ref) + "?env=prod'>" + _match_chip(match["status"]) + "</a></td></tr>")
    head = ("<thead><tr><th scope='col' class='stick'>CO</th><th scope='col'>Account</th><th scope='col'>Route</th>"
            "<th scope='col'>Onboarded</th><th scope='col'>Account ID / UUID</th><th scope='col'>Licence start &ndash; end</th>"
            "<th scope='col'>Evidence</th><th scope='col'>Production</th></tr></thead>")
    table = ("<div class='tbl-wrap'><table class='dense'>" + head + "<tbody>" + body + "</tbody></table></div>" if body
             else "<p class='empty'>No CO has verified Dev evidence yet.</p>")
    page = ("<div class='page-head'><h1>Onboarded on Dev</h1><span class='chip chip-info'>Leonardo Development</span></div>"
            "<p class='note'>Only verified evidence counts: a create readback with both ids, or a verified renewal edit. "
            "Dry runs, uncertain runs and unverified writes never count"
            + (" · " + str(mirrors_only) + " Dev mirror(s) not counted" if mirrors_only else "") + ". The Production column "
            "is the read-only match against the production clone.</p>" + ("<p class='note'>" + escape(note) + "</p>" if note else "")
            + "<section class='card tbl-card'>" + table + "</section>")
    return _app_shell("Onboarded on Dev", page, active="dev_onboarded", wide=True)


# ---- Search (owner request 2026-10-07): GET only, read-only, local data plus the cached queue read ---------------
SEARCH_MAX_LENGTH = 80
SEARCH_MAX_ROWS = 50
SEARCH_REFERENCE = re.compile(r"(?:co)?[\s-]*([0-9]{1,10})", re.IGNORECASE)


def search_reference(text: str) -> str | None:
    """Canonical CO reference (CO-0767) for CO-0767, co-0767, "co 767", 767; None for any other text."""
    found = SEARCH_REFERENCE.fullmatch(text)
    if found is None:
        return None
    reference = "CO-" + found.group(1).zfill(4)
    return reference if REFERENCE.fullmatch(reference) else None


def search_query_problem(raw: str) -> bool:
    return len(raw) > SEARCH_MAX_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in raw)


def _search_inventory_names(readbacks: dict[str, dict[str, str]]) -> dict[str, tuple[str, str]]:
    """CO -> (tenant name, main domain) from the local Dev inventory snapshot; empty when it is missing or stale."""
    if not readbacks:
        return {}
    try:
        payload = inventory.load_latest(inventory_root(), "dev", max_age=INVENTORY_DISPLAY_MAX_AGE, now=datetime.now(timezone.utc))
        by_ref = inventory.match_readbacks(payload, readbacks)["by_reference"]
        tenants = {t.get("id"): t for t in payload.get("tenants") or () if isinstance(t, dict)}
    except (inventory.InventoryError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}
    found: dict[str, tuple[str, str]] = {}
    for ref, tenant_id in by_ref.items():
        tenant = tenants.get(tenant_id) if tenant_id not in (None, "conflict") else None
        if tenant:
            found[ref] = (str(tenant.get("account_name") or ""), str(tenant.get("account_domain") or ""))
    return found


def search_matches(needle: str) -> tuple[list[dict[str, Any]], str]:
    """(matching COs, note). No Salesforce read beyond the cached open-queue read; never raises."""
    needle = needle.casefold()
    note = ""
    try:
        rows = {row["Name"]: row for row in queue_source_rows() if row.get("Name")}
    except ReadUnavailable:
        rows, note = {}, "The open queue could not be read from Salesforce; showing local matches only."
    try:
        state, readbacks, outcomes, mirrors = _evidence_inputs()
    except Exception:  # noqa: BLE001 - local evidence only; a bad file never breaks the search
        state, readbacks, outcomes, mirrors = {}, {}, {}, frozenset()
        note = (note + " " if note else "") + "Local run records could not be read."
    names = _search_inventory_names(readbacks)
    found: list[dict[str, Any]] = []
    for ref in sorted(set(rows) | set(state) | set(readbacks) | set(outcomes)):
        row, record = rows.get(ref), state.get(ref)
        tenant, domain = names.get(ref, ("", ""))
        domain = domain or (row or {}).get("Main_Domain__c") or ""
        fields = [ref, (row or {}).get("Account__r.Name") or "", tenant, domain]
        if not any(needle in str(field).casefold() for field in fields):
            continue
        where = []
        if row is not None:
            where.append("Open queue")
        try:
            if dev_status(_evidence_for(ref, state, readbacks, outcomes, mirrors)) == "onboarded":
                where.append("Onboarded on Dev")
        except Exception:  # noqa: BLE001
            pass
        if not where:
            where.append("Local run records")
        if row is not None:
            progress = " · ".join(part for part in (row.get("Onboarding_Approval_Status__c"), row.get("Onboarding_Stage__c")) if part) or "—"
        else:
            progress = "—"
        found.append({"ref": ref, "account": (row or {}).get("Account__r.Name") or tenant or "—", "row": row, "record": record,
                      "where": where, "progress": progress, "domain": domain})
    return found, note


def render_search(raw: str) -> str:
    """Results page for /search. Every value is escaped; the query is echoed escaped."""
    if search_query_problem(raw):
        body = ("<div class='page-head'><h1>Search</h1></div><section class='card'><p>Search text too long or invalid.</p>"
                "<p class='note'>Use up to " + str(SEARCH_MAX_LENGTH) + " characters, without control characters.</p></section>")
        return _app_shell("Search", body, active="search")
    needle = " ".join(raw.split())
    title = "Search: " + needle
    if not needle:
        body = ("<div class='page-head'><h1>Search</h1></div><section class='card'><p class='empty'>Enter a CO number "
                "(CO-0767 or 767), an account name or a domain.</p></section>")
        return _app_shell("Search", body, active="search")
    found, note = search_matches(needle)
    shown = found[:SEARCH_MAX_ROWS]
    rows_html = "".join(
        "<tr><td class='stick'>" + _co_link(item["ref"]) + "</td><td>" + escape(item["account"])
        + ("<span class='sub'>" + escape(item["domain"]) + "</span>" if item["domain"] else "")
        + "</td><td>" + _route_label(item["ref"], item["row"], item["record"]) + "</td><td>"
        + escape(", ".join(item["where"])) + "</td><td>" + escape(item["progress"]) + "</td></tr>" for item in shown)
    table = ("<div class='tbl-wrap'><table class='dense'><thead><tr><th scope='col' class='stick'>CO</th><th scope='col'>Account</th>"
             "<th scope='col'>Route / Case</th><th scope='col'>Where</th><th scope='col'>Approval / Stage</th></tr></thead><tbody>"
             + rows_html + "</tbody></table></div>" if shown else "<p class='empty'>No CO matches.</p>")
    more = ("<p class='note'>Showing the first " + str(SEARCH_MAX_ROWS) + " of " + str(len(found)) + " matches; narrow the search.</p>"
            if len(found) > SEARCH_MAX_ROWS else "")
    body = ("<div class='page-head'><h1>" + escape(title) + "</h1></div>"
            "<p class='note'>Matches the CO number, account name and tenant name or domain in the open queue, the local run "
            "records and the Dev tenant snapshot. Read-only.</p>"
            + ("<p class='note'>" + escape(note) + "</p>" if note else "")
            + "<section class='card tbl-card'>" + table + more + "</section>")
    return _app_shell(title, body, active="search")


def search_response(raw: str) -> tuple[str | None, str]:
    """(redirect target, page): a valid CO reference redirects to its page; anything else renders results."""
    if not search_query_problem(raw):
        reference = search_reference(" ".join(raw.split()))
        if reference is not None:
            return "/co/" + reference, ""
    return None, render_search(raw)


def _env_segment(reference: str, current: str, dev_chip: str, prod_chip: str) -> str:
    """[ Dev ][ Production ] as plain links (no script); each label carries its status chip."""
    def link(env: str, label: str, chip: str) -> str:
        on = env == current
        return ("<a href='/co/" + escape(reference) + "?env=" + env + "'" + (" class='on' aria-current='page'" if on else "")
                + ">" + label + " " + chip + "</a>")
    return "<div class='seg' role='group' aria-label='Environment'>" + link("dev", "Dev", dev_chip) + link("prod", "Production", prod_chip) + "</div>"


def _field_table(fields: list[dict[str, Any]]) -> str:
    rows = ""
    for item in fields:
        css, label = FIELD_RESULT_CHIPS.get(item["result"], ("chip-neutral", item["result"]))
        rows += ("<tr><td>" + escape(item["label"]) + ("" if item["required"] else "<span class='sub'>informational</span>")
                 + "</td><td>" + escape(str(item["expected"])) + "</td><td>" + escape(str(item["production"]))
                 + "</td><td><span class='chip " + css + "'>" + escape(label) + "</span></td></tr>")
    return ("<div class='tbl-wrap'><table class='dense'><thead><tr><th scope='col'>Field</th><th scope='col'>Salesforce / DealHub "
            "(expected)</th><th scope='col'>Production (clone)</th><th scope='col'>Result</th></tr></thead><tbody>" + rows
            + "</tbody></table></div>")


def _candidates_table(candidates: list[dict[str, Any]]) -> str:
    rows = ""
    for item in candidates:
        flags = " ".join(chip for cond, chip in (
            (item.get("deleted"), "<span class='chip chip-bad'>Deleted</span>"),
            (not item.get("enabled") and not item.get("deleted"), "<span class='chip chip-neutral'>Disabled</span>"),
            (item.get("live_paid"), "<span class='chip chip-ok'>Live, paid</span>")) if cond)
        rows += ("<tr><td>" + escape(str(item.get("name") or "—")) + "<span class='sub'><code>" + escape(str(item.get("id") or "—"))
                 + "</code></span></td><td>" + escape(str(item.get("domain") or "—")) + "</td><td>"
                 + escape(str(item.get("license_type") or "—")) + "</td><td>" + escape(_created_date(item.get("created")))
                 + "</td><td>" + flags + "</td></tr>")
    return ("<div class='tbl-wrap'><table class='dense'><thead><tr><th scope='col'>Production tenant</th><th scope='col'>Domain</th>"
            "<th scope='col'>Licence type</th><th scope='col'>Created</th><th scope='col'>State</th></tr></thead><tbody>" + rows
            + "</tbody></table></div>")


def page_detail_production(reference: str, row: dict[str, str | None], match: dict[str, Any]) -> str:
    """The Production view of a CO page (GET only: no form, no button, no write)."""
    account = row.get("Account_Name__c")
    case = renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    route_chip = ("<span class='chip chip-info'>" + escape(case[1]) + "</span>" if case is not None else _route_chip(row))
    header = ("<div class='page-head'><h1>" + escape(reference) + "</h1>"
              + ("<span class='acct'>" + escape(account) + "</span>" if account else "") + route_chip + "</div>")
    seg = _env_segment(reference, "prod", _dev_status_chip(dev_evidence_for(reference)), _match_chip(match["status"]))
    reason = ("<p class='note'>" + escape(str(match.get("reason") or "")) + "</p>") if match.get("reason") else ""
    status = ("<section class='card'><div class='card-head'><h2 class='pill'>Production match</h2>" + _match_chip(match["status"])
              + "</div>" + reason)
    if match["status"] in ("exists_matches", "exists_differs"):
        status += _field_table(match["fields"])
    if match.get("candidates"):
        status += "<h3 class='sub-h'>Matched production tenants</h3>" + _candidates_table(match["candidates"])
    status += "</section>"
    source = ("<p class='note'>" + _clone_line(_clone_info()) + " · Source: Redash saved query on the cloned production "
              "database, personal data removed. A missing match is never a clearance.</p>")
    checklist = ("<section class='card'><div class='card-head'><h2 class='pill'>Before migration</h2></div><ul>"
                 + "".join("<li>" + escape(text) + "</li>" for text in BEFORE_MIGRATION) + "</ul></section>")
    main_html = ("<a class='crumb' href='/prod'>&larr; Production readiness</a>" + header + seg
                 + "<p class='prodnote'>Production view, read-only. Nothing on this page writes to production, Leonardo or "
                 "Salesforce.</p>" + _production_marker_for(reference) + status + source + checklist)
    return _app_shell(reference + " production", main_html, active="prod", env="prod")


# Dashboard login (2026-10-03, owner decision): the operator's Salesforce SSO.
# There is no dashboard password. "Sign in" ends the alias's previous CLI
# session and runs a fresh `sf org login web` (corporate SSO + MFA in the
# browser). When it finishes, Salesforce's userinfo for that session (email,
# username, org Id; no token) must name an allowed operator
# (SURFACE_DASHBOARD_ALLOWED_USERS) in the pinned org. Only then does the
# browser that started the sign-in get this dashboard's own opaque, in-memory
# session cookie, and the Leonardo sign-in and preflight checks start. A
# forgotten password is reset through Pentera's normal SSO password reset.
SESSION_COOKIE = "surface_dashboard_session"
ATTEMPT_COOKIE = "surface_login_attempt"
DASHBOARD_SESSION_SECONDS = 12 * 60 * 60
LOGIN_ATTEMPT_SECONDS = 10 * 60
USERINFO_PATH = "/services/oauth2/userinfo"
_login_lock = Lock()
_dashboard_sessions: dict[str, tuple[float, str]] = {}  # token -> (expiry, operator)
_login_attempt: dict[str, Any] = {}  # token, process, started
_current_operator: contextvars.ContextVar[str] = contextvars.ContextVar("current_operator", default="")
LOGIN_REASON_TEXT = {
    "login_cancelled_or_failed": "The Salesforce sign-in was cancelled or did not finish. Nothing was changed.",
    "login_timeout": "The Salesforce sign-in was not completed within 10 minutes.",
    "login_in_progress": "A sign-in is already in progress on this desktop. Finish it, or cancel it first.",
    "salesforce_login_port_busy": "An earlier Salesforce sign-in is still waiting on this desktop (port 1717). Finish or "
                                  "close it, or wait a few minutes until it times out, then try again. Your current "
                                  "session was not changed.",
    "login_identity_unavailable": "Salesforce did not report who signed in. You were not signed in.",
    "login_no_allowed_operator": "No operator is configured for this dashboard (start it with start_attended_dashboard.ps1).",
    "login_operator_not_allowed": "That Salesforce account is not an operator of this dashboard. Its CLI session was ended.",
    "salesforce_org_not_pinned": "The expected Salesforce org Id is not configured, so no sign-in can be accepted.",
    "salesforce_wrong_org": "That sign-in belongs to a different Salesforce org. Its CLI session was ended.",
    "desktop_runtime_required": "Sign-in is only available on the attended Windows desktop.",
    "run_in_progress": "An attended run is in progress. Sign out after it finishes.",
}


# ---------------------------------------------------------------------------------------------------------------
# VM team-access mode (docs/40 step 0, owner go 2026-10-07). Everything below is active ONLY when
# SURFACE_ONBOARDING_RUNTIME=vm; the desktop keeps its Salesforce-SSO login, loopback-only origin checks and
# cookies exactly as before.
#
# Identity: oauth2-proxy (behind nginx) authenticates the person with OneLogin and passes the verified e-mail in
# PROXY_IDENTITY_HEADER. The dashboard trusts that header only when the TCP peer is loopback (the proxy on the
# same host) and exactly one well-formed value is present; from any other peer, or in desktop mode, it is ignored.
# Authorization: SURFACE_ONBOARDING_ALLOWED_USERS (+ _FILE) is the allow-list; SURFACE_ONBOARDING_OPERATORS
# (+ _FILE) is the subset that may POST. An unknown user gets 403. Phase 1 (docs/40 section 4) is read-only on
# the VM: every POST that launches the runner/browser or writes Salesforce stays refused for every role.
# ---------------------------------------------------------------------------------------------------------------
PROXY_IDENTITY_HEADER = "X-Forwarded-Email"
# Second factor between the proxy and the dashboard: nginx adds this header (value from a root-owned file outside
# the repository) so a local shell user who reaches 127.0.0.1 cannot forge an identity. Never logged or echoed.
PROXY_SECRET_HEADER = "X-Surface-Proxy-Secret"
PROXY_SECRET_ENV = "SURFACE_ONBOARDING_PROXY_SECRET"
PROXY_SECRET_FILE_ENV = "SURFACE_ONBOARDING_PROXY_SECRET_FILE"
PROXY_SECRET_PATTERN = re.compile(r"[\x21-\x7e]{32,256}")
# Recommended single source of truth for the allow-list and roles (see integration/onboarding/users_file.py).
USERS_FILE_ENV = "SURFACE_ONBOARDING_USERS_FILE"
TRUSTED_PROXY_PEERS = frozenset({"127.0.0.1", "::1"})
ALLOWED_USERS_ENV = "SURFACE_ONBOARDING_ALLOWED_USERS"
ALLOWED_USERS_FILE_ENV = "SURFACE_ONBOARDING_ALLOWED_USERS_FILE"
OPERATORS_ENV = "SURFACE_ONBOARDING_OPERATORS"
OPERATORS_FILE_ENV = "SURFACE_ONBOARDING_OPERATORS_FILE"
PUBLIC_ORIGIN_ENV = "SURFACE_ONBOARDING_PUBLIC_ORIGIN"
ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
VM_SIGN_OUT_PATH = "/oauth2/sign_out"  # served by oauth2-proxy, not by this dashboard
PROXY_EMAIL = re.compile(r"[a-z0-9._%+'-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}")
# Phase 1 (read-only VM): the only POSTs an operator may use. They are local acknowledgements (no browser, no
# Leonardo, no Salesforce write) and read-only checks. Everything else (runner starts, Leonardo sessions and
# checks, validation/scan/inventory sweeps, SpyCloud, renewal, Salesforce comment/id writes, runner reset,
# sign-in/out) is refused for every role.
# Owner decision 2026-10-08: the VM serves a read-only published snapshot, so NO POST is permitted for any role
# in phase 1 (the acknowledgements and checks above would write into, or read through, the read-only snapshot).
# Operators get their own write path only with the phase 2 design.
VM_OPERATOR_POSTS: frozenset[str] = frozenset()


def vm_mode() -> bool:
    return os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm"


def cookie_attributes() -> str:
    """Attributes for every cookie this dashboard sets: ``Secure`` is added in VM mode (HTTPS only)."""
    return "; HttpOnly; SameSite=Strict" + ("; Secure" if vm_mode() else "")


# ---------------------------------------------------------------------------------------------------------------
# VM data source (docs/41): in VM mode every page reads the snapshot the desktop published and the ingest tool
# unpacked (<state dir>/snapshots/current). There is no Salesforce, Leonardo, Redash or browser call and no process
# is started. The switch lives in four places only: salesforce_cli_command() / the runner's sf_command() (no CLI),
# state_paths.state_file() / inventory_root() (state and inventory files come from current/), queue_source_rows()
# and detail_row() (the two fixed Salesforce reads come from queue.json / co_details.json), and _app_shell() (banner,
# no action forms). Desktop mode never reaches any of this.
# ---------------------------------------------------------------------------------------------------------------
SNAPSHOT_STALE_HOURS_ENV = "SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS"
DEFAULT_SNAPSHOT_STALE_HOURS = 6.0
_POST_FORM = re.compile(r"<form\b[^>]*\bmethod=['\"]post['\"][^>]*>.*?</form>", re.IGNORECASE | re.DOTALL)
SNAPSHOT_BANNER_CSS = (
    ".snapbanner{margin:0 0 14px;padding:9px 14px;border-radius:6px;border:1px solid #9aa7b5;background:#eef2f6;color:#1b2733;"
    "font-size:13px}.snapbanner.stale{background:#fff3cd;border-color:#d39e00;color:#4d3800}"
    ".snapbanner.empty{background:#fde8e8;border-color:#c0392b;color:#5a1a14}")


def snapshot_root() -> Path | None:
    folder = state_paths.state_dir()
    return None if folder is None else vm_snapshot.snapshots_root(folder)


def _snapshot_json(name: str) -> Any:
    root = snapshot_root()
    if root is None:
        raise ReadUnavailable()
    try:
        return vm_snapshot.load_current_json(root, name)
    except vm_snapshot.SnapshotError:
        raise ReadUnavailable() from None


def snapshot_queue_rows() -> list[dict[str, str | None]]:
    """The published open queue: exactly the fixed QUEUE_FIELDS per row (same validation as the live read)."""
    data = _snapshot_json(vm_snapshot.QUEUE_FILE)
    try:
        rows: list[dict[str, str | None]] = []
        for record in data["rows"]:
            if (not isinstance(record, dict) or not set(QUEUE_FIELDS) <= set(record)
                    or not isinstance(record["Name"], str) or not REFERENCE.fullmatch(record["Name"])):
                raise ReadUnavailable()
            rows.append({field: record[field] if isinstance(record[field], str) else None for field in QUEUE_FIELDS})
        return rows
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


def snapshot_detail_row(reference: str) -> dict[str, str | None]:
    """The published display fields of one CO (exactly DETAIL_FIELDS); a CO that was not published is unavailable."""
    data = _snapshot_json(vm_snapshot.CO_DETAILS_FILE)
    try:
        record = data["details"][reference]
        if not isinstance(record, dict) or record.get("Name") != reference or not set(DETAIL_FIELDS) <= set(record):
            raise ReadUnavailable()
        return {field: record[field] if isinstance(record[field], str) else None for field in DETAIL_FIELDS}
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


def snapshot_stale_hours() -> float:
    try:
        value = float(os.environ.get(SNAPSHOT_STALE_HOURS_ENV, "").strip() or DEFAULT_SNAPSHOT_STALE_HOURS)
    except ValueError:
        return DEFAULT_SNAPSHOT_STALE_HOURS
    return value if 0 < value < 24 * 365 else DEFAULT_SNAPSHOT_STALE_HOURS


def snapshot_banner(now: datetime | None = None) -> str:
    """The banner every VM page shows: when the data was published, amber when older than the threshold."""
    root = snapshot_root()
    manifest = None if root is None else vm_snapshot.read_current_manifest(root)
    if manifest is None:
        return ("<div class='snapbanner empty' role='status'><strong>No data published yet.</strong> "
                "The desktop has not published a snapshot to this VM.</div>")
    created = vm_snapshot.parse_created_at(manifest["created_at"])
    age_hours = ((now or datetime.now(timezone.utc)) - created).total_seconds() / 3600
    stale = age_hours > snapshot_stale_hours()
    text = "Data as of " + created.strftime("%Y-%m-%d %H:%M") + " UTC (published from the desktop)"
    if stale:
        text += " &middot; older than " + escape(f"{snapshot_stale_hours():g}") + " h, it may be out of date"
    return "<div class='snapbanner" + (" stale" if stale else "") + "' role='status'>" + text + "</div>"


def vm_strip_actions(html: str) -> str:
    """VM pages are read-only: every POST form (Start, Confirm, Prepare sessions, checks, refresh sweeps) is removed."""
    return _POST_FORM.sub("", html)


def page_vm_unavailable() -> str:
    empty = snapshot_root() is None or vm_snapshot.read_current_manifest(snapshot_root()) is None  # type: ignore[arg-type]
    title = "No data published yet" if empty else "Not in the published data"
    detail = ("The desktop has not published a snapshot to this VM yet." if empty else
              "This item is not part of the snapshot the desktop last published. It may be new, or the desktop has not "
              "published since it appeared.")
    return _app_shell(title, "<div class='page-head'><h1>" + title + "</h1></div><section class='card'><p>" + detail
                      + "</p></section>")


def _email_set(env_name: str, file_env_name: str) -> frozenset[str]:
    """Lower-cased e-mails from a comma/space separated variable plus an optional one-per-line file (# comments).

    Fail closed: an unreadable file contributes nothing (so a missing allow-list admits nobody).
    """
    values = [item for item in re.split(r"[,\s]+", os.environ.get(env_name, "")) if item]
    path = os.environ.get(file_env_name, "").strip()
    if path:
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    values.append(line)
        except (OSError, UnicodeError):
            pass
    return frozenset(item.strip().lower() for item in values if PROXY_EMAIL.fullmatch(item.strip().lower()))


def configured_proxy_secret() -> str | None:
    """The shared secret from the file (preferred) or the variable; None when missing, unreadable, or too short.

    Fail closed: when a file is named but cannot be read, the variable is NOT used as a fallback. The value is never
    logged or returned to a client.
    """
    path = os.environ.get(PROXY_SECRET_FILE_ENV, "").strip()
    if path:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
    else:
        raw = os.environ.get(PROXY_SECRET_ENV, "")
    value = raw.strip()
    return value if PROXY_SECRET_PATTERN.fullmatch(value) else None


def proxy_secret_matches(secret_values: list[str] | None) -> bool:
    """True only for exactly one header value equal to the configured secret (constant-time comparison)."""
    expected = configured_proxy_secret()
    if expected is None or not secret_values or len(secret_values) != 1:
        return False
    return hmac.compare_digest(secret_values[0].strip().encode("utf-8"), expected.encode("utf-8"))


def proxy_identity(peer: str | None, header_values: list[str], secret_values: list[str] | None = None) -> str | None:
    """The proxy-asserted e-mail, or None.

    Only in VM mode, only from a loopback peer, only with the shared proxy secret, only exactly one valid value.
    """
    if not vm_mode() or peer not in TRUSTED_PROXY_PEERS or len(header_values) != 1:
        return None
    if not proxy_secret_matches(secret_values):
        return None
    value = header_values[0].strip().lower()
    return value if PROXY_EMAIL.fullmatch(value) else None


def _list_configured(env_name: str, file_env_name: str) -> bool:
    return bool(os.environ.get(env_name, "").strip() or os.environ.get(file_env_name, "").strip())


def _users_file_roles() -> dict[str, str]:
    """``{email: role}`` from SURFACE_ONBOARDING_USERS_FILE; empty (nobody) when unset, unreadable, or malformed."""
    path = os.environ.get(USERS_FILE_ENV, "").strip()
    if not path:
        return {}
    try:
        return users_file.load_users_file(path)
    except users_file.UsersFileError:
        return {}


def vm_role(email: str | None) -> str | None:
    """``operator`` / ``viewer`` for an allow-listed e-mail, else None (unknown user). Operators must also be allowed.

    The ALLOWED_USERS / OPERATORS variables (and their _FILE forms) win over the single users file when set,
    each list independently; otherwise the users file is the source.
    """
    if not email:
        return None
    email = email.strip().lower()
    roles = _users_file_roles()
    if _list_configured(ALLOWED_USERS_ENV, ALLOWED_USERS_FILE_ENV):
        allowed = _email_set(ALLOWED_USERS_ENV, ALLOWED_USERS_FILE_ENV)
    else:
        allowed = frozenset(roles)
    if email not in allowed:
        return None
    if _list_configured(OPERATORS_ENV, OPERATORS_FILE_ENV):
        operators = _email_set(OPERATORS_ENV, OPERATORS_FILE_ENV)
    else:
        operators = frozenset(item for item, role in roles.items() if role == ROLE_OPERATOR)
    return ROLE_OPERATOR if email in operators else ROLE_VIEWER


def configured_public_origin() -> tuple[str | None, bool]:
    """(origin, valid): the one https origin the VM answers for. (None, True) when unset; (None, False) when malformed."""
    raw = os.environ.get(PUBLIC_ORIGIN_ENV, "").strip().lower()
    if not raw:
        return None, True
    if re.fullmatch(r"https://[a-z0-9.-]{1,253}(:[0-9]{1,5})?", raw):
        return raw, True
    return None, False


def login_required() -> bool:
    """Every desktop dashboard page and action needs a signed-in operator (the VM keeps its own identity gate)."""
    return os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "desktop"


def _cookie(header: str | None, name: str) -> str | None:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None


def dashboard_operator(cookie_header: str | None, *, now: float | None = None) -> str | None:
    """The signed-in operator for this browser's session cookie, else None."""
    token = _cookie(cookie_header, SESSION_COOKIE)
    if token is None:
        return None
    now = monotonic() if now is None else now
    with _login_lock:
        session = _dashboard_sessions.get(token)
        if session is None or now >= session[0]:
            _dashboard_sessions.pop(token, None)
            return None
        return session[1]


def _sf_quiet(*arguments: str, timeout: int = 30) -> int | None:
    """Run one CLI command with its output discarded; the exit code, or None if it could not run."""
    try:
        return subprocess.run([salesforce_cli_command(), *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        return None


def start_dashboard_login() -> tuple[str | None, str | None]:
    """(attempt token, None) after starting a fresh SSO sign-in, or (None, reason)."""
    if not local_browser_launch_allowed():
        return None, "desktop_runtime_required"
    with _login_lock:
        process = _login_attempt.get("process")
        if process is not None and process.poll() is None:
            if monotonic() - _login_attempt["started"] < LOGIN_ATTEMPT_SECONDS:
                return None, "login_in_progress"
            kill_process_tree(process)
        _login_attempt.clear()
        if salesforce_login_port_busy():
            # Checked before the logout: a refused start leaves the current session untouched.
            return None, "salesforce_login_port_busy"
        # A fresh SSO + MFA every time: the previous CLI session for the alias is ended first.
        _sf_quiet("org", "logout", "--target-org", salesforce_target_org(), "--no-prompt")
        try:
            process = subprocess.Popen([salesforce_cli_command(), "org", "login", "web", "--alias", salesforce_target_org(),
                                        "--browser", salesforce_login_browser()],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return None, "login_cancelled_or_failed"
        token = token_urlsafe(32)
        _login_attempt.update(token=token, process=process, started=monotonic())
    set_session_status("salesforce", SessionStatus(SessionState.SIGNING_IN, "waiting_for_operator", readiness_now()))
    return token, None


def cancel_dashboard_login(attempt_token: str | None) -> None:
    with _login_lock:
        if attempt_token and _login_attempt.get("token") and hmac.compare_digest(attempt_token, _login_attempt["token"]):
            process = _login_attempt.get("process")
            if process is not None and process.poll() is None:
                kill_process_tree(process)
            _login_attempt.clear()


def salesforce_userinfo() -> object:
    """Salesforce's userinfo for the pinned alias (email, username, org Id; it carries no token), or None."""
    try:
        done = subprocess.run([salesforce_cli_command(), "api", "request", "rest", USERINFO_PATH, "--method", "GET",
                               "--target-org", salesforce_target_org()], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=SALESFORCE_PROBE_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode or done.stdout is None or len(done.stdout.encode()) > 64 * 1024:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        return None


def finish_dashboard_login(attempt_token: str | None) -> tuple[str, str | None]:
    """("none" | "pending" | "failed" | "signed_in", session token or reason) for this browser's attempt."""
    with _login_lock:
        token = _login_attempt.get("token")
        if not attempt_token or not token or not hmac.compare_digest(attempt_token, token):
            return "none", None
        process = _login_attempt["process"]
        returncode = process.poll()
        if returncode is None:
            if monotonic() - _login_attempt["started"] < LOGIN_ATTEMPT_SECONDS:
                return "pending", None
            kill_process_tree(process)
            _login_attempt.clear()
            set_session_status("salesforce", SessionStatus(SessionState.EXPIRED, "salesforce_sign_in_required", readiness_now()))
            return "failed", "login_timeout"
        _login_attempt.clear()
    if returncode != 0:
        set_session_status("salesforce", SessionStatus(SessionState.EXPIRED, "salesforce_sign_in_required", readiness_now()))
        return "failed", "login_cancelled_or_failed"
    allowed = readiness.allowed_operators(os.environ.get("SURFACE_DASHBOARD_ALLOWED_USERS"))
    userinfo = salesforce_userinfo()
    problem = readiness.identity_problem(userinfo, allowed, os.environ.get("SURFACE_SF_EXPECTED_ORG_ID"))
    if problem is not None:
        if problem in ("login_operator_not_allowed", "salesforce_wrong_org"):
            # Never leave a foreign session behind for the dashboard's reads.
            _sf_quiet("org", "logout", "--target-org", salesforce_target_org(), "--no-prompt")
        set_session_status("salesforce", SessionStatus(SessionState.BLOCKED, problem, readiness_now()))
        return "failed", problem
    operator = readiness.operator_label(userinfo, allowed)  # type: ignore[arg-type]
    session = token_urlsafe(32)
    with _login_lock:
        _dashboard_sessions[session] = (monotonic() + DASHBOARD_SESSION_SECONDS, operator)
    set_session_status("salesforce", SessionStatus(SessionState.READY, "salesforce_ready", readiness_now()))
    # Leonardo sign-in (with the preflight first) starts right away in the automation browser.
    start_leonardo_session_worker(sign_in=True)
    return "signed_in", session


def end_dashboard_session(cookie_header: str | None) -> str | None:
    """Sign out: the dashboard session and the alias's CLI session. Refused mid-run.

    The automation window stays open: it also hosts the dashboard tab, which
    now shows the sign-in page. "Close automation browser" ends it.
    """
    if any_run_in_progress():
        return "run_in_progress"
    token = _cookie(cookie_header, SESSION_COOKIE)
    with _login_lock:
        if token:
            _dashboard_sessions.pop(token, None)
    _sf_quiet("org", "logout", "--target-org", salesforce_target_org(), "--no-prompt")
    set_session_status("salesforce", readiness.NOT_SIGNED_IN)
    set_session_status("leonardo", readiness.NOT_SIGNED_IN)
    return None


# --- Preflight (read-only): what Salesforce and Leonardo sign-in depend on ---
LEONARDO_DEVELOPMENT_HOST = "leonardo.dev.app.pentera.io"
PREFLIGHT_TIMEOUT_SECONDS = 5
_preflight: dict[str, Any] = {}
PREFLIGHT_LABELS = {"leonardo_network": "Leonardo Development reachable (VPN)", "salesforce_cli": "Salesforce CLI",
                    "browser_runtime": "Browser automation runtime", "chrome": "Chrome"}


def run_preflight() -> dict[str, str]:
    """Local presence checks and one TCP connect to Leonardo Development (no request is sent)."""
    try:
        with socket.create_connection((LEONARDO_DEVELOPMENT_HOST, 443), timeout=PREFLIGHT_TIMEOUT_SECONDS):
            network = "ok"
    except OSError:
        network = "unreachable"
    results = {
        "leonardo_network": network,
        "salesforce_cli": "ok" if shutil.which(salesforce_cli_command()) else "missing",
        "browser_runtime": "ok" if importlib.util.find_spec("playwright") is not None else "missing",
        "chrome": "installed" if _chrome_executable() else "bundled",
    }
    with _readiness_lock:
        _preflight.clear()
        _preflight.update(results=results, checked_at=readiness_now())
    return results


def preflight_snapshot() -> dict[str, Any]:
    with _readiness_lock:
        return dict(_preflight)


def page_login(state: str = "", reason: str | None = None) -> str:
    """The sign-in page: Salesforce SSO only; the dashboard never sees a password."""
    pending = state == "pending"
    banner = ""
    if pending:
        banner = ("<div class='banner'><strong>Complete the Salesforce sign-in</strong> (OneLogin: email, password, MFA) "
                  "in the new Chrome tab that opened. This page checks every 3 seconds.</div>"
                  "<form method='post' action='/login/cancel'><button class='ghost' type='submit'>Cancel sign-in"
                  "</button></form>")
    elif reason:
        banner = ("<div class='banner banner-warn'>" + escape(LOGIN_REASON_TEXT.get(reason, "Sign-in failed."))
                  + " <span class='note'>(" + escape(reason) + ")</span></div>")
    reset_url = os.environ.get("SURFACE_PASSWORD_RESET_URL", "")
    reset = ("<a href='" + escape(reset_url) + "' rel='noreferrer'>Pentera's password reset page</a>"
             if re.fullmatch(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/?=&%-]*)?", reset_url) else "Pentera's standard SSO password reset")
    form = ("" if pending else "<form method='post' action='/login/start'><button type='submit'>Sign in with Salesforce"
            "</button></form>")
    body = (
        "<div class='page-head'><h1>Sign in</h1></div><section class='card'>" + banner +
        "<p>Sign in with your Pentera Salesforce account (SSO and MFA). Only the operators configured on this desktop "
        "can use the dashboard. Signing in also starts the Leonardo Development sign-in in the automation browser and "
        "runs the preflight checks (VPN reachability, Salesforce CLI, browser runtime).</p>" + form +
        "<p class='note'>The dashboard has no password of its own and never receives credentials, MFA codes, cookies, "
        "or CLI output. Forgot your password? Use " + reset + ".</p></section>"
    )
    refresh = "<meta http-equiv='refresh' content='3'>" if pending else ""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>" + refresh + "<title>Sign in</title><style>"
            + PENTERA_CSS + "</style></head><body><main style='max-width:680px;margin:40px auto'>" + body
            + "</main></body></html>")


# Routes that launch the runner: "start" creates a tenant, "read" is read-only.
SESSION_GATED_ROUTES = {
    "/attended/start-ce-only-runner": "start", "/attended/start-co0702-ce-only-runner": "start",
    "/attended/start-surface-runner": "start", "/attended/start-renewal": "start",
    "/attended/validate": "read", "/attended/scan-status-refresh": "read", "/attended/verify-uncertain": "read",
    "/attended/spycloud-check": "read",
}


POST_ROUTES = frozenset({
    "/attended/prepare-sessions", "/attended/session-recheck", "/attended/inventory-refresh",
    "/attended/duplicate-precheck",
    "/attended/salesforce-login", "/attended/leonardo-dev-session-check", "/attended/leonardo-dev-session-bootstrap",
    "/attended/leonardo-dev-session-reset", "/attended/leonardo-dev-browser-close",
    "/attended/rerun-comment-evaluation", "/attended/rerun-co0745-renewal-evaluation",
    "/attended/rerun-ce-only-fill-preflight", "/attended/rerun-co0702-fill-preflight",
    "/attended/start-ce-only-runner", "/attended/start-co0702-ce-only-runner", "/attended/reset-ce-only-runner",
    "/attended/start-surface-runner", "/attended/start-renewal", "/attended/mark-scan-settings-off", "/attended/mark-ce-enabled",
    "/attended/mark-operator-assigned", "/attended/confirm-user-created", "/attended/unconfirm-user-created",
    "/attended/confirm-comment-update", "/attended/production-renewal-preflight",
    "/attended/leonardo-session-check", "/attended/start-manual-onboarding", "/attended/scan-status-refresh", "/attended/scan-status-refresh-all",
    "/attended/validate", "/attended/validate-all", "/attended/verify-uncertain", "/attended/spycloud-check",
    "/attended/salesforce-id-writeback-review", "/attended/salesforce-id-writeback-confirm",
})


# "same-origin", not "no-referrer": under no-referrer a browser sends "Origin: null"
# on this dashboard's own form posts (verified 2026-10-03 in Chromium), which
# request_origin_problem must refuse, so every button returned 403. same-origin
# still sends nothing to other sites.
REFERRER_POLICY = "same-origin"


def request_origin_problem(command: str, host: str | None, origin: str | None, fetch_site: str | None,
                           port: int, referer: str | None = None) -> str | None:
    """Why a request must be refused before any handler runs, else None (review item 6, 2026-10-01).

    The Host header must be this dashboard's own loopback address (blocks DNS
    rebinding reads). A POST must not come from another site: a present
    Origin must be this dashboard, and without Origin a present
    Sec-Fetch-Site must be same-origin or none. Browsers always send one of
    them on a cross-site form post; a local script that sends neither is not
    a cross-site page. SURFACE_ONBOARDING_ALLOWED_HOSTS adds exact host:port
    values (for example an SSH-tunnel port), comma-separated.

    VM mode with SURFACE_ONBOARDING_PUBLIC_ORIGIN (docs/40 step 0): behind the HTTPS proxy the forwarded Host must
    be exactly the public host[:port] and a POST's Origin (or, without one, Referer) exactly the public origin;
    nothing else is accepted (a malformed setting refuses everything). Desktop and an unset origin: unchanged.
    """
    if vm_mode():
        public, valid = configured_public_origin()
        if not valid:
            return "public_origin_invalid"
        if public is not None:
            if (host or "").strip().lower() != public[len("https://"):]:
                return "host_not_allowed"
            if command == "POST":
                def from_public(value: str) -> bool:
                    value = value.strip().lower()
                    return value == public or value.startswith(public + "/")
                if origin is not None:
                    if origin.strip().lower() != public:
                        return "origin_not_allowed"
                    if referer is not None and not from_public(referer):
                        return "origin_not_allowed"
                elif referer is not None:
                    if not from_public(referer):
                        return "origin_not_allowed"
                elif fetch_site is not None and fetch_site.strip().lower() not in ("same-origin", "none"):
                    return "cross_site_post"
            return None
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed |= {item.strip().lower() for item in os.environ.get("SURFACE_ONBOARDING_ALLOWED_HOSTS", "").split(",") if item.strip()}
    if (host or "").strip().lower() not in allowed:
        return "host_not_allowed"
    if command == "POST":
        if origin is not None:
            if origin.strip().lower() not in {"http://" + item for item in allowed}:
                return "origin_not_allowed"
        elif fetch_site is not None and fetch_site.strip().lower() not in ("same-origin", "none"):
            return "cross_site_post"
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None: pass
    vm_email: str | None = None

    def _vm_identity(self) -> tuple[str | None, str | None]:
        """(proxy-asserted e-mail, role) in VM mode; (None, None) when the header is absent, malformed, or untrusted."""
        email = proxy_identity(self.client_address[0] if self.client_address else None,
                               list(self.headers.get_all(PROXY_IDENTITY_HEADER) or []),
                               list(self.headers.get_all(PROXY_SECRET_HEADER) or []))
        return email, vm_role(email)

    def _vm_refuse(self, status: HTTPStatus, title: str, message: str) -> None:
        # Consume a small unread request body first so the refusal is not lost to a connection reset.
        try:
            unread = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            unread = 0
        if 0 < unread <= 65536:
            self.rfile.read(unread)
        else:
            self.close_connection = True
        self.send_page(status, "<!doctype html><title>" + escape(title) + "</title><p>" + escape(message) + "</p>")

    def _vm_identity_refusal(self, email: str | None) -> None:
        if email is None:
            self._vm_refuse(HTTPStatus.FORBIDDEN, "Sign-in required",
                            "No verified sign-in reached the dashboard. Open it through the single sign-on address.")
        else:
            self._vm_refuse(HTTPStatus.FORBIDDEN, "Access not granted",
                            "This account is not on the dashboard allow-list. Ask the owner to add it.")

    def request_operator(self) -> str | None:
        """The identity to record in audit fields: the proxy e-mail on the VM, else the desktop session operator."""
        return self.vm_email if vm_mode() else dashboard_operator(self.headers.get("Cookie"))
    def parse_request(self) -> bool:
        """Refuse foreign Host / cross-site POST requests before any page or action runs."""
        if not super().parse_request():
            return False
        problem = request_origin_problem(self.command, self.headers.get("Host"), self.headers.get("Origin"),
                                         self.headers.get("Sec-Fetch-Site"), self.server.server_address[1],
                                         self.headers.get("Referer"))
        if problem is not None:
            self.send_error(HTTPStatus.FORBIDDEN, "Request refused")
            return False
        return True
    def send_page(self, status: HTTPStatus, page: str) -> None:
        data = page.encode(); self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Referrer-Policy", REFERRER_POLICY); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Content-Security-Policy", PAGE_CSP); self.end_headers(); self.wfile.write(data)
    def _claim_and_launch(self, reference: str, revision: str, launch: Any, **start_fields: str) -> bool:
        """Record the start first (the claim), then launch; a failed launch is recorded as such."""
        try:
            record_runner_start(reference, revision, datetime.now().isoformat(timespec="seconds"), **start_fields)
        except ValueError as error:
            message = START_BLOCKED_MESSAGES.get(str(error), "The start could not be recorded. No browser was launched.")
            self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>" + message + "</p>"
                           "<p><a href='/co/" + escape(reference) + "'>Return to " + escape(reference) + "</a></p>")
            return False
        except (OSError, RunnerStateUnavailable):
            self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Runner state unavailable</title><p>The start could not be recorded. No browser was launched. Do not retry; inspect the state file.</p>")
            return False
        if not launch():
            try:
                record_runner_result(reference, revision, "runner_launch_failed", datetime.now().isoformat(timespec="seconds"))
            except (OSError, ValueError, RunnerStateUnavailable):
                pass
            self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>The isolated desktop runner is unavailable. No Leonardo action was performed.</p>")
            return False
        return True

    def send_redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER); self.send_header("Location", location); self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Referrer-Policy", REFERRER_POLICY); self.end_headers()
    def send_redirect_with_cookies(self, location: str, cookies: list[str]) -> None:
        self.send_response(HTTPStatus.SEE_OTHER); self.send_header("Location", location)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Referrer-Policy", REFERRER_POLICY); self.end_headers()

    def _get_login(self) -> None:
        cookie_header = self.headers.get("Cookie")
        if dashboard_operator(cookie_header):
            self.send_redirect("/"); return
        state, value = finish_dashboard_login(_cookie(cookie_header, ATTEMPT_COOKIE))
        clear_attempt = ATTEMPT_COOKIE + "=; Path=/" + cookie_attributes() + "; Max-Age=0"
        if state == "signed_in":
            self.send_redirect_with_cookies("/connection", [
                SESSION_COOKIE + "=" + str(value) + "; Path=/" + cookie_attributes() + "; Max-Age=" + str(DASHBOARD_SESSION_SECONDS),
                clear_attempt])
            return
        self.send_page(HTTPStatus.OK, page_login(state, value))

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if vm_mode():
            # The Salesforce-CLI sign-in does not exist on the VM; identity comes from the proxy header.
            email, role = self._vm_identity()
            if email is None or role is None:
                self._vm_identity_refusal(email); return
            if path == "/login":
                self._vm_refuse(HTTPStatus.SERVICE_UNAVAILABLE, "Sign-in unavailable",
                                "Sign-in is handled by the single sign-on proxy on this VM, not by the dashboard."); return
            self.vm_email = email
            _current_operator.set(email)
        else:
            if path == "/login":
                self._get_login(); return
            operator = dashboard_operator(self.headers.get("Cookie")) if login_required() else ""
            if operator is None:
                self.send_redirect("/login"); return
            _current_operator.set(operator)
        # Display reads may use the short in-memory cache; ?refresh=1 drops it.
        if parse_qs(urlsplit(self.path).query).get("refresh", [""])[0] == "1":
            clear_display_cache()
        context = contextvars.copy_context()
        context.run(Handler._display_get, self)

    def _display_get(self) -> None:
        _display_reads.set(True)
        _display_read_times.set([])
        started = monotonic()
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                selected_queue = parse_qs(parsed.query).get("queue", [""])[0]
                scan_started = parse_qs(parsed.query).get("scan", [""])[0] == "started"
                html = render_dashboard(
                    selected_queue, scan_started, parse_qs(parsed.query).get("sort", [""])[0],
                    parse_qs(parsed.query).get("dir", [""])[0])
                record_timing("page_build", "queue", started, True)
                self.send_page(HTTPStatus.OK, html); return
            if path == "/connection":
                self.send_page(HTTPStatus.OK, page_salesforce_unavailable(failed=False)); return
            if path in ("/history", "/dev/history"):
                self.send_page(HTTPStatus.OK, render_history()); return
            params = parse_qs(parsed.query)
            kept = {key: value[0] for key, value in params.items()
                    if key in ("q", "sort", "dir", "scan", "co") and value and value[0]}
            if (path == "/inventory" and params.get("env", [""])[0] == "prod-clone") or path == "/tenants":
                # The production clone moved to Production > Tenants; keep old links and bookmarks working.
                self.send_redirect("/prod/tenants" + ("?" + urlencode(kept) if kept else "")); return
            if path == "/dev/onboarded":
                self.send_page(HTTPStatus.OK, render_dev_onboarded()); return
            if path == "/search":
                target, page = search_response(params.get("q", [""])[0])
                if target:
                    self.send_redirect(target)
                else:
                    self.send_page(HTTPStatus.OK, page)
                return
            if path == "/prod":
                self.send_page(HTTPStatus.OK, render_prod_readiness(params.get("filter", [""])[0],
                                                                    params.get("all", [""])[0] == "1")); return
            if path == "/prod/matches":
                self.send_page(HTTPStatus.OK, render_prod_matches(params.get("all", [""])[0] == "1")); return
            if path in ("/inventory", "/prod/tenants"):
                clone = path == "/prod/tenants"
                self.send_page(HTTPStatus.OK, render_inventory(
                    params.get("q", [""])[0], "" if clone else params.get("refresh", [""])[0],
                    sort=params.get("sort", [""])[0], direction=params.get("dir", [""])[0],
                    scan=params.get("scan", [""])[0], co=params.get("co", [""])[0],
                    env="prod-clone" if clone else "dev", top=PRODUCTION_GATE_NOTE if clone else ""))
                return
            if path in ("/attended/ce-only-runner-status", "/attended/co0702-runner-status"):
                ref = parse_qs(parsed.query).get("ref", [CO0702_REFERENCE])[0]
                if not REFERENCE.fullmatch(ref):
                    ref = CO0702_REFERENCE
                try:
                    state = load_runner_state()
                except RunnerStateUnavailable:
                    state = None
                self.send_page(HTTPStatus.OK, page_ce_only_runner_status(state, ref)); return
            match = re.fullmatch(r"/co/(CO-[0-9]{4,10})", path)
            notice = parse_qs(parsed.query).get("comment-update", [""])[0]
            if notice not in {"verified", "blocked"}: notice = ""
            if parse_qs(parsed.query).get("scan", [""])[0] == "started": notice = "started"
            if parse_qs(parsed.query).get("validation", [""])[0] == "started": notice = "validation-started"
            if parse_qs(parsed.query).get("spycloud", [""])[0] == "started": notice = "spycloud-started"
            id_write = parse_qs(parsed.query).get("id-write", [""])[0]
            if id_write in ID_WRITEBACK_RESULTS: notice = "id-write:" + id_write
            if match:
                # Overlap the onboarding preflight with the CO read when the
                # cached queue already tells us the route.
                queued = next((r for r in peek_display_cache("queue_source_rows") or [] if r.get("Name") == match.group(1)), None)
                queued_route = route_for(queued) if queued is not None else None
                if queued_route == CE_ENGINE:
                    prefetch_display_read(evaluate_ce_only_fill_preflight, match.group(1))
                elif queued_route in SURFACE_ROUTES:
                    prefetch_display_read(evaluate_surface_fill_preflight, match.group(1), queued_route)
                # Start the CO row and the DealHub read together (semi-join on the
                # CO name) so the two CLI calls overlap; both are display reads.
                prefetch_display_read(detail_row, match.group(1))
                if dealhub_needed(queued):
                    prefetch_display_read(dealhub_rows_for_co, match.group(1))
                row = detail_row(match.group(1))
                if parse_qs(parsed.query).get("env", [""])[0] == "prod":
                    # Production view: read-only comparison with the production clone; no forms, no writes.
                    html = page_detail_production(match.group(1), row, _match_of(
                        match.group(1), row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")))
                    record_timing("page_build", "co_prod", started, True)
                    self.send_page(HTTPStatus.OK, html)
                    return
                html = page_detail(match.group(1), row, notice, surface_commercial_readiness(row))
                record_timing("page_build", "co", started, True)
                self.send_page(HTTPStatus.OK, html)
                return
            self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
        except ReadUnavailable:
            if vm_mode():
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_vm_unavailable()); return
            set_session_status("salesforce", SessionStatus(SessionState.EXPIRED, "salesforce_read_failed", readiness_now()))
            self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_salesforce_unavailable())

    def _post_login(self, path: str) -> None:
        cookie_header = self.headers.get("Cookie")
        if path == "/login/cancel":
            cancel_dashboard_login(_cookie(cookie_header, ATTEMPT_COOKIE))
            self.send_redirect("/login"); return
        token, reason = start_dashboard_login()
        if token is None:
            self.send_page(HTTPStatus.CONFLICT, page_login("failed", reason)); return
        self.send_redirect_with_cookies("/login", [
            ATTEMPT_COOKIE + "=" + token + "; Path=/" + cookie_attributes() + "; Max-Age=" + str(LOGIN_ATTEMPT_SECONDS)])

    def _start_renewal(self, reference: str, form: Any, row: dict[str, str | None]) -> None:
        """Start renewal: the same guards as Start onboarding plus a one-time nonce; Leonardo Development only."""
        back = "<p><a href='/co/" + escape(reference) + "'>Return to " + escape(reference) + "</a></p>"

        def blocked(code: str, status: HTTPStatus = HTTPStatus.CONFLICT) -> None:
            self.send_page(status, "<!doctype html><title>Start blocked</title><p>"
                           + START_BLOCKED_MESSAGES.get(code, "The start is blocked. No browser was launched.") + "</p>" + back)

        if not source_ready_to_onboard(row):
            blocked("renewal_not_approved"); return
        if not renewal_supported(renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))):
            blocked("renewal_not_supported"); return
        if (exact_form_value(form, "plan_reviewed") != "1"
                or exact_form_value(form, "attended_renewal_authorized") != "1"):
            blocked("renewal_acknowledgement_missing"); return
        acknowledged = exact_form_value(form, "source_revision")
        if not acknowledged:
            blocked("revision_acknowledgement_missing"); return
        if acknowledged != row.get("LastModifiedDate"):
            blocked("source_revision_changed"); return
        if not consume_renewal_start_ack(reference, row, exact_form_value(form, "nonce") or ""):
            blocked("renewal_nonce_invalid"); return
        with _start_lock:
            try:
                state = load_runner_state()
            except RunnerStateUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Runner state unavailable</title><p>The local runner state file could not be read. No browser was launched. Do not retry; inspect the state file.</p>")
                return
            record = state.get(reference)
            if record is not None and record.get("source_revision") == acknowledged and record.get("result") \
                    and not renewal_uncertain(record):
                self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)  # already ran for this revision
                return
            blocker = start_blocker(record)
            if blocker is not None:
                blocked(blocker); return
            if any(not other.get("result") for ref, other in state.items() if ref != reference):
                blocked("run_in_progress_other"); return
            if not self._claim_and_launch(reference, acknowledged,
                                          lambda: start_attended_renewal_runner(reference, acknowledged),
                                          route=renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))[0]):  # type: ignore[index]
                return
        self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)

    def do_POST(self) -> None:
        # Any action may change Salesforce or local state; later pages read fresh.
        clear_display_cache()
        path = urlsplit(self.path).path
        if vm_mode():
            email, role = self._vm_identity()
            if email is None or role is None:
                self._vm_identity_refusal(email); return
            self.vm_email = email
            if role != ROLE_OPERATOR:
                self._vm_refuse(HTTPStatus.FORBIDDEN, "Read-only access",
                                "Viewers cannot change anything. Nothing was started or recorded."); return
            if path in ("/login/start", "/login/cancel"):
                self._vm_refuse(HTTPStatus.SERVICE_UNAVAILABLE, "Sign-in unavailable",
                                "Sign-in is handled by the single sign-on proxy on this VM, not by the dashboard."); return
            if path not in VM_OPERATOR_POSTS:
                self._vm_refuse(HTTPStatus.FORBIDDEN, "Not available on the VM",
                                "The VM dashboard is read-only in phase 1: runs start from the owner's Windows desktop. "
                                "Nothing was started or changed."); return
        if path in ("/login/start", "/login/cancel"):
            self._post_login(path); return
        if login_required() and dashboard_operator(self.headers.get("Cookie")) is None:
            # Not signed in: no form parse, Salesforce read, or launch.
            self.send_page(HTTPStatus.FORBIDDEN, "<!doctype html><title>Sign in required</title><p>"
                           "<a href='/login'>Sign in with Salesforce</a> first. Nothing was started.</p>")
            return
        if path == "/logout":
            problem = end_dashboard_session(self.headers.get("Cookie"))
            if problem is not None:
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Sign-out refused</title><p>"
                               + escape(LOGIN_REASON_TEXT[problem]) + "</p><p><a href='/'>Return</a></p>")
                return
            self.send_redirect_with_cookies("/login", [SESSION_COOKIE + "=; Path=/" + cookie_attributes() + "; Max-Age=0"])
            return
        if path not in POST_ROUTES:
            # Unknown routes stop here: no form parse, Salesforce read, or launch.
            self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
            return
        if path == "/attended/prepare-sessions":
            environment = exact_form_value(post_form(self), "environment") or readiness.LEONARDO_DEVELOPMENT
            problem = prepare_sessions_problem(environment)
            if problem is not None:
                locked = problem.startswith("production_") or problem == "unknown_environment"
                self.send_page(HTTPStatus.FORBIDDEN if locked else HTTPStatus.CONFLICT,
                               session_gate_page(problem, "/connection"))
                return
            prepare_sessions()
            self.send_redirect("/connection")
            return
        if path == "/attended/session-recheck":
            problem = prepare_sessions_problem(readiness.LEONARDO_DEVELOPMENT)
            if problem is not None:
                self.send_page(HTTPStatus.CONFLICT, session_gate_page(problem, "/connection"))
                return
            probe_salesforce_readiness()
            start_leonardo_session_worker(sign_in=False)
            self.send_redirect("/connection")
            return
        if path == "/attended/salesforce-login":
            if start_attended_salesforce_login():
                self.send_page(HTTPStatus.OK, page_salesforce_login_opened())
            else:
                if os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm":
                    self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_salesforce_unavailable())
                else:
                    self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Salesforce sign-in unavailable</title><p>Salesforce CLI could not be started. Use the approved desktop launcher or repair the local Salesforce CLI session.</p>")
            return
        if path == "/attended/leonardo-dev-session-check":
            if os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm":
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_leonardo_session_result("leonardo_session_unavailable"))
                return
            result = check_leonardo_session()
            set_session_status("leonardo", readiness.classify_leonardo_result(result, readiness_now()))
            self.send_page(HTTPStatus.OK, page_leonardo_session_result(result))
            return
        if path == "/attended/leonardo-dev-session-bootstrap":
            if os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm":
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_leonardo_session_result("leonardo_session_unavailable"))
                return
            result = bootstrap_leonardo_session()
            set_session_status("leonardo", readiness.classify_leonardo_result(result, readiness_now()))
            self.send_page(HTTPStatus.OK, page_leonardo_session_result(result))
            return
        if path == "/attended/leonardo-dev-session-reset":
            if os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm":
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_leonardo_session_result("leonardo_profile_reset_unavailable"))
                return
            ok = reset_leonardo_profile()
            if ok:
                set_session_status("leonardo", SessionStatus(SessionState.EXPIRED, "leonardo_session_expired", readiness_now()))
            self.send_page(HTTPStatus.OK, page_leonardo_session_result("leonardo_profile_reset" if ok else "leonardo_profile_reset_unavailable"))
            return
        if path == "/attended/leonardo-dev-browser-close":
            if os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm":
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_leonardo_session_result("automation_browser_close_unavailable"))
                return
            self.send_page(HTTPStatus.OK, page_leonardo_session_result(close_automation_browser()))
            return
        if path == "/attended/validate-all":
            # Read-only sweep: no CO reference, no Salesforce write.
            problem = action_readiness_problem("read")
            if problem is not None:
                self.send_page(HTTPStatus.CONFLICT, session_gate_page(problem)); return
            if not start_attended_validation_all():
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Validation unavailable</title><p>The read-only validation could not be started on this desktop.</p>")
                return
            self.send_redirect("/?queue=scanning&scan=started")
            return
        if path == "/attended/inventory-refresh":
            # Read-only export + validation sweep: no CO reference, no Leonardo or Salesforce write.
            problem = action_readiness_problem("read")
            if problem is not None:
                self.send_page(HTTPStatus.CONFLICT, session_gate_page(problem, "/inventory")); return
            if not start_attended_inventory_export():
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Inventory unavailable</title><p>The read-only tenant export could not be started on this desktop.</p>")
                return
            self.send_redirect("/inventory?refresh=started")
            return
        if path == "/attended/scan-status-refresh-all":
            # Read-only sweep: no CO reference, no Salesforce write.
            problem = action_readiness_problem("read")
            if problem is not None:
                self.send_page(HTTPStatus.CONFLICT, session_gate_page(problem)); return
            if not start_attended_scan_status_all():
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Scan status unavailable</title><p>The read-only scan-status sweep could not be started on this desktop.</p>")
                return
            self.send_redirect("/?queue=scanning&scan=started")
            return
        form = post_form(self)
        reference = exact_form_value(form, "reference")
        if reference is None or not REFERENCE.fullmatch(reference):
            self.send_page(HTTPStatus.BAD_REQUEST, "<!doctype html><title>Invalid request</title><p>Return to the dashboard and retry the attended step.</p>")
            return
        gated_action = SESSION_GATED_ROUTES.get(path)
        if gated_action is not None:
            # Fresh, verified sessions before any runner launch (2026-10-03).
            problem = action_readiness_problem(gated_action)
            if problem is not None:
                self.send_page(HTTPStatus.CONFLICT, session_gate_page(problem, "/co/" + reference))
                return
        if path == "/attended/rerun-comment-evaluation":
            if reference != "CO-0741":
                self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
                return
            try:
                evaluation = evaluate_co0741_comment_update()
                nonce = issue_comment_update_ack(evaluation)
            except ReadUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Evaluation blocked</title><p>CO-0741 could not be validated as one empty-comment CO and one active matching DealHub subscription. No Salesforce change was made.</p>")
                return
            self.send_page(HTTPStatus.OK, page_comment_update_confirmation(evaluation, nonce))
            return
        if path == "/attended/rerun-co0745-renewal-evaluation":
            if reference != CO0745_REFERENCE:
                self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
                return
            try:
                evaluation = evaluate_co0745_renewal_comment()
            except ReadUnavailable:
                self.send_page(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "<!doctype html><title>Evaluation blocked</title><p>CO-0745 could not be validated as one empty-comment, New, Pending Surface renewal with one active baseline subscription. No Salesforce change was made.</p>",
                )
                return
            self.send_page(HTTPStatus.OK, page_co0745_renewal_evaluation(evaluation))
            return
        if path in ("/attended/rerun-ce-only-fill-preflight", "/attended/rerun-co0702-fill-preflight"):
            try:
                evaluation = evaluate_ce_only_fill_preflight(reference)
            except ReadUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Preflight unavailable</title><p>" + escape(reference) + " could not be read for this preflight. No external action was performed.</p>")
                return
            try:
                state = load_runner_state()
            except RunnerStateUnavailable:
                state = None
            self.send_page(HTTPStatus.OK, page_ce_only_fill_preflight(evaluation, state))
            return
        if path in ("/attended/start-ce-only-runner", "/attended/start-co0702-ce-only-runner"):
            if exact_form_value(form, "attended_create_authorized") != "1":
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>An explicit attended authorization is required.</p>")
                return
            acknowledged_revision = exact_form_value(form, "source_revision")
            try:
                evaluation = evaluate_ce_only_fill_preflight(reference)
            except ReadUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Preflight unavailable</title><p>" + escape(reference) + " could not be freshly read. No browser was launched.</p>")
                return
            with _start_lock:
                try:
                    state = load_runner_state()
                except RunnerStateUnavailable:
                    self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Runner state unavailable</title><p>The local runner state file could not be read. No browser was launched. Do not retry; inspect the state file.</p>")
                    return
                decision = evaluate_ce_only_start(acknowledged_revision, evaluation, state)
                if decision == "revision_already_acknowledged":
                    # The run already happened (or is in progress): show its actual
                    # outcome, then return to the CO, instead of a dead-end message.
                    # No browser is launched.
                    self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)
                    return
                if decision != "start":
                    blocked = START_BLOCKED_MESSAGES.get(decision, "The start is blocked. No browser was launched.")
                    self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>" + blocked + "</p>"
                                   "<p><a href='/co/" + escape(reference) + "'>Return to " + escape(reference) + "</a></p>")
                    return
                if not self._claim_and_launch(reference, evaluation.source_revision,
                                              lambda: start_attended_ce_only_runner(reference, evaluation.source_revision)):
                    return
            self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)
            return
        if path == "/attended/start-surface-runner":
            back = "<p><a href='/co/" + escape(reference) + "'>Return to " + escape(reference) + "</a></p>"
            if exact_form_value(form, "attended_create_authorized") != "1":
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>An explicit attended authorization is required.</p>" + back)
                return
            if exact_form_value(form, "scope_reviewed") != "1":
                # scope_review_missing: every Surface CO needs the revision-bound
                # scope review (owner decision 2026-09-29, option b).
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>"
                               + RUNNER_RESULT_MESSAGES["scope_review_missing"][1] + "</p>" + back)
                return
            acknowledged_revision = exact_form_value(form, "source_revision")
            scope_digest = exact_form_value(form, "scope_digest")
            route = exact_form_value(form, "route") or SURFACE_ENGINE
            if route not in SURFACE_ROUTES:
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>Unsupported route. No browser was launched.</p>" + back)
                return
            try:
                evaluation = evaluate_surface_fill_preflight(reference, route)
            except ReadUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Preflight unavailable</title><p>" + escape(reference) + " could not be freshly read. No browser was launched.</p>")
                return
            with _start_lock:
                try:
                    state = load_runner_state()
                except RunnerStateUnavailable:
                    self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Runner state unavailable</title><p>The local runner state file could not be read. No browser was launched. Do not retry; inspect the state file.</p>")
                    return
                record = state.get(reference)
                if record is not None and record.get("result") == "readback_verified":
                    # A verified tenant exists: show it; a later revision never re-creates it.
                    self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)
                    return
                decision = evaluate_surface_start(acknowledged_revision, scope_digest, evaluation, state)
                if decision == "revision_already_acknowledged":
                    self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)
                    return
                if decision != "start":
                    blocked = START_BLOCKED_MESSAGES.get(decision, "The start is blocked. No browser was launched.")
                    self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Start blocked</title><p>" + blocked + "</p>" + back)
                    return
                now = datetime.now().isoformat(timespec="seconds")
                if not self._claim_and_launch(reference, evaluation.source_revision,
                                              lambda: start_attended_surface_runner(reference, evaluation.source_revision, route),
                                              route=route, scope_reviewed_on=now):
                    return
            self.send_redirect("/attended/ce-only-runner-status?ref=" + reference)
            return
        if path in ("/attended/salesforce-id-writeback-review", "/attended/salesforce-id-writeback-confirm") \
                and not ID_WRITEBACK_ENABLED:
            self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Salesforce ID write disabled</title><p>Leonardo "
                           "Development IDs stay on the dashboard only (owner decision 2026-10-01). Nothing was "
                           "written to Salesforce.</p><p><a href='/co/" + escape(reference) + "'>Return</a></p>")
            return
        if path == "/attended/salesforce-id-writeback-review":
            # Read-only: a fresh read and a one-time, revision-bound confirmation page.
            try:
                evaluation = evaluate_id_writeback(reference)
            except ReadUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_salesforce_unavailable())
                return
            if evaluation.blocker:
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Write not available</title><p>Nothing can be written for "
                               + escape(reference) + " (<code>" + escape(evaluation.blocker) + "</code>). Nothing was changed.</p>")
                return
            self.send_page(HTTPStatus.OK, page_id_writeback_confirmation(evaluation, issue_id_writeback_ack(evaluation)))
            return
        if path == "/attended/salesforce-id-writeback-confirm":
            nonce = exact_form_value(form, "nonce")
            try:
                evaluation = evaluate_id_writeback(reference)
            except ReadUnavailable:
                self.send_redirect("/co/" + reference + "?id-write=write_blocked")
                return
            result = write_salesforce_id_after_confirmation(evaluation, nonce or "")
            self.send_redirect("/co/" + reference + "?id-write=" + result)
            return
        if path == "/attended/verify-uncertain":
            try:
                record = load_runner_state().get(reference)
            except RunnerStateUnavailable:
                record = None
            if renewal_uncertain(record):
                if (record or {}).get("uncertain") != "renewal_write":
                    self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Verify by hand</title><p>The Dev mirror may exist. Check Leonardo Development manually; the dashboard cannot verify a mirror create. Nothing was started.</p>"
                                   "<p><a href='/co/" + escape(reference) + "'>Return to " + escape(reference) + "</a></p>")
                    return
                # Read-only renewal dry run: it records the outcome and settles the uncertain apply.
                if not _start_runner_mode("--co", reference, "--renew"):
                    self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Verify unavailable</title><p>The read-only check could not be started on this desktop.</p>")
                    return
                self.send_redirect("/co/" + reference)
                return
            if not create_uncertain(record):
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Nothing to verify</title><p>" + escape(reference) + " has no uncertain create. Nothing was started.</p>")
                return
            route = (record or {}).get("route") or CE_ENGINE
            if not _start_runner_mode("--co", reference, "--readback-only", "--route", route):
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Verify unavailable</title><p>The read-only check could not be started on this desktop.</p>")
                return
            self.send_redirect("/co/" + reference)
            return
        if path == "/attended/duplicate-precheck":
            # Read-only: one Salesforce source read and the local DEV inventory; no Leonardo, no browser.
            try:
                route = route_for(detail_row(reference))
            except ReadUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_salesforce_unavailable()); return
            if route not in (CE_ENGINE, *SURFACE_ROUTES):
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Not supported</title><p>The duplicate pre-check "
                               "covers new CE, Surface, and Case 3 onboardings only.</p><p><a href='/co/" + escape(reference)
                               + "'>Return</a></p>"); return
            self.send_page(HTTPStatus.OK, page_duplicate_precheck(reference, run_inventory_precheck(reference, route)))
            return
        if path == "/attended/validate":
            if reference not in attended_leonardo_readbacks():
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Not onboarded</title><p>No local readback exists for " + escape(reference) + ". Nothing was started.</p>")
                return
            if not start_attended_validation(reference):
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Validation unavailable</title><p>The read-only validation could not be started on this desktop.</p>")
                return
            self.send_redirect("/co/" + reference + "?validation=started")
            return
        if path == "/attended/spycloud-check":
            # Read-only dry run (opens Edit, reports the checkbox, Cancel): never --confirm-write.
            if reference not in attended_leonardo_readbacks():
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Not onboarded</title><p>No local readback exists for " + escape(reference) + ". Nothing was started.</p>")
                return
            if not start_attended_spycloud_check(reference):
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>SpyCloud check unavailable</title><p>The read-only SpyCloud check could not be started on this desktop.</p>")
                return
            self.send_redirect("/co/" + reference + "?spycloud=started")
            return
        if path == "/attended/scan-status-refresh":
            # Read-only: launches the runner's --scan-status mode for an onboarded CO.
            if reference not in attended_leonardo_readbacks():
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Not onboarded</title><p>No local readback exists for " + escape(reference) + ". Nothing was started.</p>")
                return
            if not start_attended_scan_status(reference):
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Scan status unavailable</title><p>The read-only scan-status read could not be started on this desktop.</p>")
                return
            self.send_redirect("/co/" + reference + "?scan=started")
            return
        if path in ("/attended/mark-scan-settings-off", "/attended/mark-ce-enabled", "/attended/mark-operator-assigned"):
            # Local acknowledgements only: no Leonardo, browser, or Salesforce write.
            if path in ("/attended/mark-scan-settings-off", "/attended/mark-operator-assigned"):
                kind = "scan_settings_off" if path == "/attended/mark-scan-settings-off" else "operator_assigned"
                try:
                    record = load_runner_state().get(reference)
                except RunnerStateUnavailable:
                    record = None
                allowed = (record is not None and record.get("route") in SURFACE_ROUTES
                           and record.get("result") == "readback_verified")
            else:
                kind = "ce_enabled"
                try:
                    allowed = evaluate_surface_fill_preflight(reference).core_plus_present
                except ReadUnavailable:
                    allowed = False
            if not allowed:
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Reminder not applicable</title><p>This reminder does not apply to " + escape(reference) + ". Nothing was recorded.</p>")
                return
            try:
                record_attended_reminder(reference, kind)
            except (OSError, ValueError, ReadUnavailable):
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Reminder unavailable</title><p>The local reminders file could not be written. Nothing was changed in Leonardo or Salesforce.</p>")
                return
            self.send_redirect("/co/" + reference)
            return
        if path in ("/attended/confirm-user-created", "/attended/unconfirm-user-created"):
            # Local acknowledgement only: no Leonardo, browser, or Salesforce write.
            try:
                set_user_created_confirmation(reference, path == "/attended/confirm-user-created",
                                              self.request_operator())
            except (OSError, ValueError):
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Confirmation unavailable</title><p>The local confirmation file could not be written. Nothing was changed in Leonardo or Salesforce.</p>")
                return
            self.send_redirect("/co/" + reference)
            return
        if path == "/attended/reset-ce-only-runner":
            if exact_form_value(form, "reset_authorized") != "1":
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Reset blocked</title><p>An explicit reset authorization is required.</p>")
                return
            try:
                reset_runner_record(reference)
            except RunnerStateUnavailable:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Reset blocked</title><p>The local runner state file could not be read. No change was made. Do not retry; inspect the state file.</p>")
                return
            except ValueError as error:
                reset_message = {
                    "invalid_runner_state_record": "The runner state record is invalid. No change was made.",
                    "runner_in_progress_cannot_be_reset": "An attended run is in progress and cannot be reset. Wait for it to finish.",
                    "runner_result_cannot_be_reset": "The run verified a created tenant and cannot be reset.",
                    "create_uncertain_cannot_be_reset": "The run may have created a tenant. Verify it read-only on the CO page first; nothing was changed.",
                    "renewal_uncertain_cannot_be_reset": "The renewal run may have written to Leonardo Development. Verify it on the CO page first; nothing was changed.",
                }.get(str(error), "The runner record could not be reset. No change was made.")
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Reset blocked</title><p>" + escape(reset_message) + "</p>")
                return
            self.send_redirect("/co/" + reference)
            return
        if path == "/attended/confirm-comment-update":
            if reference != "CO-0741":
                self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
                return
            nonce = exact_form_value(form, "nonce")
            try:
                evaluation = evaluate_co0741_comment_update()
                updated = nonce is not None and update_co0741_comment_after_confirmation(evaluation, nonce)
            except WriteUnavailable:
                self.send_redirect("/co/CO-0741?comment-update=blocked")
                return
            except ReadUnavailable:
                self.send_redirect("/co/CO-0741?comment-update=blocked")
                return
            if not updated:
                self.send_redirect("/co/CO-0741?comment-update=blocked")
                return
            self.send_redirect("/co/CO-0741?comment-update=verified")
            return
        try:
            row = detail_row(reference)
        except ReadUnavailable:
            self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Unavailable</title><p>The current source could not be read for this attended step.</p>")
            return
        if path == "/attended/production-renewal-preflight":
            if "renewal" not in (row.get("Onboarding_Type__c") or "").casefold():
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Renewal preflight blocked</title><p>This source is not a renewal.</p>")
                return
            if not open_attended_production_backoffice_login():
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Browser unavailable</title><p>The production sign-in page could not be opened. No tenant search or change was performed.</p>")
                return
            self.send_page(HTTPStatus.OK, "<!doctype html><title>Production sign-in opened</title><p>The production BackOffice sign-in page was opened. Complete SSO/MFA manually. This Phase 1 preflight does not search, open, edit, or save a tenant.</p><p><a href='/co/" + escape(reference) + "'>Return to " + escape(reference) + "</a></p>")
            return
        if path == "/attended/start-renewal":
            self._start_renewal(reference, form, row)
            return
        if not source_ready_to_onboard(row):
            self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Source not ready</title><p>This source is no longer ready for manual onboarding.</p>")
            return
        if path == "/attended/leonardo-session-check":
            if not open_attended_leonardo_tenant_management() or grant_manual_start_ack(reference, row) is None:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Browser unavailable</title><p>The Development tenant-management page could not be opened. Connect to the RND VPN and open the approved page manually.</p>")
                return
            self.send_page(HTTPStatus.OK, "<!doctype html><title>Session check opened</title><p>The exact Leonardo Development tenant-management route was opened. Complete SSO/MFA if needed, then return to the source detail and attest your active admin session. No tenant action was performed.</p><p><a href='/co/" + escape(reference) + "'>Return to source detail</a></p>")
            return
        if path == "/attended/start-manual-onboarding":
            nonce = exact_form_value(form, "nonce")
            attested = exact_form_value(form, "admin_session_active") == "1"
            if not attested or nonce is None or not consume_manual_start_ack(reference, row, nonce):
                self.send_page(HTTPStatus.CONFLICT, "<!doctype html><title>Manual start blocked</title><p>A fresh session check and admin-session attestation are required for the current source revision.</p>")
                return
            if not open_attended_leonardo_tenant_management():
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Browser unavailable</title><p>The Development tenant-management page could not be opened. No onboarding action was performed.</p>")
                return
            self.send_page(HTTPStatus.OK, "<!doctype html><title>Manual onboarding opened</title><p>Tenant management was opened for the attended workflow. Select Add Account manually when ready. This action did not fill, submit, create, or authorize a tenant.</p>")
            return
        if path != "/attended/leonardo-session-check":
            self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")


if __name__ == "__main__":
    listener_host, listener_port = listener_address()
    print(f"attended_open_onboardings_dashboard_listening_on_{listener_host}:{listener_port}")
    _timing_handler = logging.StreamHandler(sys.stderr)
    _timing_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _timing_logger.addHandler(_timing_handler)
    _timing_logger.setLevel(logging.INFO)
    ThreadingHTTPServer((listener_host, listener_port), Handler).serve_forever()
