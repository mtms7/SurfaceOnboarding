"""Start the first Surface scan of a new Case 3 tenant ("Scan now"), owner request 2026-11-05 (CO-0841).

Live finding 2026-11-05: the Case 3 Add Account form has no "Scan now" control once a Scanning interval is chosen (record
``scan_now_control: absent``), and Edit Account saves ``scanNow: false``; 25 min after the CO-0841 create the tenant row still
had ``lastScanStatusEnum`` and ``lastReconScan`` null. The owner then used the tenant's row menu "Scan now", which sent ONE
``POST /api/v1/backoffice/account/{id}/campaign/executeReconCampaign`` with body ``{}``; the next row read showed
``lastScanStatusEnum: RUNNING`` and a ``lastReconScan`` time after the create.

The runner (``_pcr_case3_scan_now``) does the same once, right after the post-create Edit Account (so the scan uses Maximum
scan Duration 90): it reads the row, clicks the row menu item "Scan now" only when no scan has started, lets exactly one
request for that tenant id through, and re-reads the row. This module holds the pure rules and texts; it reads and writes
nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

STEP = "scan_now"  # the step name of the result lines in ``case3_followup.WRITES_FILE``
MENU_LABEL = "Scan now"
PATH = re.compile(r"/api/v1/backoffice/account/([A-Za-z0-9_-]{1,64})/campaign/executeReconCampaign")
STARTED_STATUSES = frozenset({"RUNNING", "COMPLETED", "INCOMPLETE"})
# Row-menu items whose data-am token names something destructive are never clicked, whatever their text says.
HAZARD_TOKEN = re.compile(r"delete|remove|disable|deactivate|suspend|reset|purge", re.IGNORECASE)
MENU_TOKEN = re.compile(r"Button-Grid_[A-Za-z0-9_]{1,40}")

VERIFIED = "scan_now_verified"
ALREADY = "scan_already_started"
# After the click the request may have gone out: never clicked again by the automation.
WRITE_OUTCOMES = frozenset({VERIFIED, "scan_now_unverified", "scan_now_no_signal", "scan_now_rejected", "scan_now_id_mismatch"})
OK_RESULTS = frozenset({VERIFIED, ALREADY})

TEXT = {
    VERIFIED: "Scan started by the run (row menu \"Scan now\") and read back as running.",
    ALREADY: "A scan was already running when the run checked; nothing was clicked.",
}
ACTION_TEXT = ("No scan was confirmed by the run. In BackOffice open the tenant's row menu and click \"Scan now\" "
               "(once), then check the row shows the scan running.")


def started(row: Mapping[str, Any] | None) -> bool:
    """True when the tenant row shows a Surface scan that belongs to this tenant's life: a known status, or a
    ``lastReconScan`` time after the row's own ``_created`` (never a value from before the create)."""
    if not isinstance(row, Mapping):
        return False
    if row.get("lastScanStatusEnum") in STARTED_STATUSES:
        return True
    last, created = row.get("lastReconScan"), row.get("_created")
    return type(last) is int and type(created) is int and last > created


def request_id(method: object, url_path: object) -> str | None:
    """The tenant id of a POST to the exact "Scan now" path, else None (the caller checks the origin)."""
    if str(method).upper() != "POST" or not isinstance(url_path, str):
        return None
    found = PATH.fullmatch(url_path)
    return found.group(1) if found else None


def menu_token_allowed(token: Any) -> bool:
    """A row-menu item may be clicked only when its data-am token is a plain grid token and names nothing destructive."""
    return isinstance(token, str) and MENU_TOKEN.fullmatch(token) is not None and HAZARD_TOKEN.search(token) is None


def response_problem(method: str, account_in_url: str | None, status: int, account_id: str) -> str | None:
    if account_in_url != account_id:
        return "scan_now_id_mismatch"
    if method.upper() != "POST" or not 200 <= int(status) < 300:
        return "scan_now_rejected"
    return None


def action_needed(result: str | None, edit_result: str | None) -> str | None:
    """The dashboard's "Action needed" text for a Case 3 create from 2026-11-02 on (``edit_result`` set), else None.
    Old creates (no Edit Account line) show nothing new."""
    if edit_result is None:
        return None
    if result in OK_RESULTS:
        return None
    return ACTION_TEXT


def note(result: str | None) -> str | None:
    """What the CO page says about the scan for a recorded result (None when there is no line)."""
    if result is None:
        return None
    if result in TEXT:
        return TEXT[result]
    return ACTION_TEXT + " (run result: " + result + ")"
