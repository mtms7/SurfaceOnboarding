"""Desktop-only, attended dashboard for Salesforce's Open_Onboardings view.

No scheduler, cache, write operation, VM transport, or generic query is
provided. Each request uses the operator's current Salesforce CLI session and
keeps data in memory only for rendering that response.
"""
from __future__ import annotations

from html import escape
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import subprocess
import sys
from threading import Lock
from time import monotonic
import webbrowser
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase1_validator.onboarding_comment_dates import extract_dealhub_dates
from phase1_validator.surface_source_readiness import evaluate_new_surface_source
from phase2_leonardo.case4_comment_validation import (
    ENGINE_VALUE as CASE4_ENGINE_VALUE,
    validate_case4_onboarding_comments,
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
LEONARDO_READBACK_STATES = frozenset({"Account Scanning"})
CASE4_PRODUCT = "Surface & Credential Exposure"
CASE4_TYPE = "Renewal of Surface + New Credential Exposure Module"
_manual_start_acks: dict[str, tuple[str, str, float]] = {}
_manual_start_lock = Lock()
_view_id_lock = Lock()
_open_onboardings_view_id: str | None = None
_salesforce_login_lock = Lock()
_salesforce_login_process: subprocess.Popen[str] | None = None
_comment_update_acks: dict[str, tuple[str, str, float]] = {}
_comment_update_lock = Lock()


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
        try:
            _salesforce_login_process = subprocess.Popen(
                [salesforce_cli_command(), "org", "login", "web", "--alias", "surface-onboarding", "--set-default"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            _salesforce_login_process = None
            return False


def attended_leonardo_readbacks() -> dict[str, dict[str, str]]:
    """Load explicit local Leonardo Development evidence; never write Salesforce."""
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
        done = subprocess.run([salesforce_cli_command(), *args], capture_output=True, text=True, timeout=35, check=False)
        if done.returncode or len(done.stdout.encode()) > 512 * 1024:
            raise ReadUnavailable()
        return json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ReadUnavailable() from exc


def sf_write_json(args: list[str]) -> object:
    """Run the sole attended write command without retaining CLI output."""
    try:
        done = subprocess.run([salesforce_cli_command(), *args], capture_output=True, text=True, timeout=35, check=False)
        if done.returncode or len(done.stdout.encode()) > 512 * 1024:
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
    try:
        view_id = open_onboardings_view_id()
        result = sf_json(["api", "request", "rest", f"/services/data/v67.0/sobjects/Customer_Onboarding__c/listviews/{view_id}/results", "--method", "GET"])
        rows: list[dict[str, str | None]] = []
        for record in result["records"]:  # type: ignore[index]
            values = {column["fieldNameOrPath"]: column["value"] for column in record["columns"]}
            if not set(QUEUE_FIELDS) <= set(values) or not isinstance(values["Name"], str) or not REFERENCE.fullmatch(values["Name"]): raise ReadUnavailable()
            rows.append({field: value if isinstance(value, str) else None for field, value in values.items() if field in QUEUE_FIELDS})
        readbacks = attended_leonardo_readbacks()
        for row in rows:
            readback = readbacks.get(row["Name"] or "")
            if readback is not None:
                row["Local_Leonardo_State"] = readback["leonardo_state"]
        return rows
    except (KeyError, TypeError):
        raise ReadUnavailable() from None


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


def page_queue(rows: list[dict[str, str | None]], selected_queue: str = "") -> str:
    """Render a compact operational overview; details load only after a click."""
    def bucket(row: dict[str, str | None]) -> tuple[int, str, str, str]:
        if row.get("Local_Leonardo_State") == "Account Scanning":
            return (0, "Account Scanning", "scanning", "Leonardo evidence indicates a tenant is scanning.")
        if row.get("Onboarding_Approval_Status__c") == "Approved" or row.get("Onboarding_Stage__c") == "Request Approved":
            return (1, "Ready to onboard", "ready", "Approved source records awaiting the guarded Leonardo workflow.")
        if row.get("Onboarding_Approval_Status__c") == "Pending" and row.get("Onboarding_Stage__c") == "New":
            return (2, "Needs subscription validation", "validation", "New COs awaiting an individual DealHub product and term check.")
        return (3, "Manual review", "review", "Records outside the standard new/pending or approved flow.")

    grouped: dict[tuple[int, str, str, str], list[dict[str, str | None]]] = {}
    for row in rows:
        grouped.setdefault(bucket(row), []).append(row)
    allowed_queues = {"scanning", "ready", "validation", "review"}
    selected_queue = selected_queue if selected_queue in allowed_queues else ""
    nav = "<a href='/'><b>" + str(len(rows)) + "</b>All queues</a>" + "".join(
        f"<a href='/?queue={css}'><b>{sum(len(items) for key, items in grouped.items() if key[2] == css)}</b>{escape(label)}</a>"
        for label, css in (("Account Scanning", "scanning"), ("Ready to onboard", "ready"),
                           ("Needs validation", "validation"), ("Manual review", "review"))
    ) + "<a href='/connection'><b>✓</b>Salesforce connection</a>"
    sections = ""
    for key in sorted(grouped):
        _, label, css, helper = key
        if selected_queue and css != selected_queue:
            continue
        cards = ""
        for row in sorted(grouped[key], key=lambda item: item.get("Submission_Date__c") or ""):
            reference = escape(row["Name"] or "")
            cards += (
                f"<li><a class='case-card' href='/co/{reference}'><span class='case-id'>{reference}</span>"
                f"<span class='state {css}'>{escape(row.get('Onboarding_Approval_Status__c') or 'Approval status not populated')}</span>"
                f"<strong>{escape(row.get('Account__r.Name') or 'Account unavailable')}</strong>"
                f"<small>{escape(row.get('Onboarding_Product__c') or 'Product unavailable')}</small>"
                f"<small>Stage: {escape(row.get('Onboarding_Stage__c') or 'Not set')} · Submitted {escape(display_date(row.get('Submission_Date__c')))}</small>"
                "</a></li>"
            )
        sections += f"<section id='queue-{css}'><header><div><p>QUEUE</p><h2>{escape(label)} <span>{len(grouped[key])}</span></h2><small>{escape(helper)}</small></div></header><ul>{cards}</ul></section>"
    return (
        "<!doctype html><title>Surface Onboarding</title><style>"
        "*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#162031;font:14px/1.45 system-ui,sans-serif}.shell{display:grid;grid-template-columns:210px 1fr;min-height:100vh}.rail{padding:28px 20px;background:#02081e;color:#dbe5f5}.brand{font-size:19px;font-weight:750;color:#fff}.brand small{display:block;margin-top:4px;color:#8fa4c4;font-size:11px;font-weight:500}.rail nav{display:grid;gap:8px;margin-top:36px}.rail a{padding:9px 10px;border-radius:7px;color:#b9c8df;text-decoration:none}.rail a:hover{background:#102446;color:#fff}.rail b{display:block;color:#fff;font-size:19px}.content{max-width:1400px;padding:34px 42px}.eyebrow,small{color:#6d7888}.eyebrow{margin:0;font-size:11px;letter-spacing:.08em}h1{margin:4px 0 5px;font-size:29px}button{margin:16px 0;padding:8px 12px;border:1px solid #2869c7;border-radius:6px;background:#fff;color:#1554a2;font:inherit;font-weight:650;cursor:pointer}.queues{display:grid;gap:26px}section header{display:flex;justify-content:space-between;margin-bottom:10px}section header p{margin:0;color:#72839a;font-size:10px;letter-spacing:.1em;font-weight:700}h2{margin:1px 0;font-size:18px}h2 span{color:#4d78b6;font-weight:600}ul{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:12px;margin:0;padding:0;list-style:none}.case-card{display:grid;gap:5px;min-height:138px;padding:16px;border:1px solid #e2e8f0;border-radius:9px;background:#fff;color:#162031;box-shadow:0 2px 8px #1a2d4a0a;text-decoration:none}.case-card:hover{border-color:#6b9fdd;box-shadow:0 4px 14px #1a2d4a18}.case-id{color:#1d65bd;font-weight:750}.state{justify-self:start;padding:2px 7px;border-radius:20px;font-size:11px;font-weight:700}.state.scanning{background:#e8f6fb;color:#08708e}.state.ready{background:#e7f6ed;color:#167544}.state.validation{background:#fff3d9;color:#805b09}.state.review{background:#fcebec;color:#a32d37}@media(max-width:800px){.shell{grid-template-columns:1fr}.rail{padding:16px}.rail nav{grid-template-columns:repeat(2,1fr);margin-top:14px}.content{padding:24px 18px}}</style>"
        "<div class='shell'><aside class='rail'><div class='brand'>PENTERA<small>Surface onboarding</small></div><nav>" + nav + "</nav></aside>"
        "<main class='content'><p class='eyebrow'>ATTENDED · LOCALHOST ONLY</p><h1>" + (escape(selected_queue.replace("_", " ").title()) if selected_queue else "Onboarding overview") + "</h1><p>Open a CO to run its bounded, read-only commercial validation.</p><form method='get' action='/'><input type='hidden' name='queue' value='" + escape(selected_queue) + "'><button type='submit'>Refresh overview</button></form><div class='queues'>" + sections + "</div></main></div>"
    )


def page_detail(reference: str, row: dict[str, str | None], notification: str = "", commercial_readiness: dict[str, object] | None = None) -> str:
    rows = "".join(f"<dt>{escape(label)}</dt><dd>{escape(row.get(field) or 'Not populated')}</dd>" for label, field in DETAIL_DISPLAY_FIELDS)
    comment_validation = extract_dealhub_dates(row.get("Onboarding_Comments__c"))
    case4_panel = ""
    renewal_preflight = ""
    if "renewal" in (row.get("Onboarding_Type__c") or "").casefold():
        renewal_preflight = (
            "<section class='login-preflight' aria-labelledby='renewal-preflight-title'><div><h2 id='renewal-preflight-title'>Production renewal account validation</h2>"
            "<p>Step 1: open the production BackOffice login and complete SSO/MFA manually. This preflight stops before any tenant search, edit, or save action.</p>"
            "<p class='login-safety'>The subsequent read-only account-existence lookup requires an approved desktop Playwright runtime and a reviewed production page schema. No credentials, cookies, MFA codes, or browser state are read or retained.</p></div>"
            "<form method='post' action='/attended/production-renewal-preflight'><input type='hidden' name='reference' value='" + escape(reference) + "'><button type='submit'>Open production sign-in</button></form></section>"
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
            "<form method='post' action='/attended/rerun-comment-evaluation'><input type='hidden' name='reference' value='CO-0741'><button type='submit'>Re-run evaluation</button></form></section>"
            )
        else:
            comment_repair_action = ("<section class='manual-action'><div><h2>Re-run DealHub comment evaluation</h2>"
                                     "<p>This repair action is unavailable because CO-0741 no longer has the required empty-comment, New, Pending pre-state.</p></div>"
                                     "<button type='button' disabled aria-disabled='true'>Re-run evaluation unavailable</button></section>")
    source_ready = source_ready_to_onboard(row)
    commercial_ready = bool(commercial_readiness and commercial_readiness.get("commercial_ready"))
    commercial_manual_review = bool(commercial_readiness and commercial_readiness.get("manual_review_required"))
    if source_ready:
        comment_summary = (
            "The onboarding-comment subscription period is also validated."
            if comment_validation["ready_for_cse_review"]
            else "The onboarding-comment subscription period needs separate review before execution."
        )
        readiness = (
            "<section class='readiness source-ready' aria-labelledby='readiness-title'><div class='readiness-heading'>"
            "<span class='readiness-icon' aria-hidden='true'>✓</span><div><h2 id='readiness-title'>Source ready to onboard</h2>"
            "<p>Salesforce approval is validated. " + comment_summary + "</p></div></div>"
            "<details><summary>Why Leonardo creation is not enabled yet</summary><ul>"
            "<li>Approved route mapping and scope-threshold evaluation</li>"
            "<li>Leonardo Development authentication, authority, and duplicate checks</li>"
            "<li>Idempotency binding, named human approval, and read-after-write plan</li>"
            "</ul></details></section>"
        )
        session_check = (
            "<section class='login-preflight' aria-labelledby='login-preflight-title'><div><h2 id='login-preflight-title'>Leonardo Development session check</h2>"
            "<p>Open the exact Development tenant-management route. Your browser will show tenant management when the current session is active or redirect to SSO/MFA when it is not.</p>"
            "<p class='login-safety'>This dashboard cannot inspect VPN state, credentials, cookies, or browser state. The resulting browser page is the attended authentication signal; it does not clear any authority, duplicate, mapping, or execution gate.</p></div>"
            "<form method='post' action='/attended/leonardo-session-check'><input type='hidden' name='reference' value='" + escape(reference) + "'><button type='submit'>Check Leonardo Development session</button></form></section>"
        )
        nonce = manual_start_ack_nonce(reference, row)
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
                "<button type='submit'>Start manual onboarding</button></form>"
                "<p class='manual-blocker'>This opens tenant management only. It does not fill, submit, create, or authorize a tenant.</p></section>"
            )
        manual_action = session_check + manual_action
        if is_case4:
            manual_action = (
                "<section class='manual-action' aria-labelledby='case4-route-title'><div><h2 id='case4-route-title'>Case 4 Leonardo workflow</h2>"
                "<p>Source is ready to onboard, but the Case 4 route remains unmapped and cannot start Leonardo onboarding.</p></div>"
                "<button type='button' disabled aria-disabled='true'>Case 4 route blocked</button>"
                "<p class='manual-blocker'>Blocked independently from Salesforce source readiness: owner-approved Case 4 mapping is required.</p></section>"
            )
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
    local_readback = ""
    if readback is not None:
        local_readback = ("<section class='readiness' aria-labelledby='leonardo-readback-title'><h2 id='leonardo-readback-title'>Leonardo Development readback</h2>"
                          "<p>Local operator evidence only; Salesforce remains unchanged.</p><dl>"
                          f"<dt>Leonardo state</dt><dd>{escape(readback['leonardo_state'])}</dd>"
                          f"<dt>Surface Account ID</dt><dd>{escape(readback['surface_account_id'])}</dd>"
                          f"<dt>Account UUID</dt><dd>{escape(readback['account_uuid'])}</dd>"
                          f"<dt>Observed</dt><dd>{escape(readback['observed_on'])}</dd></dl></section>")
    notifications = {"verified": ("Update verified", "CO-0741 was refreshed from Salesforce. Comments, Stage, and Approval Status are verified."), "blocked": ("Update blocked", "No verified update was completed. Reconcile the current Salesforce value before a new evaluation.")}
    toast = ""
    if notification in notifications:
        title, message = notifications[notification]
        toast = "<section class='toast' role='status' aria-live='polite'><strong>" + escape(title) + "</strong><span>" + escape(message) + "</span></section>"
    return "<!doctype html><title>" + escape(reference) + "</title><style>*{box-sizing:border-box}body{margin:0;background:#f3f3f3;color:#181818;font:14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}main{max-width:1080px;margin:auto;padding:28px 32px 40px}a{color:#0176d3;text-decoration:none;font-weight:600}a:hover{text-decoration:underline}h1{margin:16px 0;font-size:1.625rem;line-height:1.25}.toast{position:fixed;z-index:2;right:24px;top:20px;display:grid;gap:2px;max-width:430px;padding:13px 16px;border:1px solid #2e844a;border-left:4px solid #2e844a;border-radius:5px;background:#fff;box-shadow:0 3px 10px #0003}.toast span{color:#514f4d}.readiness,.login-preflight{margin:0 0 18px;padding:16px;border:1px solid;border-left-width:4px;border-radius:4px;background:#fff}.source-ready{border-color:#2e844a}.source-blocked{border-color:#ba0517}.readiness-heading{display:flex;gap:12px;align-items:flex-start}.readiness-icon{display:grid;place-items:center;flex:0 0 22px;width:22px;height:22px;border-radius:50%;color:#fff;font-size:.875rem;font-weight:800}.source-ready .readiness-icon{background:#2e844a}.source-blocked .readiness-icon{background:#ba0517}.readiness h2,.login-preflight h2,.manual-action h2{margin:0;color:#3e3e3c;font-size:1rem}.readiness p,.login-preflight p{margin:3px 0 0;color:#514f4d}.readiness details{margin:12px 0 0;color:#3e3e3c}.readiness summary{cursor:pointer;color:#0176d3;font-weight:600}.readiness ul{margin:8px 0 0;padding-left:20px}.readiness li{margin:4px 0}.login-preflight{display:grid;grid-template-columns:1fr auto;gap:8px 18px;align-items:center;border-color:#0176d3}.login-preflight form{margin:0}.login-preflight button{padding:8px 12px;border:1px solid #0176d3;border-radius:4px;background:#0176d3;color:#fff;font:inherit;font-weight:600;cursor:pointer}.login-preflight button:hover{background:#014486}.login-safety{grid-column:1/-1;font-size:.8125rem}.manual-action{display:grid;grid-template-columns:1fr auto;gap:8px 18px;align-items:center;margin:0 0 18px;padding:16px;border:1px solid #dddbda;border-radius:4px;background:#fff}.manual-action p{margin:3px 0 0;color:#514f4d}.manual-action button{padding:8px 12px;border:1px solid #c9c7c5;border-radius:4px;background:#f3f3f3;color:#706e6b;font:inherit;font-weight:600;cursor:not-allowed}.manual-blocker{grid-column:1/-1;font-size:.8125rem}dl{display:grid;grid-template-columns:minmax(190px,260px) 1fr;margin:0;border:1px solid #dddbda;background:#fff}dt,dd{margin:0;padding:12px 16px;border-bottom:1px solid #dddbda}dt{background:#f3f2f2;color:#3e3e3c;font-size:.8125rem;font-weight:700}dd{white-space:pre-wrap;overflow-wrap:anywhere}dt:nth-last-of-type(1),dd:last-child{border-bottom:0}@media(max-width:700px){main{padding:20px 16px}.toast{left:16px;right:16px;top:12px;max-width:none}.login-preflight,.manual-action{grid-template-columns:1fr}.login-preflight form,.manual-action button{justify-self:start}dl{display:block}dt{border-bottom:0;padding-bottom:4px}dd{padding-top:4px}}</style><main><a href='/'>← Open Onboardings</a><h1>" + escape(reference) + "</h1>" + toast + case4_panel + comment_repair_action + renewal_preflight + readiness + manual_action + local_readback + "<dl>" + rows + "</dl></main>"


def page_salesforce_unavailable() -> str:
    """Render the attended connection section using the dashboard shell."""
    return (
        "<!doctype html><title>Salesforce connection</title><style>"
        "*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#162031;font:14px/1.45 system-ui,sans-serif}.shell{display:grid;grid-template-columns:210px 1fr;min-height:100vh}.rail{padding:28px 20px;background:#02081e;color:#dbe5f5}.brand{font-size:19px;font-weight:750;color:#fff}.brand small{display:block;margin-top:4px;color:#8fa4c4;font-size:11px;font-weight:500}.rail nav{display:grid;gap:8px;margin-top:36px}.rail a{padding:9px 10px;border-radius:7px;color:#b9c8df;text-decoration:none}.rail a.active{background:#102446;color:#fff}.rail b{display:block;color:#fff;font-size:19px}.content{max-width:920px;padding:34px 42px}.eyebrow{margin:0;color:#72839a;font-size:11px;letter-spacing:.08em}h1{margin:4px 0 8px;font-size:29px}.panel{max-width:700px;margin-top:24px;padding:22px;border:1px solid #d9e3f0;border-left:4px solid #3678c5;border-radius:9px;background:#fff;box-shadow:0 2px 8px #1a2d4a0a}.status{display:inline-block;padding:3px 8px;border-radius:20px;background:#fff3d9;color:#805b09;font-size:11px;font-weight:750}.panel h2{margin:14px 0 5px;font-size:19px}.panel p{color:#526174}.panel button{margin-top:10px;padding:9px 13px;border:1px solid #2869c7;border-radius:6px;background:#2869c7;color:#fff;font:inherit;font-weight:700;cursor:pointer}.panel button:hover{background:#1554a2}.note{font-size:12px}@media(max-width:800px){.shell{grid-template-columns:1fr}.rail{padding:16px}.rail nav{grid-template-columns:repeat(2,1fr);margin-top:14px}.content{padding:24px 18px}}</style>"
        "<div class='shell'><aside class='rail'><div class='brand'>PENTERA<small>Surface onboarding</small></div><nav>"
        "<a href='/'><b>—</b>All queues</a><a href='/?queue=scanning'><b>—</b>Account Scanning</a><a href='/?queue=ready'><b>—</b>Ready to onboard</a><a href='/?queue=validation'><b>—</b>Needs validation</a><a href='/?queue=review'><b>—</b>Manual review</a><a class='active' href='/connection'><b>!</b>Salesforce connection</a>"
        "</nav></aside><main class='content'><p class='eyebrow'>ATTENDED · LOCALHOST ONLY</p><h1>Salesforce connection</h1><p>Reconnect the attended source session, then return to the queues.</p><section class='panel'><span class='status'>CONNECTION REQUIRED</span><h2>Salesforce data is unavailable</h2><p>The dashboard could not complete its attended Salesforce read. No queue data is cached or shown while the session is unavailable.</p>"
        "<form method='post' action='/attended/salesforce-login'><button type='submit'>Sign in to Salesforce</button></form>"
        "<p class='note'>This opens the standard Salesforce CLI browser sign-in. Complete SSO/MFA in that browser, then select <strong>Return to queues</strong>. The dashboard never receives or stores credentials, cookies, MFA codes, or CLI output.</p></section></main></div>"
    )


def page_salesforce_login_opened() -> str:
    return (
        "<!doctype html><title>Salesforce sign-in opened</title><style>body{max-width:680px;margin:48px auto;padding:0 22px;font:16px/1.5 system-ui,sans-serif;color:#162031}a{display:inline-block;margin-top:12px;padding:9px 13px;border-radius:6px;background:#2869c7;color:#fff;font-weight:700;text-decoration:none}.note{color:#526174}</style>"
        "<main><h1>Complete Salesforce sign-in</h1><p>The Salesforce browser sign-in was opened. Complete SSO/MFA there.</p><p class='note'>The dashboard does not receive or store browser credentials, cookies, or MFA codes.</p><a href='/'>Return to queues</a><p><a href='/connection' style='background:none;color:#1554a2;padding:0'>Back to Salesforce connection</a></p></main>"
    )


def page_comment_update_confirmation(evaluation: CommentUpdateEvaluation, nonce: str) -> str:
    return (
        "<!doctype html><title>Confirm CO-0741 comment update</title><style>body{max-width:760px;margin:48px auto;padding:0 22px;font:16px/1.5 system-ui,sans-serif;color:#181818}section{border:1px solid #0176d3;border-left:4px solid #0176d3;border-radius:4px;padding:18px;background:#fff}code{font-weight:700}button{padding:9px 13px;border:1px solid #0176d3;border-radius:4px;background:#0176d3;color:#fff;font:inherit;font-weight:600;cursor:pointer}.note{color:#514f4d;font-size:.9rem}</style>"
        "<main><h1>Confirm Onboarding Comments update</h1><section><p>CO-0741 passed the selected DealHub term validation.</p>"
        "<p>Proposed value: <code>" + escape(evaluation.proposed_comment) + "</code></p>"
        "<p class='note'>Confirming updates only three CO-0741 fields: Onboarding Comments, Stage to Request Approved, and Approval Status to Approved. It then immediately reads all three back. This expires in 10 minutes and is invalidated by a CO or selected subscription revision change.</p>"
        "<form method='post' action='/attended/confirm-comment-update'><input type='hidden' name='reference' value='CO-0741'><input type='hidden' name='nonce' value='" + escape(nonce) + "'><button type='submit'>Confirm update in Salesforce</button></form>"
        "<p><a href='/co/CO-0741'>Cancel and return to CO-0741</a></p></section></main>"
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None: pass
    def send_page(self, status: HTTPStatus, page: str) -> None:
        data = page.encode(); self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Referrer-Policy", "no-referrer"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"); self.end_headers(); self.wfile.write(data)
    def send_redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER); self.send_header("Location", location); self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Referrer-Policy", "no-referrer"); self.end_headers()
    def do_GET(self) -> None:
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/":
                selected_queue = parse_qs(parsed.query).get("queue", [""])[0]
                self.send_page(HTTPStatus.OK, page_queue(queue_rows(), selected_queue)); return
            if path == "/connection":
                self.send_page(HTTPStatus.OK, page_salesforce_unavailable()); return
            match = re.fullmatch(r"/co/(CO-[0-9]{4,10})", path)
            notice = parse_qs(parsed.query).get("comment-update", [""])[0]
            if notice not in {"verified", "blocked"}: notice = ""
            if match:
                row = detail_row(match.group(1))
                self.send_page(HTTPStatus.OK, page_detail(match.group(1), row, notice, surface_commercial_readiness(row)))
                return
            self.send_page(HTTPStatus.NOT_FOUND, "<!doctype html><title>Not found</title>")
        except ReadUnavailable:
            self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, page_salesforce_unavailable())

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/attended/salesforce-login":
            if start_attended_salesforce_login():
                self.send_page(HTTPStatus.OK, page_salesforce_login_opened())
            else:
                self.send_page(HTTPStatus.SERVICE_UNAVAILABLE, "<!doctype html><title>Salesforce sign-in unavailable</title><p>Salesforce CLI could not be started. Use the approved desktop launcher or repair the local Salesforce CLI session.</p>")
            return
        form = post_form(self)
        reference = exact_form_value(form, "reference")
        if reference is None or not REFERENCE.fullmatch(reference):
            self.send_page(HTTPStatus.BAD_REQUEST, "<!doctype html><title>Invalid request</title><p>Return to the dashboard and retry the attended step.</p>")
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
