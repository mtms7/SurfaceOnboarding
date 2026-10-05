"""Serve the static UI preview on loopback for an SSH-tunnel-only review.

This is deliberately separate from the onboarding application and must never be
installed as a service. It serves no live data, takes no actions, accepts no
host override, and binds only to 127.0.0.1.

It renders the current attended dashboard design (queue, History, CO pages)
from synthetic CO-DEMO records. While a page renders, every reader the
dashboard and runner use -- Salesforce CLI, Leonardo browser launches, and the
local readback, runner-state, scan-status, writeback, and reminder files -- is
replaced by a synthetic value or a fail-closed stub, under one lock, and then
restored. Every button is disabled and the handler accepts GET only.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
from threading import Lock
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

if __package__ in (None, ""):
    # Run as a script (python tools/serve_loopback_preview.py): import from the project root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.attended_ce_only_playwright as runner  # noqa: E402
import tools.serve_attended_open_onboardings_dashboard as dashboard  # noqa: E402


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
QUEUE_KEYS = frozenset(key for key, _label, _helper in dashboard.QUEUE_DEFINITIONS)
REVISION = "2026-10-01T09:00:00.000+0000"
DEMO_SURFACE_ID, DEMO_UUID = "0123456789abcdef01234567", "0123456789abcdef0123456789abcdef"
DEMO_OTHER_ID = "fedcba9876543210fedcba98"
BANNER = ("<div style='margin:0 0 16px;padding:10px 14px;border-radius:8px;background:#fff1b8;color:#7a4a00;"
          "font-size:13px'><b>Read-only design preview · synthetic data · Not deployed.</b> CO-DEMO records are "
          "invented; no Salesforce, Leonardo, or local onboarding data is read, and every action is disabled.</div>")


def _detail(name: str, product: str, onboarding_type: str, approval: str, stage: str, **fields: str | None) -> dict[str, str | None]:
    row: dict[str, str | None] = {field: None for field in dashboard.DETAIL_FIELDS}
    row.update({"Name": name, "Onboarding_Product__c": product, "Onboarding_Type__c": onboarding_type,
                "Onboarding_Approval_Status__c": approval, "Onboarding_Stage__c": stage, "LastModifiedDate": REVISION,
                "Account__c": "001000000000DEMO", "Account_Name__c": f"Example Customer {name[-1]}",
                "Primary_User_Name__c": "Example User", "Main_Domain__c": f"customer{name[-1]}.example"})
    row.update(fields)
    return row


DETAILS = {
    "CO-DEMO-0001": _detail("CO-DEMO-0001", "Surface", "New Product Onboarding", "Approved", "Request Approved",
                            Alternate_Domains__c="other1.example"),
    "CO-DEMO-0002": _detail("CO-DEMO-0002", "Credential Exposure", "New Product Onboarding", "Approved", "Request Approved",
                            Email_Domains__c="customer2.example"),
    "CO-DEMO-0003": _detail("CO-DEMO-0003", "Surface & Credential Exposure", "New Product Onboarding", "Approved",
                            "Request Approved", Email_Domains__c="customer3.example"),
    "CO-DEMO-0004": _detail("CO-DEMO-0004", "Surface", "New Product Onboarding", "Approved", "Account Scanning",
                            Surface_Account_ID__c=DEMO_OTHER_ID),
    "CO-DEMO-0005": _detail("CO-DEMO-0005", "Surface & Credential Exposure", "New Product Onboarding", "Pending", "New"),
    "CO-DEMO-0006": _detail("CO-DEMO-0006", "Credential Exposure", "New Product Onboarding", "Approved", "Request Approved",
                            Email_Domains__c="customer6.example"),
}
READBACKS = {ref: {"surface_account_id": DEMO_SURFACE_ID, "account_uuid": DEMO_UUID, "leonardo_state": state,
                   "observed_on": "2026-10-01", "source": "Leonardo Development Details readback"}
             for ref, state in (("CO-DEMO-0002", "No scan started"), ("CO-DEMO-0004", "Account Scanning"))}
RUNNER_STATE = {
    "CO-DEMO-0002": {"source_revision": REVISION, "result": "readback_verified", "completed_on": "2026-10-01T10:12:40",
                     "license_start_entered": "2026-09-30", "license_end_entered": "2027-09-29"},
    "CO-DEMO-0004": {"source_revision": REVISION, "result": "readback_verified", "completed_on": "2026-09-30T15:41:29",
                     "route": dashboard.SURFACE_ENGINE, "license_start_entered": "2026-09-29",
                     "license_end_entered": "2027-09-30"},
    "CO-DEMO-0006": {"source_revision": REVISION, "result": "duplicate_found", "completed_on": "2026-10-01T11:02:05"},
}


def _scope(**extra: object) -> dict[str, object]:
    scope: dict[str, object] = {
        "tier": "prime", "scanning_interval": "Weekly", "main_domains": 1, "alternate_root_domains": 1,
        "requested_subdomains": 0, "number_of_domains": 1000, "baseline_subdomains": 1000, "addon_subdomains": 0,
        "licensed_subdomains": 1000, "product_domains": None, "assets": 10000, "license_start": "2026-10-01",
        "license_end": "2027-09-30", "large_scope": False, "core_plus_present": False}
    scope.update(extra)
    return scope


TERM_DETAIL = ("The Surface licence would end 2027-09-30 but the Core Plus (Credential Exposure) licence would end "
               "2027-06-30. One tenant has one licence term, so this CO needs a manual review: correct the DealHub "
               "terms in Salesforce, or onboard it manually.")


def _surface_preflight(reference: str, route: str = dashboard.SURFACE_ENGINE) -> Any:
    if reference == "CO-DEMO-0003":
        return dashboard.SurfaceScopePreflight(reference, REVISION, ("case3_term_mismatch",), None, True,
                                               route=dashboard.CASE3_ENGINE, blocker_details=(TERM_DETAIL,))
    if reference in DETAILS:
        return dashboard.SurfaceScopePreflight(reference, REVISION, (), _scope(), False, route=route)
    raise dashboard.ReadUnavailable()


def _ce_preflight(reference: str) -> Any:
    if reference in DETAILS:
        return dashboard.CredentialExposureFillPreflight(reference, REVISION, 1, ())
    raise dashboard.ReadUnavailable()


def _scan_statuses() -> dict[str, dict[str, object]]:
    now = datetime.now()
    return {"CO-DEMO-0004": {"state": "scan_started", "status_enum": "COMPLETED",
                             "last_recon_scan": "2026-09-30T22:45:00+00:00", "duration_ms": 11_292_919,
                             "observed_at": now - timedelta(minutes=5), "expires_at": now + timedelta(hours=6)}}


def _validations() -> dict[str, dict[str, object]]:
    now = datetime.now()
    checks = [
        {"group": "Account", "check": "Account enabled", "status": "ok", "expected": True, "found": True},
        {"group": "Licence", "check": "Licence type", "status": "ok", "expected": "Prepaid annual subscription",
         "found": "PREPAID_ANNUAL_SUBSCRIPTION"},
        {"group": "Licence", "check": "Expiration date", "status": "ok", "expected": "2027-09-30", "found": "2027-09-30"},
        {"group": "Settings", "check": "Nuclei", "status": "drift", "expected": True, "found": False},
        {"group": "Settings", "check": "Maximum scan duration (h)", "status": "unknown", "expected": 90, "found": None},
        {"group": "Domains", "check": "Alternate domains", "status": "ok", "expected": "1 item(s)", "found": "1 item(s)"},
        {"group": "People", "check": "MFA required", "status": "ok", "expected": True, "found": True},
        {"group": "People", "check": "Operator Account", "status": "info", "found": "not assigned"},
        {"group": "Scan", "check": "Scan status", "status": "info", "found": "scan_completed"},
    ]
    return {"CO-DEMO-0004": {"checks": checks, "plan_note": "", "observed_at": now - timedelta(minutes=3),
                             "expires_at": now + timedelta(hours=6)}}


def _history() -> Any:
    today = date.today()
    completed, created = [], []
    for back in range(13):
        year, month = today.year, today.month - back
        while month < 1:
            year, month = year - 1, month + 12
        for product, count in (("Credential Exposure", 18 + back % 5), ("Surface & Credential Exposure", 12 + back % 4),
                               ("Surface", 3 + back % 3)):
            completed.append({"y": year, "m": month, "p": product, "n": count})
        created.append({"y": year, "m": month, "n": 30 + back % 7})
    history = dashboard.build_closed_history(completed, created, today)
    return dashboard.ClosedHistory(history.series, history.months, history.as_of,
                                   dashboard.ClosedKpis(38, 33, 11.5, 120, 42), today.strftime("%H:%M"))


def _blocked(*_args: object, **_kwargs: object) -> Any:
    raise dashboard.ReadUnavailable()


def _blocked_write(*_args: object, **_kwargs: object) -> Any:
    raise dashboard.WriteUnavailable()


def _blocked_runner(*_args: object, **_kwargs: object) -> Any:
    raise RuntimeError("preview_has_no_backends")


_RENDER_LOCK = Lock()


@contextmanager
def synthetic_backends() -> Iterator[None]:
    """Swap every dashboard/runner reader for synthetic or fail-closed values while rendering."""
    swaps: dict[Any, dict[str, Any]] = {
        dashboard: {
            "sf_json": _blocked, "sf_write_json": _blocked_write, "local_browser_launch_allowed": lambda: False,
            "attended_leonardo_readbacks": lambda: dict(READBACKS), "load_runner_state": lambda: dict(RUNNER_STATE),
            "attended_scan_statuses": _scan_statuses, "salesforce_id_writebacks": lambda: {},
            "attended_validations": _validations,
            "renewal_subscription_rows": _blocked,
            "load_attended_reminders": lambda: {}, "manual_start_ack_nonce": lambda *_a, **_k: None,
            "evaluate_surface_fill_preflight": _surface_preflight, "evaluate_ce_only_fill_preflight": _ce_preflight,
            "closed_history": _history, "cached_closed_history": _history, "detail_row": _blocked,
            "queue_rows": _blocked, "queue_source_rows": _blocked,
        },
        runner: {"_sf_records": _blocked_runner, "_attach_attended_browser": _blocked_runner},
    }
    with _RENDER_LOCK:
        saved = {module: {name: getattr(module, name) for name in names} for module, names in swaps.items()}
        try:
            for module, names in swaps.items():
                for name, value in names.items():
                    setattr(module, name, value)
            yield
        finally:
            for module, names in saved.items():
                for name, value in names.items():
                    setattr(module, name, value)


def _queue_rows() -> list[dict[str, str | None]]:
    rows = []
    for row in DETAILS.values():
        queue_row = {field: row.get(field) for field in dashboard.QUEUE_FIELDS if field in row}
        queue_row["Account__r.Name"] = row["Account_Name__c"]
        queue_row["Submission_Date__c"] = (date.today() - timedelta(days=int(row["Name"][-1]) * 3)).isoformat()
        if row["Name"] in READBACKS:
            queue_row["Local_Leonardo_State"] = READBACKS[row["Name"]]["leonardo_state"]
        rows.append(queue_row)
    return rows


def _finish(page: str) -> bytes:
    """Add the preview banner and disable every button (the preview takes no action)."""
    page = re.sub(r"<main( class='wide')?>", lambda match: match.group(0) + BANNER, page, count=1)
    page = page.replace("Read from Salesforce at", "Synthetic data at")
    page = re.sub(r"<button(?![^>]*\bdisabled\b)", "<button disabled title='Preview only'", page)
    return page.encode("utf-8")


def preview_response(path: str) -> tuple[int, bytes, str]:
    """Return a synthetic preview page, a minimal health response, or a 404."""
    parts = urlsplit(path)
    query = parse_qs(parts.query, keep_blank_values=True)
    html = "text/html; charset=utf-8"
    if parts.path == "/health" and not parts.query:
        return 200, b'{"status":"static_preview_only"}', "application/json"
    with synthetic_backends():
        if parts.path == "/":
            selected = ""
            if parts.query:
                if set(query) != {"queue"} or query["queue"][0] not in QUEUE_KEYS:
                    return 404, b"not found\n", "text/plain; charset=utf-8"
                selected = query["queue"][0]
            page = dashboard.page_queue(_queue_rows(), selected, runner_state=dict(RUNNER_STATE),
                                        history=_history(), read_at=datetime.now().strftime("%H:%M"))
            return 200, _finish(page), html
        if parts.query:
            return 404, b"not found\n", "text/plain; charset=utf-8"
        if parts.path == "/history":
            return 200, _finish(dashboard.page_history(_history())), html
        if parts.path == "/connection":
            body = ("<div class='page-head'><h1>Connection</h1></div><section class='card'><p>Connections are disabled "
                    "in this preview. The live dashboard runs on the operator desktop at 127.0.0.1:8012.</p></section>")
            return 200, _finish(dashboard._app_shell("Connection", body, active="connection")), html
        match = re.fullmatch(r"/co/(CO-DEMO-000[1-6])", parts.path)
        if match:
            reference = match.group(1)
            return 200, _finish(dashboard.page_detail(reference, dict(DETAILS[reference]))), html
    return 404, b"not found\n", "text/plain; charset=utf-8"


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "SurfacePreview"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - required stdlib handler name
        status, body, content_type = preview_response(self.path)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Do not retain request paths or SSH-tunnel metadata in terminal logs."""


def parse_port(arguments: list[str]) -> int:
    if len(arguments) > 1:
        raise ValueError("usage: serve_loopback_preview.py [PORT]")
    if not arguments:
        return DEFAULT_PORT
    try:
        port = int(arguments[0])
    except ValueError as exc:
        raise ValueError("preview_port_must_be_integer") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("preview_port_out_of_range")
    return port


def main(arguments: list[str]) -> int:
    try:
        port = parse_port(arguments)
    except ValueError as exc:
        print(str(exc))
        return 2
    server = ThreadingHTTPServer((LOOPBACK_HOST, port), PreviewHandler)
    print(f"static_preview_listening_on_loopback_port_{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
