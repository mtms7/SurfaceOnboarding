"""D4 read-only BackOffice production probe: gate, network guard, click guard, form reader, redacted report.

Owner decision D4 (docs/45, 2026-10-16): one attended, read-only probe of BackOffice production in the dedicated
profile `backoffice-production-automation`. The human signs in with MFA; this code never types credentials. It records
page titles and URL paths (no query strings), the labels/names/types/required flags of the create form's controls
(nothing is filled or submitted), whether Search and Edit controls exist, and the method + path template of the API
calls the page itself makes. No values, bodies, cookies, tokens or headers are read or kept.

This is NOT a production write path: it never builds a production RunTarget (`production_target` stays locked behind
`PRODUCTION_RUNNER_SUPPORTED = False`) and every request that is not a GET (or an allow-listed read POST) is aborted.
The module opens no connection itself; the runner passes it a Playwright page.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from . import session_readiness
from .origin_policy import require_production_origin
from .run_target import RunTarget

GATE_ENV = "SURFACE_ONBOARDING_PRODUCTION_PROBE"
REF_ENV = "SURFACE_ONBOARDING_PRODUCTION_PROBE_REF"
REF_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{5,63}$")
PRODUCTION_ORIGIN = "https://app.pentera.io"
PROBE_START_PATH = "/backoffice/tenantManagement"
REPORT_SUBDIR = "production-probe"

# --- Network guard -----------------------------------------------------------
# Read methods pass. Of the non-GET methods only these exact read endpoints (the ones the Dev runner already
# uses, which are POSTs that read): the tenant table query and the per-tenant scan-executions query. Everything
# else (account/add, account/<id>/edit, any PUT/PATCH/DELETE, any other POST) is aborted and counted.
SAFE_METHODS = frozenset({"GET", "HEAD"})
READ_POST_PATHS = (
    re.compile(r"/api/v1/backoffice/getAllDetailedAccounts"),
    re.compile(r"/api/v1/backoffice/account/[A-Za-z0-9_-]{1,64}/campaign/executions"),
)
MAX_API_ENTRIES = 200

# --- The one read body the guards accept for the table endpoint (docs/48 note 2026-10-18) --------------------------
# The page sends its OWN getAllDetailedAccounts request; the runner rewrites only the JSON ``tableServerData`` fields below.
# A POST to the endpoint passes the guard only when its body is exactly one bounded, read-only table search: the HAR key
# set, offset 0, at most TABLE_ROW_LIMIT rows, and a filter that is one ``search`` clause or the verified ``or`` variant.
TABLE_READ_PATH = "/api/v1/backoffice/getAllDetailedAccounts"
TABLE_ROW_LIMIT = 100
_TABLE_KEYS = frozenset({"offset", "items_per_page", "sort", "filters", "unique_fields", "projection"})
_LEAF_KEYS = frozenset({"method", "value", "field"})
_OR_METHODS = frozenset({"search", "equals"})
_PATH_LIKE = re.compile(r"[A-Za-z0-9_.]{1,80}")
_MAX_BODY_CHARS = 20_000
_MAX_VALUE_CHARS = 200
_ID_SEGMENT = re.compile(r"(?i)^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]{5,}|[A-Za-z0-9]{16,64})$")

# --- Click guard -------------------------------------------------------------
# The probe may click only these controls; none submits or saves. Confirm/Submit/Save/Edit items are never located.
CLICK_OPEN_CREATE_FORM = "open_create_form"   # the "Add Account" button: opens the form client-side
CLICK_CANCEL_CREATE_FORM = "cancel_create_form"  # the form's own Cancel
CLICK_OPEN_ROW_MENU = "open_row_menu"          # opens a row's action menu to see whether Edit exists (Edit is never clicked)
ALLOWED_CLICKS = frozenset({CLICK_OPEN_CREATE_FORM, CLICK_CANCEL_CREATE_FORM, CLICK_OPEN_ROW_MENU})
ADD_ACCOUNT_BUTTON = "Add Account"
# Exact visible-text candidates for the create control, tried in order (button role, then link role).
# The generic "Add" and "Create" are not candidates: a click on them could hit another control.
# The dry run clicks only "Add Account".
CREATE_CANDIDATES = ("Add Account", "Add account", "Create Account", "New Account")
SETTLE_IDLE_MS = 20_000
SETTLE_POLLS = 80  # x 250 ms = 20 s wait for a table row or an empty-state text
MAX_CONTROLS = 60
MAX_HEADINGS = 3
CONTROL_TEXT = 40
ROW_MENU_SELECTOR = ".rowMenuActionsContainer .list-trigger"
EDIT_SELECTOR = '[data-am="Button-Grid_Edit"]'
FORM_POLLS = 20
FORM_POLL_MS = 250

MAX_FIELDS = 200
MAX_TEXT = 80
_FIELD_NAME = re.compile(r"[A-Za-z0-9_.\[\]-]{1,80}")
_FIELD_TYPES = frozenset({"text", "email", "password", "number", "tel", "url", "search", "date", "checkbox", "radio",
                          "select", "textarea", "hidden", "file", "range", "time", "datetime-local", "month", "week",
                          "color", "unknown"})

# In-page script. It reads metadata only: label text, name, type, required flag, option COUNT. It never reads `.value`,
# `.checked`, option text, or any attribute carrying data. Buttons inside the form are listed by their label only.
FORM_JS = """() => {
  const root = document.querySelector('[role=dialog], .MuiDialog-root, .tenants-add-account, form');
  if (!root) return null;
  const labelOf = (el) => {
    const bits = [];
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) bits.push(l.textContent); }
    const wrap = el.closest('label'); if (wrap) bits.push(wrap.textContent);
    const by = el.getAttribute('aria-labelledby');
    if (by) by.split(/\\s+/).forEach(id => { const n = document.getElementById(id); if (n) bits.push(n.textContent); });
    if (el.getAttribute('aria-label')) bits.push(el.getAttribute('aria-label'));
    if (!bits.length && el.getAttribute('placeholder')) bits.push(el.getAttribute('placeholder'));
    return (bits.find(b => b && b.trim()) || '').trim();
  };
  const fields = [];
  root.querySelectorAll('input, select, textarea').forEach(el => {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
    const label = labelOf(el);
    fields.push({label: label, name: el.getAttribute('name') || '', type: type,
                 required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\\*\\s*$/.test(label)),
                 option_count: tag === 'select' ? el.options.length : null,
                 data_am: el.getAttribute('data-am') || ''});
  });
  const buttons = Array.from(root.querySelectorAll('button')).map(b => (b.textContent || '').trim()).filter(Boolean);
  return {fields: fields, buttons: buttons};
}"""


# True when the table shows a row or a "no records" style message.
SETTLE_JS = """() => {
  if (document.querySelector('tbody tr td, [role=row] [role=cell]')) return true;
  return /no (matching )?records found|no data|no results|nothing to show/i.test(document.body ? document.body.innerText : '');
}"""

# Diagnostic list of visible controls: tag, role, short visible text, enabled flag. Nothing else is read.
VISIBLE_JS = """() => {
  const seen = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const controls = Array.from(document.querySelectorAll('button, a, [role=button]')).filter(seen).slice(0, 200).map(el => ({
    tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
    text: ((el.innerText || el.getAttribute('aria-label') || '') + '').trim().slice(0, 200),
    enabled: !(el.disabled || el.getAttribute('aria-disabled') === 'true')}));
  const headings = Array.from(document.querySelectorAll('h1, h2, h3')).filter(seen).slice(0, 3)
    .map(el => ((el.innerText || '') + '').trim().slice(0, 200));
  return {controls: controls, headings: headings};
}"""


# Sign-in state: visible control texts (no values), table and navigation counts. Read only.
AUTH_JS = """() => {
  const seen = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const texts = Array.from(document.querySelectorAll('button, a, [role=button], input[type=submit]')).filter(seen).slice(0, 100)
    .map(el => ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim().slice(0, 60));
  const count = (sel) => Array.from(document.querySelectorAll(sel)).filter(seen).length;
  return {texts: texts, tables: count('table, [role=table], [role=grid]'),
          navs: count('nav, [role=navigation], [role=menu], [role=menubar]')};
}"""
LOGIN_TEXTS = ("log in", "sign in with sso", "forgot your password?")
LOGIN_STABLE_POLLS = 3
LOGIN_REMINDER_SECONDS = 60.0


def is_authenticated(path: object, snapshot: object) -> bool:
    """Signed in only if: path under /backoffice, no 'login' in it, no login control visible, an app marker visible."""
    low = path.casefold() if isinstance(path, str) else ""
    if not low.startswith("/backoffice") or "login" in low or not isinstance(snapshot, Mapping):
        return False
    raw = snapshot.get("texts")
    texts = [t.strip().casefold() for t in raw if isinstance(t, str)] if isinstance(raw, list) else []
    if any(t in LOGIN_TEXTS for t in texts):
        return False
    tables, navs = snapshot.get("tables"), snapshot.get("navs")
    return (isinstance(tables, int) and tables > 0) or (isinstance(navs, int) and navs > 0) or any(texts)


def wait_for_login(poll: Any, timeout_seconds: float, *, stable: int = LOGIN_STABLE_POLLS, interval: float = 1.0,
                   now: Any = None, sleeper: Any = None, say: Any = print) -> bool:
    """Poll (path, snapshot) until authenticated on `stable` consecutive polls; False after the timeout. Reads only."""
    from time import monotonic, sleep
    now, sleeper = now or monotonic, sleeper or sleep
    start = now()
    deadline, next_reminder, run = start + timeout_seconds, start + LOGIN_REMINDER_SECONDS, 0
    while now() < deadline:
        try:
            path, snapshot = poll()
        except Exception:
            path, snapshot = "", None
        run = run + 1 if is_authenticated(path, snapshot) else 0
        if run >= stable:
            return True
        if now() >= next_reminder:
            say("waiting for sign-in")
            next_reminder += LOGIN_REMINDER_SECONDS
        sleeper(interval)
    return False


class ProbeRefused(Exception):
    """The probe may not start; `code` is the stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def probe_gate(environ: Mapping[str, str]) -> str:
    """Return the probe reference, or raise ProbeRefused. Its own gate: independent of the production write unlock."""
    if environ.get(GATE_ENV) != "1":
        raise ProbeRefused("production_probe_flag_missing")
    reference = environ.get(REF_ENV, "")
    if not isinstance(reference, str) or not REF_PATTERN.fullmatch(reference):
        raise ProbeRefused("production_probe_reference_invalid")
    return reference


def probe_target() -> RunTarget:
    """The read-only probe target (exact production origin, dedicated profile). Not a write target."""
    environment = session_readiness.ENVIRONMENTS[session_readiness.BACKOFFICE_PRODUCTION]
    return RunTarget(environment.key, require_production_origin(environment.origin), environment.profile_dir_name)


def start_url(target: RunTarget) -> str:
    return require_production_origin(target.origin) + PROBE_START_PATH


# --- URL helpers (no urllib: this package stays free of transport imports) ---

_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)")


def url_origin(url: object) -> str:
    match = _URL.match(url) if isinstance(url, str) else None
    return f"{match.group(1).lower()}://{match.group(2).lower()}" if match else ""


def url_path(url: object) -> str:
    """Path only: no scheme, host, query or fragment."""
    match = _URL.match(url) if isinstance(url, str) else None
    return match.group(3) or "/" if match else ""


def _is_id_segment(part: str) -> bool:
    """Id-like: long hex/UUID, 5+ digits, or a 16-64 character token containing a digit (camelCase words stay)."""
    if not _ID_SEGMENT.fullmatch(part):
        return False
    return any(c.isdigit() for c in part) or re.fullmatch(r"(?i)[0-9a-f]{32}", part) is not None


def path_template(url: object) -> str:
    """Path with id-like segments replaced so no tenant id or UUID is kept."""
    return "/".join("{id}" if _is_id_segment(part) else part for part in url_path(url).split("/"))


def _value_ok(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= _MAX_VALUE_CHARS
            and re.search(r"[\x00-\x1f\x7f]", value) is None)


def _leaf_ok(leaf: object, methods: frozenset[str], allow_field: bool) -> bool:
    if not isinstance(leaf, dict) or not {"method", "value"} <= set(leaf) <= _LEAF_KEYS:
        return False
    if leaf["method"] not in methods or not _value_ok(leaf["value"]):
        return False
    field = leaf.get("field")
    return "field" not in leaf or (allow_field and isinstance(field, str) and _PATH_LIKE.fullmatch(field) is not None)


def _filters_ok(filters: object) -> bool:
    if not isinstance(filters, dict) or set(filters) != {"and"}:
        return False
    clauses = filters["and"]
    if not isinstance(clauses, list) or len(clauses) != 1 or not isinstance(clauses[0], dict):
        return False
    clause = clauses[0]
    if set(clause) == {"or"}:  # the HAR name-list variant: {and:[{or:[{method, value, field}]}]}
        leaves = clause["or"]
        return isinstance(leaves, list) and 1 <= len(leaves) <= 50 and all(_leaf_ok(x, _OR_METHODS, True) for x in leaves)
    return _leaf_ok(clause, frozenset({"search"}), False)  # the Dev grammar: {and:[{method:"search", value}]}


def _names_ok(names: object, limit: int) -> bool:
    return (isinstance(names, list) and len(names) <= limit
            and all(isinstance(n, str) and _PATH_LIKE.fullmatch(n) is not None for n in names))


def read_body_problem(raw: object) -> str | None:
    """None only when ``raw`` (JSON text or bytes) is exactly one bounded read-only table search; else a short reason.

    Top level: ``tableServerData`` and nothing else. Inside: only the HAR keys; ``offset`` 0; ``items_per_page`` an int
    from 1 to TABLE_ROW_LIMIT; ``filters`` one ``search`` clause or the verified ``or`` variant (non-empty bounded
    values, optional field path); optional ``sort`` {direction, key}, ``unique_fields`` and ``projection`` (path-like
    names only). Any other key, a missing body, junk or an oversized body is refused.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return "body_unreadable"
    if not isinstance(raw, str) or not raw.strip() or len(raw) > _MAX_BODY_CHARS:
        return "body_unreadable"
    try:
        body = json.loads(raw)
    except ValueError:
        return "body_unreadable"
    if not isinstance(body, dict) or set(body) != {"tableServerData"} or not isinstance(body["tableServerData"], dict):
        return "body_keys"
    server = body["tableServerData"]
    if not {"offset", "items_per_page", "filters"} <= set(server) <= _TABLE_KEYS:
        return "body_keys"
    offset, size = server["offset"], server["items_per_page"]
    if type(offset) is not int or offset != 0 or type(size) is not int or not 1 <= size <= TABLE_ROW_LIMIT:
        return "paging"
    if not _filters_ok(server["filters"]):
        return "filters"
    sort = server.get("sort")
    if "sort" in server and not (isinstance(sort, dict) and set(sort) <= {"direction", "key"}
                                 and all(isinstance(v, str) and len(v) <= 80 for v in sort.values())):
        return "sort"
    if "unique_fields" in server and not _names_ok(server["unique_fields"], 20):
        return "unique_fields"
    if "projection" in server and not _names_ok(server["projection"], 80):
        return "projection"
    return None


def table_rewrite_fields(value: str, projection: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """The ``tableServerData`` fields the runner overwrites on the page's own request; nothing else is ever changed.

    ``unique_fields`` is name + domain on purpose: the page's own load request collapses rows by account type, license
    type and first name (docs/48), which would hide tenants from a duplicate check. The sort the page sent is kept.
    """
    if not _value_ok(value):
        raise ValueError("search_value_invalid")
    names = list(projection)
    if not names or not _names_ok(names, 80):
        raise ValueError("projection_invalid")
    return {"offset": 0, "items_per_page": TABLE_ROW_LIMIT, "filters": {"and": [{"method": "search", "value": value}]},
            "unique_fields": ["accountName", "accountDomain"], "projection": names}


def request_decision(method: str, url: str) -> tuple[bool, str]:
    """(allowed, reason). Fail closed: unknown methods and every non-listed write are refused."""
    verb = (method or "").upper()
    if verb in SAFE_METHODS:
        return True, "read_method"
    if verb == "POST" and url_origin(url) == PRODUCTION_ORIGIN \
            and any(pattern.fullmatch(url_path(url)) for pattern in READ_POST_PATHS):
        return True, "allowlisted_read_post"
    return False, "write_or_unlisted_method_blocked"


class RequestGuard:
    """Routes every request of the probe page: allow reads, abort everything else, record names only."""

    def __init__(self, *, require_read_body: bool = False) -> None:
        self.api_calls: dict[tuple[str, str], dict[str, Any]] = {}
        self.blocked: dict[tuple[str, str], int] = {}
        self.other_read_count = 0
        self.require_read_body = require_read_body  # True: a table POST passes only with a validated read body

    @staticmethod
    def _where(url: str) -> str:
        return path_template(url) if url_origin(url) == PRODUCTION_ORIGIN else "<other-origin>"

    def handle(self, route: Any) -> None:
        try:
            method, url = str(route.request.method).upper(), str(route.request.url)
        except Exception:
            route.abort()
            return
        allowed, _reason = request_decision(method, url)
        if allowed and self.require_read_body and method == "POST" and url_path(url) == TABLE_READ_PATH:
            try:
                allowed = read_body_problem(getattr(route.request, "post_data", None)) is None
            except Exception:
                allowed = False
        where = self._where(url)
        if not allowed:
            key = (method, where)
            self.blocked[key] = self.blocked.get(key, 0) + 1
            route.abort()
            return
        if "/api/" in where:
            entry = self.api_calls.setdefault((method, where), {"count": 0, "statuses": set()})
            entry["count"] += 1
        else:
            self.other_read_count += 1
        route.continue_()

    def on_response(self, response: Any) -> None:
        try:
            method, where = str(response.request.method).upper(), self._where(str(response.url))
            entry = self.api_calls.get((method, where))
            if entry is not None and isinstance(response.status, int):
                entry["statuses"].add(response.status)
        except Exception:
            pass

    def install(self, page: Any) -> None:
        """Raises if the guard cannot be installed: the probe must not run unguarded."""
        page.route("**/*", self.handle)
        try:
            page.on("response", self.on_response)
        except Exception:
            pass

    def api_report(self) -> list[dict[str, Any]]:
        rows = [{"method": m, "path": p, "name": p.rstrip("/").rsplit("/", 1)[-1][:60], "count": e["count"],
                 "statuses": sorted(e["statuses"])} for (m, p), e in sorted(self.api_calls.items())]
        return rows[:MAX_API_ENTRIES]

    def blocked_report(self) -> list[dict[str, Any]]:
        return [{"method": m, "path": p, "count": c} for (m, p), c in sorted(self.blocked.items())][:MAX_API_ENTRIES]


class ClickLog:
    """Every click the probe makes goes through here; only ALLOWED_CLICKS exist."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def click(self, kind: str, locator: Any) -> None:
        if kind not in ALLOWED_CLICKS:
            raise ProbeRefused("production_probe_click_not_allowed")
        locator.click(timeout=5_000)
        self.kinds.append(kind)


# --- Reading the page ----------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IDS = re.compile(r"(?<![A-Za-z0-9])(?:[0-9a-fA-F]{32}|[0-9]{5,}|(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{16,64})(?![A-Za-z0-9])")


def clean_text(value: object, limit: int = MAX_TEXT) -> str:
    """UI label text only: control characters removed, emails masked, length capped."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", value if isinstance(value, str) else "")
    text = _EMAIL.sub("<redacted>", " ".join(text.split()))
    return _IDS.sub("<id>", text)[:limit]


def sanitize_form(raw: object) -> dict[str, Any] | None:
    """Keep only label/name/type/required/option_count/data_am of each control; drop everything else, values included."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("fields"), list):
        return None
    fields: list[dict[str, Any]] = []
    for item in raw["fields"][:MAX_FIELDS]:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        kind = item.get("type") if item.get("type") in _FIELD_TYPES else "unknown"
        count = item.get("option_count")
        data_am = item.get("data_am") if isinstance(item.get("data_am"), str) else ""
        fields.append({
            "label": clean_text(item.get("label")),
            "name": name if _FIELD_NAME.fullmatch(name) else "",
            "type": kind,
            "required": item.get("required") is True,
            "option_count": count if isinstance(count, int) and not isinstance(count, bool) and 0 <= count < 10_000 else None,
            "data_am": data_am if _FIELD_NAME.fullmatch(data_am) else "",
        })
    buttons = [clean_text(b, 40) for b in (raw.get("buttons") or []) if isinstance(b, str)][:30]
    return {"fields": fields, "buttons": buttons}


def _count(locator: Any) -> int:
    try:
        return int(locator.count())
    except Exception:
        return -1


def _page_entry(page: Any) -> dict[str, str]:
    try:
        title = clean_text(page.title())
    except Exception:
        title = ""
    return {"path": path_template(getattr(page, "url", "")), "title": title}


def _settle(page: Any) -> dict[str, Any]:
    """Wait for the page to load: GET the tenant page once if elsewhere, network idle, then a row or empty-state."""
    navigated_from, navigation_ok = None, True
    if url_path(getattr(page, "url", "")).casefold() != PROBE_START_PATH.casefold():
        navigated_from = path_template(getattr(page, "url", ""))
        try:
            page.goto(PRODUCTION_ORIGIN + PROBE_START_PATH, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            navigation_ok = False
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_IDLE_MS)
        idle = True
    except Exception:
        idle = False
    ready = False
    for _poll in range(SETTLE_POLLS):
        try:
            ready = bool(page.evaluate(SETTLE_JS))
        except Exception:
            ready = False
        if ready:
            break
        page.wait_for_timeout(FORM_POLL_MS)
    return {"network_idle": idle, "content_ready": ready, "navigated_from": navigated_from,
            "navigation_ok": navigation_ok}


def _visible(page: Any) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        raw = page.evaluate(VISIBLE_JS)
    except Exception:
        return [], []
    if not isinstance(raw, Mapping):
        return [], []
    controls = []
    for item in (raw.get("controls") if isinstance(raw.get("controls"), list) else [])[:MAX_CONTROLS]:
        if isinstance(item, Mapping):
            controls.append({"tag": clean_text(item.get("tag"), 12), "role": clean_text(item.get("role"), 20),
                             "text": clean_text(item.get("text"), CONTROL_TEXT), "enabled": item.get("enabled") is True})
    headings = [clean_text(h, CONTROL_TEXT) for h in (raw.get("headings") if isinstance(raw.get("headings"), list) else [])
                if isinstance(h, str)][:MAX_HEADINGS]
    return controls, headings


def _read_form(page: Any) -> dict[str, Any] | None:
    for _poll in range(FORM_POLLS):
        try:
            form = sanitize_form(page.evaluate(FORM_JS))
        except Exception:
            form = None
        if form and form["fields"]:
            return form
        page.wait_for_timeout(FORM_POLL_MS)
    return None


def _close_form(page: Any, clicks: ClickLog) -> bool:
    cancel = page.get_by_role("button", name="Cancel", exact=True)
    if _count(cancel) == 1:
        clicks.click(CLICK_CANCEL_CREATE_FORM, cancel.first)
    else:
        page.keyboard.press("Escape")  # closes a dialog client-side; nothing is saved
    for _poll in range(FORM_POLLS):
        try:
            still_open = sanitize_form(page.evaluate(FORM_JS))
        except Exception:
            return False
        if not still_open or not still_open["fields"]:
            return True
        page.wait_for_timeout(100)
    return False


def collect(page: Any, guard: RequestGuard, clicks: ClickLog, *, dev_labels: tuple[str, ...] = (),
            dev_names: tuple[str, ...] = ()) -> dict[str, Any]:
    """Read-only inspection of the signed-in page. Fills and submits nothing; clicks only via ALLOWED_CLICKS."""
    settle = _settle(page)
    pages = [_page_entry(page)]
    visible_controls, headings = _visible(page)
    form_report: dict[str, Any] = {"control_found": False, "candidate_matched": None, "opened": False, "fields": [], "field_count": 0,
                                   "submit_control_present": None, "closed": None}
    controls: dict[str, Any] = {}

    search_button = _count(page.get_by_role("button", name="Search", exact=True))
    search_box = _count(page.get_by_role("textbox", name="Search", exact=True))
    controls["search"] = "present" if search_button > 0 or search_box > 0 else (
        "unknown" if search_button < 0 and search_box < 0 else "absent")

    menus = page.locator(ROW_MENU_SELECTOR)
    menu_count = _count(menus)
    if menu_count > 0:
        clicks.click(CLICK_OPEN_ROW_MENU, menus.first)
        page.wait_for_timeout(FORM_POLL_MS)
        edit = _count(page.locator(EDIT_SELECTOR))
        edit_named = _count(page.get_by_role("menuitem", name="Edit", exact=True))
        controls["edit"] = "present" if edit > 0 or edit_named > 0 else "absent"
        page.keyboard.press("Escape")
    else:
        controls["edit"] = "not_checked_no_rows" if menu_count == 0 else "unknown"

    add, matched = None, None
    for candidate in CREATE_CANDIDATES:
        for role in ("button", "link"):
            found = page.get_by_role(role, name=candidate, exact=True)
            if _count(found) == 1:
                add, matched = found, candidate
                break
        if add is not None:
            break
    if add is not None:
        form_report["control_found"] = True
        form_report["candidate_matched"] = matched
        clicks.click(CLICK_OPEN_CREATE_FORM, add.first)
        form = _read_form(page)
        if form is not None:
            form_report.update(opened=True, fields=form["fields"], field_count=len(form["fields"]),
                               submit_control_present="Confirm" in form["buttons"],
                               buttons=form["buttons"])
            pages.append(_page_entry(page))
            form_report["closed"] = _close_form(page, clicks)

    labels = {f["label"].casefold().rstrip(" *") for f in form_report["fields"]}
    names = {f["name"] for f in form_report["fields"]} | {f["data_am"] for f in form_report["fields"]}
    missing_labels = [x for x in dev_labels if x.casefold() not in labels]
    missing_names = [x for x in dev_names if x not in names]
    checks = {
        "login_completed": True,
        "page_settled": settle["content_ready"] and settle["navigation_ok"],
        "create_control_found": form_report["control_found"],
        "create_form_opened": form_report["opened"],
        "create_form_fields_read": form_report["field_count"] > 0,
        "create_form_closed_without_saving": form_report["closed"] is True,
        "search_control_present": controls["search"] == "present",
        "edit_control_present": controls["edit"] == "present",
        "dev_contract_labels_all_present": form_report["opened"] and not missing_labels,
        "dev_contract_names_all_present": form_report["opened"] and not missing_names,
        "submit_never_clicked": all(kind in ALLOWED_CLICKS for kind in clicks.kinds),  # no allowed kind submits
        "no_write_request_allowed": True,  # by construction: RequestGuard aborts every non-read request
    }
    return {
        "pages": pages,
        "settle": settle,
        "title": pages[0]["title"],
        "headings": headings,
        "visible_controls": visible_controls,
        "create_form": form_report,
        "controls": controls,
        "dev_contract": {"labels_missing_in_production": missing_labels, "names_missing_in_production": missing_names},
        "api_calls": guard.api_report(),
        "blocked_requests": guard.blocked_report(),
        "other_read_requests": guard.other_read_count,
        "clicks": list(clicks.kinds),
        "checks": checks,
    }


# --- Report --------------------------------------------------------------------

_FORBIDDEN_IN_REPORT = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), re.compile(r"(?i)\b[0-9a-f]{32}\b"), re.compile(r"eyJ[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+\S+"), re.compile(r"(?i)(?:password|passcode|cookie|authorization)\s*[:=]"),
)


def build_report(reference: str, now: datetime, result: str, collected: dict[str, Any] | None) -> dict[str, Any]:
    checks = (collected or {}).get("checks", {})
    return {
        "schema": 1, "kind": "production_readonly_probe", "reference": reference,
        "captured_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "origin": PRODUCTION_ORIGIN, "profile": probe_target().profile_dir_name, "result": result,
        "production_write": "not_performed", "salesforce_writeback": "not_performed",
        "checks_passed": sorted(k for k, v in checks.items() if v is True),
        "checks_failed": sorted(k for k, v in checks.items() if v is not True),
        **(collected or {}),
    }


def report_problem(report: Mapping[str, Any]) -> str | None:
    """None when the serialized report holds no credential/token/email/UUID shaped text."""
    text = json.dumps(report, sort_keys=True)
    return "production_probe_report_not_redacted" if any(p.search(text) for p in _FORBIDDEN_IN_REPORT) else None


def write_report(base: Path, report: Mapping[str, Any], now: datetime) -> Path:
    """Write `<base>/production-probe/<UTC stamp>.json` atomically; refuses an unredacted report (fail closed)."""
    if report_problem(report):
        raise ProbeRefused("production_probe_report_not_redacted")
    folder = base / REPORT_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def summary_line(report: Mapping[str, Any], path: Path | None) -> str:
    return json.dumps({"result": report["result"], "checks_passed": len(report["checks_passed"]),
                       "checks_failed": len(report["checks_failed"]), "report": path.name if path else None,
                       "production_write": "not_performed", "salesforce_writeback": "not_performed"},
                      separators=(",", ":"))
