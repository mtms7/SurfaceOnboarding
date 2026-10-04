"""Desktop-only, attended dashboard for Salesforce's Open_Onboardings view.

No scheduler, cache, write operation, VM transport, or generic query is
provided. Each request uses the operator's current Salesforce CLI session and
keeps data in memory only for rendering that response.
"""
from __future__ import annotations

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
import webbrowser
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import leonardo_inventory as inventory
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
    VALIDATION_PATH,
    RunnerStateUnavailable,
    bootstrap_leonardo_session,
    check_leonardo_session,
    CE_ENGINE,
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
    SURFACE_ROUTE_PRODUCT,
    SURFACE_ROUTE_TYPE,
    _chrome_executable,
    close_automation_browser,
    load_runner_state,
    start_blocker,
    create_uncertain,
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
ATTENDED_LEONARDO_READBACK_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_leonardo_readbacks.json"
ATTENDED_CE_ONLY_RUNNER = Path(__file__).resolve().with_name("attended_ce_only_playwright.py")
# Local operator acknowledgements for post-onboarding reminders (gitignored).
# It never drives Leonardo or Salesforce; it only hides a reminder.
ATTENDED_REMINDERS_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_scan_reminders.json"
REMINDER_FIELDS = {"scan_settings_off": "scan_settings_off_on", "ce_enabled": "ce_enabled_on",
                   "operator_assigned": "operator_assigned_on"}
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


VALIDATION_STATUSES = frozenset({"ok", "drift", "unknown", "info"})
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
            results[reference] = {"checks": checks, "plan_note": str(value.get("plan_note") or ""),
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


def _validation_section(reference: str, notice: str = "", now: datetime | None = None) -> str:
    """Surface validation checklist for an onboarded CO (local, read-only observation)."""
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
    stale = now > result["expires_at"]  # type: ignore[operator]
    cls, icon = ("source-blocked", "!") if drift else (("source-warn", "!") if unknown else ("source-ready", "✓"))
    title = (f"{len(drift)} difference(s) found" if drift
             else (f"Verified, {len(unknown)} check(s) unconfirmed" if unknown else "Everything matches"))
    marks = {"ok": "✓", "drift": "✗", "unknown": "?", "info": "·"}
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
            style = " style='color:var(--bad);font-weight:600'" if item["status"] == "drift" else ""
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
            + "<details class='fold'" + (" open" if drift else "") + "><summary>" + str(len(checks)) + " checks</summary>"
            "<dl>" + rows + "</dl></details></section>")


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
                                   "duration_ms": duration, "observed_at": observed, "expires_at": expires}
        except (KeyError, TypeError, ValueError):
            continue
    return statuses


def _scan_status_section(reference: str, notice: str = "", now: datetime | None = None) -> str:
    """Leonardo scan-status card for an onboarded CO (local, read-only observation)."""
    observation = attended_scan_statuses().get(reference)
    now = now or datetime.now()
    refresh = ("<form method='post' action='/attended/scan-status-refresh'><input type='hidden' name='reference' value='"
               + escape(reference) + "'><button type='submit' class='ghost sm'>Refresh scan status</button></form>")
    started = ("<p class='note'>A read-only scan-status read was started in the automation browser. "
               "Reload this page in about 30 seconds.</p>" if notice == "started" else "")
    if observation is None:
        return ("<section class='stat' aria-labelledby='scan-status-title'><div class='stat-head'>"
                "<h2 id='scan-status-title'>Leonardo scan status</h2></div>"
                "<p>Not read yet. Refresh reads the tenant's scan fields from Leonardo Development (read-only).</p>"
                + started + refresh + "</section>")
    cls, title, message = SCAN_STATES[str(observation["state"])]
    stale = now > observation["expires_at"]  # type: ignore[operator]
    icon_style = "" if cls else " style='background:#9aa1ad'"
    icon = {"source-ready": "✓", "source-blocked": "!", "source-warn": "!"}.get(cls, "–")
    duration = observation["duration_ms"]
    rows = (f"<dt>Last recon scan</dt><dd>{escape(str(observation['last_recon_scan'] or 'None'))}</dd>"
            f"<dt>Leonardo status</dt><dd><code>{escape(str(observation['status_enum'] or 'None'))}</code></dd>"
            f"<dt>Duration</dt><dd>{escape(f'{duration / 3_600_000:.1f} h' if isinstance(duration, int) else 'None')}</dd>"
            f"<dt>Observed</dt><dd>{escape(observation['observed_at'].strftime('%Y-%m-%d %H:%M'))}"  # type: ignore[union-attr]
            + (" · <b>stale, refresh</b>" if stale else "") + "</dd>")
    return ("<section class='stat" + (" " + cls if cls else "") + "' aria-labelledby='scan-status-title'><div class='stat-head'>"
            f"<span class='readiness-icon' aria-hidden='true'{icon_style}>{icon}</span>"
            f"<h2 id='scan-status-title'>Leonardo scan status · {escape(title)}</h2></div><p>{escape(message)}</p>"
            "<dl>" + rows + "</dl>" + started + refresh
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
# call costs about 4 s, so GET pages reuse a Salesforce read for up to two
# minutes. It applies only while a GET handler renders (_display_reads), keeps
# results in memory only (never disk or logs), and is cleared by a Refresh or
# any POST. Starts and the runner never use it: they re-read Salesforce and
# re-check the source revision themselves.
DISPLAY_READ_TTL_SECONDS = 120.0
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
ID_WRITEBACK_PATH = Path(__file__).resolve().parents[1] / "integration" / "attended_salesforce_id_writebacks.json"
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
                            reference: str = "", extra: str = "") -> str:
    """Folded Salesforce IDs panel (``extra``: the local readback block); a warning opens it."""
    plan = salesforce_id_writeback_plan(route, readback, row)
    status = plan["status"]

    def folded(cls: str, icon: str, icon_style: str, title: str, body: str) -> str:
        return ("<details class='more" + (" " + cls if cls else "") + "'"
                + (" open" if cls in ("source-warn", "source-blocked") else "") + " aria-labelledby='salesforce-ids-title'>"
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


def sf_json(args: list[str]) -> object:
    try:
        # Decode CLI output as UTF-8 (the --json contract) rather than the
        # locale code page; cp1252 cannot decode UTF-8 continuation bytes and
        # would otherwise leave done.stdout as None and crash the handler.
        done = subprocess.run([salesforce_cli_command(), *args, "--target-org", salesforce_target_org()], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35, check=False)
        if done.returncode or done.stdout is None or len(done.stdout.encode()) > 512 * 1024:
            raise ReadUnavailable()
        return json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ReadUnavailable() from exc


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
        email_domains = [item for item in re.split(r"[,;\s]+", raw_email_domains.strip()) if item] if isinstance(raw_email_domains, str) else []
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
    for row in rows:
        result = validations.get(row["Name"] or "")
        if result is not None:
            row["Local_Validation_Drift"] = str(sum(1 for c in result["checks"] if c["status"] == "drift"))  # type: ignore[union-attr]
    return rows


@_display_cached
def queue_source_rows() -> list[dict[str, str | None]]:
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


@_display_cached
def renewal_subscription_rows(account_id: str) -> list[dict[str, object]]:
    """DealHub rows for one account (fixed read-only query; display only, never stored)."""
    if not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
        raise ReadUnavailable()
    response = sf_json(["data", "query", "--query",
                        "SELECT Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, "
                        "DealHub_Subscription_End_Date__c FROM DealHub_Subscription__c WHERE DealHub_Account__c = '"
                        + account_id + "' LIMIT 100", "--json"])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list):  # type: ignore[index]
            raise ReadUnavailable()
        return [{key: record.get(key) for key in ("Product_Full_Name__c", "DealHub_Status__c",
                                                    "DealHub_Subscription_Start_Date__c", "DealHub_Subscription_End_Date__c")}
                for record in records if isinstance(record, dict)]
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


RENEWAL_BLOCKER_TEXT = {
    "no_new_surface_term": "No new-model Surface term (Go/Prime) is in DealHub yet; only legacy or no Surface rows.",
    "no_core_plus_term": "No Core Plus term is in DealHub for the Credential Exposure renewal.",
    "surface_term_ambiguous": "Several different Surface baselines start on the same day.",
    "core_plus_term_ambiguous": "Several Core Plus rows with different end dates start on the same day.",
    "terms_differ": "The Surface and Core Plus terms end on different dates (rule 1a: one tenant, one licence term).",
}


def _renewal_plan_section(row: dict[str, str | None], today: date | None = None) -> str:
    """Read-only renewal plan (Cases 4-6 and single-product renewals): a manual checklist, no action."""
    case = renewal_case(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    if case is None:
        return ""
    head = ("<section class='card' aria-labelledby='renewal-plan-title'><div class='card-head'>"
            "<h2 id='renewal-plan-title' class='pill'>Renewal plan · " + escape(case[1]) + "</h2>"
            "<span class='chip chip-neutral'>Manual in production · plan only</span></div>")
    try:
        rows = renewal_subscription_rows(row.get("Account__c") or "")
    except ReadUnavailable:
        return head + "<p class='note'>The DealHub subscriptions could not be read. No plan is shown.</p></section>"
    plan = build_renewal_plan(row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"), rows, today)
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
                  ("Surface settings", "Revalidate the Surface profile: 90 h, Recon / brute force / Nuclei ON, discovery / dorking / AI / static IP / auth testing / multi-stack OFF; Notifications / Multiple users / API ON")]
    if ce:
        extra = f" + {ce['ce_domains_addon']} from add-on rows (Q7)" if ce["ce_domains_addon"] else ""
        facts += [("New Core Plus term", f"{ce['products'][0]} · {ce['status']} · {ce['start']} → {ce['end']}"),
                  ("Leaked Credentials", f"ON · {ce['leaked_credentials_interval']} · {ce['ce_domains_included']} CE email domain{extra}")]
    term = surface or ce
    if term:
        when = "now" if term["applicable_now"] else "from " + term["apply_from"]
        facts += [("Apply", when + " (up to 14 days before the new term starts)"),
                  ("Expiration (Q1 open)", f"annual cap {term['annual_expiration']} · or term end {term['end']}"),
                  ("Start date", "never change (Renew card)")]
    if "terms_agree" in plan:
        agree = plan["terms_agree"]
        facts.append(("Term check", "✓ Surface and Core Plus agree" if agree["annual"] and agree["term_end"]
                      else "✗ the terms differ — manual review"))
    if plan["legacy_ignored"]:
        facts.append(("Legacy rows ignored (Q11)", str(len(plan["legacy_ignored"])) + " older-model product row(s)"))
    blockers = "".join("<p class='note' style='color:var(--bad)'><b>" + escape(RENEWAL_BLOCKER_TEXT.get(code, code))
                       + "</b> <code>" + escape(code) + "</code></p>" for code in plan["blockers"])
    checklist = ["Open the tenant in Back Office › ⋮ › Edit", "Apply the changes above; never change the start date",
                 "Check the user email domains for the primary user", "Update the Operator Account if the TA changed",
                 "Save; Salesforce updates stay manual"]
    return (head + blockers + "<dl>" + "".join("<dt>" + escape(k) + "</dt><dd>" + escape(v) + "</dd>" for k, v in facts)
            + "</dl><p class='note'>Checklist: " + " · ".join("☐ " + escape(item) for item in checklist) + "</p>"
            "<p class='login-safety'>Read-only: built from Salesforce and DealHub. Nothing is opened, changed, or "
            "saved in Leonardo or Salesforce. Rules marked Q1/Q7/Q10/Q11 await owner answers (docs/38).</p></section>")


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
    account_id = row.get("Account__c")
    if not isinstance(account_id, str) or not re.fullmatch(r"[A-Za-z0-9]{15,18}", account_id):
        return {"commercial_ready": False, "manual_review_required": True, "reason": "account_missing"}
    response = sf_json([
        "data", "query", "--query",
        "SELECT Product_Full_Name__c, DealHub_Status__c, DealHub_Subscription_Start_Date__c, DealHub_Subscription_End_Date__c "
        "FROM DealHub_Subscription__c WHERE DealHub_Account__c = '" + account_id + "' LIMIT 100",
        "--json",
    ])
    try:
        records = response["result"]["records"]  # type: ignore[index]
        if response["status"] != 0 or not isinstance(records, list):
            raise ReadUnavailable()
        subscriptions: list[dict[str, object]] = []
        for record in records:
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
    """The main-dashboard tile that summarizes the closed queue and opens the History tab."""
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
    return (f"<a class='tile hist' href='/history'><b>{number}</b><span>{escape(label)} "
            f"<i aria-hidden='true'>&rarr;</i></span><small>{escape(sub)}</small></a>")


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


def page_queue(rows: list[dict[str, str | None]], selected_queue: str = "", *,
               runner_state: dict[str, dict[str, str]] | None = None, runner_state_unavailable: bool = False,
               history: ClosedHistory | None = None, history_failed: bool = False,
               read_at: str | None = None, today: date | None = None, scan_started: bool = False) -> str:
    """Render the open-onboardings dashboard: queue tiles, a closed-history tile
    that opens the History tab, and one aligned table per queue."""
    today = today or date.today()
    read_at = read_at or datetime.now().strftime("%H:%M")
    state = runner_state or {}
    allowed = {key for key, _label, _helper in QUEUE_DEFINITIONS}
    selected = selected_queue if selected_queue in allowed else ""
    classified = []
    for row in rows:
        record = state.get(row.get("Name") or "")
        key, step, owner = classify_queue_row(row, record)
        classified.append((row, record, key, step, owner))
    by_queue: dict[str, list[tuple]] = {key: [] for key, _label, _helper in QUEUE_DEFINITIONS}
    for item in classified:
        by_queue[item[2]].append(item)
    for items in by_queue.values():
        items.sort(key=lambda item: (item[4] != "you", item[0].get("Submission_Date__c") or "9999", item[0].get("Name") or ""))
    need_you = sum(1 for item in classified if item[4] == "you")
    duplicates = sum(1 for item in by_queue["review"] if item[1] and (item[1].get("result") or "").startswith("duplicate"))
    automated = sum(1 for item in by_queue["ready"] if item[4] == "you" and route_for(item[0]))
    running = sum(1 for item in by_queue["ready"] if item[4] == "runner")
    scanning = sum(1 for item in by_queue["scanning"] if item[4] == "leonardo")
    tile_subs = {
        "review": f"{duplicates} duplicate" if duplicates else "Runs and data gaps",
        "ready": f"{automated} automated" + (f" · {running} running" if running else ""),
        "validation": "DealHub term check",
        "scanning": f"{scanning} waiting on Leonardo" if scanning else "Salesforce, scan, user",
    }

    def tile(href: str, key: str, number: int, label: str, sub: str) -> str:
        current = " aria-current='page'" if key == selected else ""
        state_class = " zero" if number == 0 else (" alert" if key == "review" else "")
        return (f"<a class='tile{state_class}' href='{href}'{current}><b>{number}</b><span>{escape(label)}</span>"
                f"<small>{escape(sub)}</small></a>")

    tiles = ("<nav class='tiles' aria-label='Queues and history'>"
             + tile("/", "", len(rows), "All open", f"{need_you} need you")
             + "".join(tile(f"/?queue={key}", key, len(by_queue[key]), label, tile_subs[key])
                       for key, label, _helper in QUEUE_DEFINITIONS)
             + history_tile(history, failed=history_failed) + "</nav>")
    banner = ""
    if runner_state_unavailable:
        banner = _outcome_banner("info", "The local attended-run record could not be read, so run results are not shown. "
                                 "Open the CO page before acting.", "runner_state_unavailable",
                                 headline="Local run results unavailable")
    head_row = ("<thead><tr><th scope='col'>Onboarding</th><th scope='col' class='opt'>Product</th>"
                "<th scope='col'>Automation</th><th scope='col' class='opt'>Salesforce</th><th scope='col'>Next step</th>"
                "<th scope='col' class='num'><abbr title='Days since submission'>Age</abbr></th></tr></thead>")
    cards = ""
    for key, label, helper in QUEUE_DEFINITIONS:
        if selected and key != selected:
            continue
        items = by_queue[key]
        if not items and not selected:
            continue
        if items:
            body = ""
            for row, record, _key, step, _owner in items:
                reference = escape(row.get("Name") or "")
                product, onboarding_type = row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")
                leonardo = row.get("Local_Leonardo_State")
                run = _run_chip(record)
                drift = row.get("Local_Validation_Drift")
                automation = (_route_chip(row) + (f"<span class='run'>{run}</span>" if run else "")
                              + (f"<span class='sub'>Leonardo: {escape(leonardo)}</span>" if leonardo else "")
                              + (f"<span class='sub' style='color:var(--bad)'>⚠ {escape(drift)} setting(s) differ</span>"
                                 if drift not in (None, "0") else ""))
                body += (
                    f"<tr><td><a class='co' href='/co/{reference}'>{reference}"
                    f"<span class='sub'>{escape(row.get('Account__r.Name') or 'Account unavailable')}</span></a></td>"
                    f"<td class='opt'>{escape(_PRODUCT_SHORT.get(product or '', product or '—'))}"
                    f"<span class='sub'>{escape(_TYPE_SHORT.get(onboarding_type or '', onboarding_type or '—'))}</span></td>"
                    f"<td>{automation}</td>"
                    f"<td class='opt'>{escape(row.get('Onboarding_Stage__c') or '—')}"
                    f"<span class='sub'>{escape(row.get('Onboarding_Approval_Status__c') or '—')}</span></td>"
                    f"<td>{step}</td><td class='num'>{_age_days(row.get('Submission_Date__c'), today)}</td></tr>")
            inner = f"<table class='q'>{head_row}<tbody>{body}</tbody></table>"
        else:
            inner = f"<p class='empty'>Nothing in {escape(label)} right now. <a href='/'>Show all open ({len(rows)})</a></p>"
        cards += (f"<section class='card queue-card' id='queue-{key}' aria-labelledby='queue-{key}-h'><div class='card-head'>"
                  f"<h2 class='pill' id='queue-{key}-h'>{escape(label)}<b class='n'>{len(items)}</b></h2>"
                  f"<span class='note'>{escape(helper)}</span></div>{inner}</section>")
    if not rows:
        cards = ("<section class='card'><div class='card-head'><h2 class='pill'>Open onboardings</h2></div>"
                 f"<p class='empty'>No open onboardings. The Salesforce Open_Onboardings view returned 0 records at "
                 f"{escape(read_at)}.</p></section>")
    hidden = (f"<input type='hidden' name='queue' value='{escape(selected)}'>" if selected else "") + "<input type='hidden' name='refresh' value='1'>"
    title = next((label for key, label, _helper in QUEUE_DEFINITIONS if key == selected), "Open onboardings")
    head = (f"<div class='page-head'><h1>{escape(title)}</h1><div class='head-meta'><span>Read from Salesforce at "
            f"{escape(read_at)}</span><form method='get' action='/'>{hidden}<button class='ghost' type='submit'>Refresh"
            "</button></form><form method='post' action='/attended/scan-status-refresh-all'>"
            "<button class='ghost' type='submit' title='Read-only: reads each onboarded Surface tenant&#39;s scan status'>"
            "Refresh scan statuses</button></form><form method='post' action='/attended/validate-all'>"
            "<button class='ghost' type='submit' title='Read-only: compares each onboarded tenant with its plan'>"
            "Validate all</button></form></div></div>")
    if scan_started:
        banner = ("<p class='note'>A read-only scan-status sweep of every onboarded Surface tenant was started in the "
                  "automation browser. Reload in about a minute.</p>") + banner
    return _app_shell("Onboardings", head + tiles + banner + cards, active="onboardings")


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
    domains = [item for item in re.split(r"[,;\s]+", raw.strip()) if item]
    if len(domains) != 1:
        return False
    domain = domains[0].casefold()
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", domain))


RENEWAL_MANUAL_NOTE = ("Renewals are applied manually in BackOffice until the renewal rules (docs/38) are decided.")


def _domain_count(raw: str | None) -> str:
    items = [item for item in re.split(r"[,;\s]+", (raw or "").strip()) if item]
    return str(len(items)) if items else "—"


def _detail_summary(row: dict[str, str | None]) -> str:
    """One line of key CO facts under the header (the full record is folded below)."""
    product, onboarding_type = row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c")
    items = (
        ("Product", _PRODUCT_SHORT.get(product or "", product or "—") + " · "
         + _TYPE_SHORT.get(onboarding_type or "", onboarding_type or "—")),
        ("Salesforce", (row.get("Onboarding_Approval_Status__c") or "Approval not populated") + " · "
         + (row.get("Onboarding_Stage__c") or "Stage not populated")),
        ("Main domain", row.get("Main_Domain__c") or "Not populated"),
        ("Alternate domains", _domain_count(row.get("Alternate_Domains__c"))),
        ("Email domains", _domain_count(row.get("Email_Domains__c"))),
    )
    return "<div class='summary'>" + "".join(
        "<span>" + escape(label) + "<b>" + escape(value) + "</b></span>" for label, value in items) + "</div>"


def page_detail(reference: str, row: dict[str, str | None], notification: str = "", commercial_readiness: dict[str, object] | None = None) -> str:
    """CO detail: header + summary, one next-step card, tenant health, then folded records.

    Renewal COs (owner decision 2026-10-04) have no primary action: the renewal
    plan leads, and the production sign-in and Leonardo Development manual
    actions are folded away as ghost buttons.
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
    commercial_ready = bool(commercial_readiness and commercial_readiness.get("commercial_ready"))
    commercial_manual_review = bool(commercial_readiness and commercial_readiness.get("manual_review_required"))
    if source_ready:
        comment_summary = (
            "The onboarding-comment subscription period is also validated."
            if comment_validation["ready_for_cse_review"]
            else "The onboarding-comment subscription period needs separate review before execution."
        )
        ce_automated = not is_case4 and ce_only_eligible(row) and not is_renewal
        # Surface-only (Case 1): exact Product "Surface" + Type "New Product
        # Onboarding" only; every other CO keeps its existing behaviour.
        surface_automated = not is_case4 and not is_renewal and route_for(row) in SURFACE_ROUTES
        # The "not enabled" explanation only applies to routes without an
        # automated onboarding; it is contradictory on a CE-only/Surface CO.
        not_enabled = ("" if ce_automated or surface_automated else
                       "<details><summary>Why Leonardo creation is not enabled yet</summary><ul>"
                       "<li>Approved route mapping and scope-threshold evaluation</li>"
                       "<li>Leonardo Development authentication, authority, and duplicate checks</li>"
                       "<li>Idempotency binding, named human approval, and read-after-write plan</li>"
                       "</ul></details>")
        readiness = (
            "<section class='readiness source-ready' aria-labelledby='readiness-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>✓</span><div><h2 id='readiness-title'>Source ready to onboard</h2>"
            "<p>Salesforce approval is validated. " + comment_summary + "</p></div></div>"
            + not_enabled + "</section>"
        )
        # The automated cards carry this sentence themselves (no separate readiness card).
        lede = ("Salesforce approval is validated. " + (comment_summary if comment_validation["ready_for_cse_review"]
                                                         else "<b>" + comment_summary + "</b>"))
        if is_case4:
            manual_action = (
                "<section class='manual-action' aria-labelledby='case4-route-title'><div><h2 id='case4-route-title'>Case 4 Leonardo workflow</h2>"
                "<p>Source is ready to onboard, but the Case 4 route remains unmapped and cannot start Leonardo onboarding.</p></div>"
                "<button type='button' disabled aria-disabled='true'>Case 4 route blocked</button>"
                "<p class='manual-blocker'>Blocked independently from Salesforce source readiness: owner-approved Case 4 mapping is required.</p></section>"
            )
        elif (ce_automated or surface_automated) and salesforce_id_already_present(route_for(row), row) \
                and reference not in attended_leonardo_readbacks():
            manual_action = _SALESFORCE_ID_PRESENT_SECTION
        elif ce_automated:
            readiness = ""
            manual_action = _ce_only_onboard_section(reference, lede=lede)
        elif surface_automated:
            readiness = ""
            manual_action = _surface_onboard_section(reference, route_for(row) or SURFACE_ENGINE, lede=lede)
        else:
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
    elif commercial_ready:
        review_suffix = " — manual review required" if commercial_manual_review else ""
        review_message = (
            "A comment already exists and will not be overwritten automatically. Review it before any approval decision."
            if commercial_manual_review else
            "A current-month or earlier Surface commercial term was found. Salesforce remains Pending until an operator reviews and approves the proposed subscription dates."
        )
        readiness = (
            "<section class='readiness source-ready' aria-labelledby='readiness-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>✓</span><div><h2 id='readiness-title'>Source ready to onboard" + review_suffix + "</h2>"
            "<p>Commercial source validation passed. " + escape(review_message) + "</p>"
            "<p class='login-safety'>Validated transiently: one eligible Surface baseline and explicitly named subdomain add-ons. "
            "This is not a Salesforce approval, a Leonardo authorization, or a write.</p></div></div></section>"
        )
        manual_action = (
            "<section class='manual-action'><div><h2>Approval decision pending</h2>"
            "<p>Review the proposed DealHub dates before changing Onboarding Comments, Stage, or Approval Status.</p></div>"
            "<button type='button' disabled aria-disabled='true'>Awaiting approval</button></section>"
        )
    else:
        commercial_reason = ""
        if commercial_readiness is not None:
            commercial_reason = " The Product, term, Main Domain, or comments need manual review."
        readiness = (
            "<section class='readiness source-blocked' aria-labelledby='readiness-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>!</span><div><h2 id='readiness-title'>Source requirements need review</h2>"
            "<p>Salesforce onboarding approval is required before this CO is source ready." + escape(commercial_reason) + "</p></div></div></section>"
        )
        manual_action = ""
    readback = attended_leonardo_readbacks().get(reference)
    readback_html = ""
    if readback is not None:
        readback_html = ("<div class='sub-block' aria-labelledby='leonardo-readback-title'>"
                         "<h3 id='leonardo-readback-title' class='sub-h'>Leonardo Development readback</h3>"
                         "<p class='note'>Local operator evidence only; Salesforce remains unchanged.</p><dl>"
                         f"<dt>Environment</dt><dd><span class='chip chip-info'>{escape(ID_ENVIRONMENT_LABEL)}</span> Leonardo Development</dd>"
                         f"<dt>Leonardo state</dt><dd>{escape(readback['leonardo_state'])}</dd>"
                         f"<dt>Observed</dt><dd>{escape(readback['observed_on'])}</dd></dl></div>")
    ids_html = _salesforce_ids_section(route_for(row), readback, row, reference, readback_html)
    health = ""
    if readback is not None:
        tiles = _validation_section(reference, notification)
        if route_for(row) != CE_ENGINE:
            tiles += _scan_status_section(reference, notification)
        health = "<div class='health'>" + tiles + "</div>"
    notifications = {"verified": ("Update verified", "CO-0741 was refreshed from Salesforce. Comments, Stage, and Approval Status are verified."), "blocked": ("Update blocked", "No verified update was completed. Reconcile the current Salesforce value before a new evaluation.")}
    toast = ""
    if notification.startswith("id-write:") and notification[9:] in ID_WRITEBACK_RESULTS:
        kind, text = ID_WRITEBACK_RESULTS[notification[9:]]
        notifications[notification] = ("Salesforce ID written" if kind == "verified" else "Salesforce ID not written", text + ".")
    if notification in notifications:
        title, message = notifications[notification]
        toast = "<section class='toast' role='status' aria-live='polite'><strong>" + escape(title) + "</strong><span>" + escape(message) + "</span></section>"
    if is_renewal:
        # Owner decision 2026-10-04: no primary action on a renewal CO.
        folded = renewal_preflight + comment_repair_action + renewal_comment_evaluation_action + manual_action
        next_step = (case4_panel + _renewal_plan_section(row)
                     + "<p class='lede renewal-note'>" + escape(RENEWAL_MANUAL_NOTE) + "</p>" + readiness
                     + ("<details class='more'><summary><h2 class='sum-h'>Sign-in and manual onboarding</h2>"
                        "<span class='note'>Production sign-in · Leonardo Development session check</span></summary>"
                        "<div class='body'>" + folded + "</div></details>" if folded else ""))
    else:
        next_step = (case4_panel + comment_repair_action + renewal_comment_evaluation_action
                     + renewal_preflight + readiness + manual_action)
    record = ("<details class='more'><summary><h2 class='sum-h'>Salesforce record</h2><span class='note'>"
              + str(len(DETAIL_DISPLAY_FIELDS)) + " fields</span></summary><div class='body'><dl>" + rows + "</dl></div></details>")
    account = row.get("Account_Name__c")
    main_html = ("<a class='crumb' href='/'>&larr; Open Onboardings</a>"
                 "<div class='page-head'><h1>" + escape(reference) + "</h1>"
                 + ("<span class='acct'>" + escape(account) + "</span>" if account else "") + _route_chip(row)
                 + "<div class='head-meta'><span>Read from Salesforce at " + escape(display_read_at()) + "</span>"
                 "<form method='get' action='/co/" + escape(reference) + "'><input type='hidden' name='refresh' value='1'>"
                 "<button class='ghost' type='submit'>Refresh</button></form></div></div>"
                 + _detail_summary(row) + toast + next_step + health + ids_html + record)
    return _app_shell(reference, main_html, active="onboardings")


def render_dashboard(selected_queue: str = "", scan_started: bool = False) -> str:
    """Read the open queue (live), the local run records, and the cached
    closed-onboardings history for the history tile, then render the dashboard.

    A queue read failure propagates (ReadUnavailable → the connection page).
    A history or run-record failure only degrades its own part of the page.
    Filtered views only peek at the history cache and never read Salesforce
    for it.
    """
    rows = queue_rows()
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
    return page_queue(rows, selected_queue, runner_state=runner_state,
                      runner_state_unavailable=runner_state_unavailable,
                      history=history, history_failed=history_failed, read_at=display_read_at(),
                      scan_started=scan_started)


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
            "Salesforce session. No queue data is cached or shown until the separately approved runner is ready.</p>"
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
DUPLICATE_RESULTS = frozenset({"duplicate_found", "duplicate_ambiguous", "duplicate_inventory_match"})
RUNNER_RESULT_MESSAGES: dict[str, tuple[str, str]] = {
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
    "ce_subscription_unavailable": ("blocked", "No Pentera Core Plus Commercial subscription was found for this account. No tenant was created. Verify the DealHub subscription."),
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
    ".banner-warn{background:var(--warn-bg)}.inv{overflow-x:auto}.inv th,.inv td{text-align:left!important;white-space:nowrap}"
    ".inv td:first-child{white-space:normal;min-width:160px}"
    # CO detail page (2026-10-04): summary strip, one next-step card, follow-ups,
    # tenant-health tiles, and folded secondary panels. Existing tokens only.
    ".page-head .acct{color:var(--muted);font-size:15px;font-weight:600}"
    ".summary{display:flex;flex-wrap:wrap;gap:8px 32px;margin:-8px 0 18px;color:var(--muted);font-size:12.5px}"
    ".summary b{display:block;color:var(--text);font-size:14px;font-weight:600;overflow-wrap:anywhere}"
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
    "@media(max-width:760px){dl.compact,.stat dl{display:block}.health{grid-template-columns:1fr}.summary{margin-top:0}"
    ".todo li{flex-wrap:wrap}}"
)


def _app_shell(title: str, main_html: str, refresh: str = "", active: str = "") -> str:
    """Wrap a page in the Pentera-styled shell (navy sidebar + light canvas)."""
    def nav(href: str, label: str, key: str) -> str:
        return "<a href='" + href + "'" + (" class='active'" if key == active else "") + ">" + label + "</a>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>" + refresh +
        "<title>" + escape(title) + "</title><style>" + PENTERA_CSS + "</style></head><body><div class='shell'>"
        "<aside class='side'><span class='brand'>PENTERA.</span><div class='brand-sub'>Surface Onboarding</div>"
        + nav("/", "Onboardings", "onboardings") + nav("/history", "History", "history")
        + nav("/inventory", "Tenants", "inventory")
        + nav("/connection", "Sessions", "connection") + _session_chip() + _operator_block() +
        "<div class='side-foot'>Attended · localhost only</div></aside>"
        "<main>" + main_html + "</main></div></body></html>"
    )


def _operator_block() -> str:
    operator = _current_operator.get()
    if not operator:
        return ""
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
    headline = headline or RUNNER_HEADLINES.get(result) or default_headline
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


_START_IN_PROGRESS_NOTE = (
    "<p class='note'><b>A run for an earlier source revision has not reported a result.</b> The CO changed in "
    "Salesforce while it was running, so no new run can start until it finishes.</p>")


def _progress_button(ref: str) -> str:
    """The one action while a run is open: the read-only status page (GET; ``ref`` is already escaped)."""
    return ("<form method='get' action='/attended/ce-only-runner-status'><input type='hidden' name='ref' value='"
            + ref + "'><button type='submit'>View progress</button></form>")


def _running_note(ref: str, record: dict[str, str]) -> str:
    started = " at " + escape(record["started_on"]) if record.get("started_on") else ""
    return ("<p class='note'>An attended run for this source revision was started" + started +
            " and has not reported a result. Do not start another.</p>" + _progress_button(ref))


def _reset_form(ref: str, record: dict[str, str]) -> str:
    """Re-arm a failed run; folded away for a duplicate, where the next step is a manual review."""
    form = ("<form class='reset-form' method='post' action='/attended/reset-ce-only-runner'>"
            "<input type='hidden' name='reference' value='" + ref + "'>"
            "<label><input type='checkbox' name='reset_authorized' value='1' required> "
            "Re-arm this failed run (nothing was created)</label>"
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
    ref = escape(reference)
    try:
        evaluation = evaluate_ce_only_fill_preflight(reference)
    except ReadUnavailable:
        return (
            "<section class='readiness source-blocked' aria-labelledby='onboard-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>!</span><div><h2 id='onboard-title'>Onboard " + ref + " (Credential Exposure)</h2>"
            "<p>The CE-only source could not be read. No browser was launched. Reconnect the attended Salesforce session and retry.</p></div></div></section>"
        )
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
    start_action = ""
    if same_revision and record.get("result"):
        kind, message = RUNNER_RESULT_MESSAGES.get(record["result"], ("blocked", "Result code <code>" + escape(record["result"]) + "</code>."))
        runner_note = (_outcome_banner(kind, message, record["result"], record.get("completed_on"), compact=True)
                       + _entered_license_note(record))
    elif same_revision:
        runner_note = _running_note(ref, record)
    elif start_blocker(record) == "run_in_progress":
        runner_note = _START_IN_PROGRESS_NOTE + _progress_button(ref)
    elif start_blocker(record) == "tenant_already_verified":
        runner_note = (_outcome_banner("success", RUNNER_RESULT_MESSAGES["readback_verified"][1],
                                       "readback_verified", record.get("completed_on"), compact=True)
                       + _entered_license_note(record))
    else:
        if record is not None:
            runner_note = ("<p class='note'>Previous attended run for a different source revision: <code>" +
                           escape(record.get("result", "no result recorded")) + "</code></p>")
        start_action = _ce_only_start_form(evaluation)
    blockers = ""
    if evaluation.blockers:
        blockers = ("<ul class='blockers'>" + "".join(
            "<li><b>Blocked:</b> " + escape(item.replace("_", " ")) + "</li>" for item in evaluation.blockers) + "</ul>")
    reset_action = ""
    if create_uncertain(record):
        runner_note = _uncertain_banner(ref, record)
        start_action = ""
    elif record is not None and record.get("result") and record["result"] != "readback_verified":
        reset_action = _reset_form(ref, record)
    return _onboard_card(
        "Credential Exposure onboarding", status_chip,
        ("<p class='lede'>" + lede + "</p>" if lede else "") + runner_note + blockers + start_action + reset_action
        + "<p class='meta-line'>Email domains <b>" + str(evaluation.email_domain_count) + "</b> · Source revision <code>"
        + escape(evaluation.source_revision) + "</code></p>")


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
    start_action = ""
    if same_revision and record.get("result"):
        kind, message = RUNNER_RESULT_MESSAGES.get(record["result"], ("blocked", "Result code <code>" + escape(record["result"]) + "</code>."))
        runner_note = (_outcome_banner(kind, message, record["result"], record.get("completed_on"), compact=True)
                       + _entered_license_note(record))
    elif same_revision:
        runner_note = _running_note(ref, record)
    elif record is not None and record.get("result") == "readback_verified":
        # A verified tenant exists; a later source revision never re-creates it.
        runner_note = (_outcome_banner("success", RUNNER_RESULT_MESSAGES["readback_verified"][1],
                                       "readback_verified", record.get("completed_on"), compact=True)
                       + _entered_license_note(record))
    elif start_blocker(record) == "run_in_progress":
        runner_note = _START_IN_PROGRESS_NOTE + _progress_button(ref)
    else:
        if record is not None:
            runner_note = ("<p class='note'>Previous attended run for a different source revision: <code>" +
                           escape(record.get("result", "no result recorded")) + "</code></p>")
        start_action = _surface_start_form(evaluation)
    blockers = ""
    if evaluation.blockers:
        blockers = ("<ul class='blockers'>" + "".join(
            "<li><b>Blocked:</b> " + RUNNER_RESULT_MESSAGES.get(code, ("blocked", ""))[1]
            + " <code>" + escape(code) + "</code></li>" for code in evaluation.blockers)
            + "".join("<li><b>" + escape(detail) + "</b></li>" for detail in evaluation.blocker_details) + "</ul>")
    try:
        reminders = load_attended_reminders().get(reference, {})
        reminder_error = ""
    except ReadUnavailable:
        reminders = {}
        reminder_error = "<p class='note'>The local reminders file could not be read; reminders are shown until it is repaired.</p>"
    reminder_html = ""
    if route == SURFACE_ENGINE and evaluation.core_plus_present and not reminders.get(REMINDER_FIELDS["ce_enabled"]):
        reminder_html += _reminder(CE_REMINDER_TEXT, "/attended/mark-ce-enabled", reference, "Mark CE enabled")
    if (record is not None and record.get("route") in SURFACE_ROUTES and record.get("result") == "readback_verified"
            and not reminders.get(REMINDER_FIELDS["scan_settings_off"])):
        reminder_html += _reminder(SCAN_REMINDER_TEXT, "/attended/mark-scan-settings-off", reference,
                                   "Mark scan settings turned off")
    if (record is not None and record.get("route") in SURFACE_ROUTES and record.get("result") == "readback_verified"
            and not reminders.get(REMINDER_FIELDS["operator_assigned"])):
        reminder_html += _reminder(OPERATOR_REMINDER_TEXT, "/attended/mark-operator-assigned", reference,
                                   "Mark Operator Account assigned")
    for kind, label in (("scan_settings_off", "Scan settings marked off"), ("ce_enabled", "Credential Exposure marked enabled"),
                        ("operator_assigned", "Operator Account marked assigned")):
        if reminders.get(REMINDER_FIELDS[kind]):
            reminder_html += ("<li class='done'><span class='chip chip-ok'>Done</span><span class='what'>" + label
                              + " on " + escape(reminders[REMINDER_FIELDS[kind]]) + ".</span></li>")
    followups = ""
    if reminder_html or reminder_error:
        followups = ("<section class='card' aria-labelledby='followups-title'><div class='card-head'>"
                     "<h2 id='followups-title' class='pill'>Follow-ups</h2><span class='note'>" + REMINDER_NOTE + "</span></div>"
                     + reminder_error + ("<ul class='todo'>" + reminder_html + "</ul>" if reminder_html else "") + "</section>")
    reset_action = ""
    if create_uncertain(record):
        runner_note = _uncertain_banner(ref, record)
        start_action = ""
    elif record is not None and record.get("result") and record["result"] != "readback_verified":
        reset_action = _reset_form(ref, record)
    scope_html = _surface_scope_facts(evaluation)
    if scope_html and start_action:
        scope_html = "<h3 class='sub-h'>Scope to review</h3>" + scope_html
    elif scope_html:
        scope_html = ("<details class='fold'><summary>Planned scope · " + _scope_summary_line(evaluation)
                      + "</summary>" + scope_html + "</details>")
    revision = evaluation.source_revision or "unavailable"
    return _onboard_card(
        "Surface + Credential Exposure onboarding" if route == CASE3_ENGINE else "Surface onboarding", status_chip,
        ("<p class='lede'>" + lede + "</p>" if lede else "") + runner_note + blockers + scope_html + start_action
        + reset_action + "<p class='meta-line'>Route <code>" + escape(route) + "</code> · Source revision <code>"
        + escape(revision) + "</code></p>") + followups


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
        body = ("<div class='card-head'><span class='pill'>Onboarding in progress</span><span class='chip chip-warn'>Running</span></div>"
                "<p>Started at <code>" + escape(started) + "</code>. This page refreshes every 5 seconds.</p>"
                "<p class='note'>In the automation Chrome window, complete SSO/MFA if prompted. The runner first checks Leonardo "
                "for an existing tenant, then fills and confirms the Add Account form. It never updates Salesforce.</p>")
    else:
        result = record["result"]
        completed = record.get("completed_on", "unknown")
        kind, message = RUNNER_RESULT_MESSAGES.get(result, ("blocked", "The runner finished with result code <code>" + escape(result) + "</code>."))
        # A finished run returns the operator to the CO page, where the same
        # green/red outcome is shown; the link works immediately.
        refresh = "<meta http-equiv='refresh' content='" + str(RUNNER_REDIRECT_SECONDS) + ";url=/co/" + ref + "'>"
        banner = _uncertain_banner(ref, record) if create_uncertain(record) else _outcome_banner(kind, message, result, completed)
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
                + "</code>). Refresh it on the Tenants page. Start still runs the live duplicate check.</div>")
    else:
        body = "<div class='banner banner-warn'>The CO source could not be read (<code>" + escape(result) + "</code>).</div>"
    main_html = ("<a class='crumb' href='/co/" + ref + "'>&larr; " + ref + "</a><div class='page-head'><h1>Duplicate "
                 "pre-check</h1><span class='chip chip-info'>" + label + "</span></div><section class='card'>" + body
                 + "<p class='note'>Inventory captured " + escape(str(report.get("captured_at") or "—")) + ". Rules: same "
                 "tenant name or primary domain (as the live check), plus the CO's domains against each tenant's "
                 "alternate domains.</p></section>")
    return _app_shell(reference + " duplicate pre-check", main_html, active="onboardings")


def render_inventory(query: str = "", notice: str = "") -> str:
    """Server-rendered tenant table (the CSP blocks scripts); ?q= filters by tenant name."""
    now = datetime.now(timezone.utc)
    refresh_form = ("<form method='post' action='/attended/inventory-refresh'><button type='submit'>Refresh inventory"
                    "</button></form>")
    head = ("<div class='page-head'><h1>Tenant inventory</h1><span class='chip chip-info'>"
            + escape(inventory.ENVIRONMENTS["dev"].label) + "</span>" + refresh_form + "</div>")
    note = ("<p class='note'>Read-only copy of Leonardo Development's tenant list, with personal data removed (no user "
            "names, emails, phones, or domain lists). It is informational: Start always re-checks Leonardo live for "
            "duplicates. Refresh also re-validates every onboarded CO from the same read.</p>")
    started = ("<div class='banner'><strong>Export started.</strong> It runs read-only in the automation browser; reload "
               "this page in a minute.</div>" if notice == "started" else "")
    try:
        payload = inventory.load_latest(inventory_root(), "dev", max_age=INVENTORY_DISPLAY_MAX_AGE, now=now)
    except inventory.InventoryError as error:
        body = "<section class='card'><p>" + escape(INVENTORY_ERROR_TEXT.get(error.reason, error.reason)) + "</p></section>"
        return _app_shell("Tenant inventory", head + started + body + note, active="inventory")
    try:
        readbacks = attended_leonardo_readbacks()
    except ReadUnavailable:
        readbacks = {}
    matched = inventory.match_readbacks(payload, readbacks)
    co_by_tenant = {tenant_id: ref for ref, tenant_id in matched["by_reference"].items()
                    if tenant_id not in (None, "conflict")}
    conflicts = sorted(ref for ref, tenant_id in matched["by_reference"].items() if tenant_id == "conflict")
    age = timedelta(seconds=int(payload.get("age_seconds", 0)))
    stale = age > INVENTORY_STALE_AFTER
    drift = payload.get("schema_drift") or {}
    drift_count = sum(len(drift.get(key, [])) for key in ("unknown", "missing", "type_changed"))
    summary = ("<div class='banner" + (" banner-warn" if stale else "") + "'><strong>"
               + escape(str(payload.get("environment_label") or inventory.ENVIRONMENTS["dev"].label))
               + "</strong> · Captured " + escape(str(payload.get("captured_at")))
               + " (" + str(int(age.total_seconds() // 3600)) + " h ago" + ("; stale, refresh before relying on it" if stale else "")
               + ") · " + str(payload.get("row_count")) + " tenants (" + str(payload.get("deleted_count")) + " deleted, "
               + str(payload.get("uuid_missing_count", 0)) + " without UUID) · "
               + str(drift_count) + " schema change(s)"
               + (" · <strong>ID conflict for " + escape(", ".join(conflicts)) + "</strong>: re-check live" if conflicts else "")
               + "</div>")
    needle = " ".join(query.split()).casefold()[:80]
    rows_html = ""
    shown = 0
    for tenant in sorted(payload.get("tenants") or [], key=lambda t: str(t.get("account_name") or "").casefold()):
        name = str(tenant.get("account_name") or "")
        if needle and needle not in name.casefold():
            continue
        shown += 1
        licence = tenant.get("license") or {}
        scan = tenant.get("scan") or {}
        ref = co_by_tenant.get(tenant.get("id"))
        co_cell = ("<a href='/co/" + escape(ref) + "'>" + escape(ref) + "</a>") if ref else "<span class='muted'>no CO</span>"
        state = "deleted" if tenant.get("is_deleted") else ("enabled" if tenant.get("enabled") else "disabled")
        rows_html += (
            "<tr><td>" + escape(name) + "</td><td>" + co_cell + "</td><td><code>" + escape(str(tenant.get("id") or ""))
            + "</code>" + ("" if tenant.get("account_uuid") else " <span class='chip chip-warn'>no UUID</span>")
            + "</td><td>" + escape(str(licence.get("type") or "—")) + "</td><td>"
            + _inventory_date(licence.get("start_date")) + " → " + _inventory_date(licence.get("expiration_date"))
            + "</td><td>" + escape(str(scan.get("last_recon_scan_utc") or "—")[:16].replace("T", " ")) + "</td><td>"
            + _inventory_duration(scan.get("duration_ms")) + "</td><td>" + escape(str(scan.get("status") or "—"))
            + "</td><td>" + state + "</td></tr>")
    search = ("<form method='get' action='/inventory' class='actions'><input name='q' value='" + escape(query[:80])
              + "' placeholder='Filter by tenant name'> <button class='ghost' type='submit'>Filter</button></form>")
    table = ("<section class='card'>" + search + "<p class='note'>" + str(shown) + " shown. Last scan times are UTC. "
             "Snapshot file: " + escape(str(payload.get("environment"))) + " folder (leonardo-dev-inventory-*) under %LOCALAPPDATA%\\SurfaceOnboarding"
             "\\leonardo-inventory (CSV beside it).</p><div class='qh-table inv'><table><thead><tr><th>Tenant</th><th>CO</th>"
             "<th>ID</th><th>Licence</th><th>Start → End</th><th>Last scan (UTC)</th><th>Duration</th><th>Status</th>"
             "<th>Account</th></tr></thead><tbody>" + rows_html + "</tbody></table></div></section>")
    return _app_shell("Tenant inventory", head + started + summary + table + note, active="inventory")


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
    "/attended/start-surface-runner": "start",
    "/attended/validate": "read", "/attended/scan-status-refresh": "read", "/attended/verify-uncertain": "read",
}


POST_ROUTES = frozenset({
    "/attended/prepare-sessions", "/attended/session-recheck", "/attended/inventory-refresh",
    "/attended/duplicate-precheck",
    "/attended/salesforce-login", "/attended/leonardo-dev-session-check", "/attended/leonardo-dev-session-bootstrap",
    "/attended/leonardo-dev-session-reset", "/attended/leonardo-dev-browser-close",
    "/attended/rerun-comment-evaluation", "/attended/rerun-co0745-renewal-evaluation",
    "/attended/rerun-ce-only-fill-preflight", "/attended/rerun-co0702-fill-preflight",
    "/attended/start-ce-only-runner", "/attended/start-co0702-ce-only-runner", "/attended/reset-ce-only-runner",
    "/attended/start-surface-runner", "/attended/mark-scan-settings-off", "/attended/mark-ce-enabled",
    "/attended/mark-operator-assigned", "/attended/confirm-comment-update", "/attended/production-renewal-preflight",
    "/attended/leonardo-session-check", "/attended/start-manual-onboarding", "/attended/scan-status-refresh", "/attended/scan-status-refresh-all",
    "/attended/validate", "/attended/validate-all", "/attended/verify-uncertain",
    "/attended/salesforce-id-writeback-review", "/attended/salesforce-id-writeback-confirm",
})


# "same-origin", not "no-referrer": under no-referrer a browser sends "Origin: null"
# on this dashboard's own form posts (verified 2026-10-03 in Chromium), which
# request_origin_problem must refuse, so every button returned 403. same-origin
# still sends nothing to other sites.
REFERRER_POLICY = "same-origin"


def request_origin_problem(command: str, host: str | None, origin: str | None, fetch_site: str | None,
                           port: int) -> str | None:
    """Why a request must be refused before any handler runs, else None (review item 6, 2026-10-01).

    The Host header must be this dashboard's own loopback address (blocks DNS
    rebinding reads). A POST must not come from another site: a present
    Origin must be this dashboard, and without Origin a present
    Sec-Fetch-Site must be same-origin or none. Browsers always send one of
    them on a cross-site form post; a local script that sends neither is not
    a cross-site page. SURFACE_ONBOARDING_ALLOWED_HOSTS adds exact host:port
    values (for example an SSH-tunnel port), comma-separated.
    """
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
    def parse_request(self) -> bool:
        """Refuse foreign Host / cross-site POST requests before any page or action runs."""
        if not super().parse_request():
            return False
        problem = request_origin_problem(self.command, self.headers.get("Host"), self.headers.get("Origin"),
                                         self.headers.get("Sec-Fetch-Site"), self.server.server_address[1])
        if problem is not None:
            self.send_error(HTTPStatus.FORBIDDEN, "Request refused")
            return False
        return True
    def send_page(self, status: HTTPStatus, page: str) -> None:
        data = page.encode(); self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Referrer-Policy", REFERRER_POLICY); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"); self.end_headers(); self.wfile.write(data)
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
        clear_attempt = ATTEMPT_COOKIE + "=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        if state == "signed_in":
            self.send_redirect_with_cookies("/connection", [
                SESSION_COOKIE + "=" + str(value) + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + str(DASHBOARD_SESSION_SECONDS),
                clear_attempt])
            return
        self.send_page(HTTPStatus.OK, page_login(state, value))

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
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
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                selected_queue = parse_qs(parsed.query).get("queue", [""])[0]
                scan_started = parse_qs(parsed.query).get("scan", [""])[0] == "started"
                self.send_page(HTTPStatus.OK, render_dashboard(selected_queue, scan_started)); return
            if path == "/connection":
                self.send_page(HTTPStatus.OK, page_salesforce_unavailable(failed=False)); return
            if path == "/history":
                self.send_page(HTTPStatus.OK, render_history()); return
            if path == "/inventory":
                params = parse_qs(parsed.query)
                self.send_page(HTTPStatus.OK, render_inventory(params.get("q", [""])[0], params.get("refresh", [""])[0]))
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
                row = detail_row(match.group(1))
                self.send_page(HTTPStatus.OK, page_detail(match.group(1), row, notice, surface_commercial_readiness(row)))
                return
            self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
        except ReadUnavailable:
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
            ATTEMPT_COOKIE + "=" + token + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + str(LOGIN_ATTEMPT_SECONDS)])

    def do_POST(self) -> None:
        # Any action may change Salesforce or local state; later pages read fresh.
        clear_display_cache()
        path = urlsplit(self.path).path
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
            self.send_redirect_with_cookies("/login", [SESSION_COOKIE + "=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"])
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
    ThreadingHTTPServer((listener_host, listener_port), Handler).serve_forever()
