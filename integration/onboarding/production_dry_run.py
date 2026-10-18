"""Pure logic of the production CE-only DRY RUN (docs/42 section 6.2, docs/45 D1-D6, docs/48).

A dry run opens the real production Add Account form, types the intended values, READS the advanced controls, verifies
every control by reading it back, then Cancels. It never clicks Confirm/Submit/Save and creates nothing. This module
holds only the decisions: value mapping, schema expectations, the session / duplicate / demo-domain verdicts, the
advanced-control report, the stricter request guard and the click allowlist. It opens no connection and reads no file.

Reads (docs/48 note 2026-10-18): the cookie-only in-page ``fetch`` was refuted live (401 after a human SSO+MFA sign-in),
so no script is evaluated in the page any more. The runner lets the PAGE send its own table request, rewrites only the
``tableServerData`` JSON body and reads the page's own reply in Python; the reducers below keep only status codes and a
few non-secret row fields (name, domain lists, deleted flag). Nothing here reads, returns or logs a token, cookie,
header or storage entry, and nothing keeps a typed value in a record: results are field names and pass/fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Mapping

from . import production_probe

# --- The page's own API traffic (read only) -----------------------------------------------------------------------
AUTHORITIES_PATH = "/api/v1/authenticated/getAuthorities"
ACCOUNT_INFO_PATH = "/api/v1/authenticated/accountInfo"
ACCOUNTS_PATH = production_probe.TABLE_READ_PATH
DEMO_DOMAINS_PATH = "/api/v1/backoffice/systemSettings/demoAccountDomains"
# Passive signals: the status of the page's own start-up calls, observed when it makes them (never requested by the runner).
PASSIVE_SESSION_PATHS = frozenset({AUTHORITIES_PATH, ACCOUNT_INFO_PATH})
SEARCH_PAGE_SIZE = production_probe.TABLE_ROW_LIMIT

_NO_REPLY = {"status": 0, "total": None, "rows": None}


def _strings(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str)][:50] if isinstance(value, list) else []


def search_reply(status: object, body: object) -> dict[str, Any]:
    """Reduce the page's OWN table reply (HTTP status, parsed JSON body) to {status, total, rows}.

    ``rows`` keep only name, domain, alternate and email domain lists and the deleted flag. A missing status is status 0;
    a non-200 status carries no rows; a 200 with an unexpected schema (any row that is not an object, a missing or
    non-integer total) has ``total``/``rows`` None, which every verdict treats as unavailable.
    """
    if type(status) is not int:
        return dict(_NO_REPLY)
    if status != 200:
        return {"status": status, "total": None, "rows": None}
    paged = body.get("pagination_response") if isinstance(body, dict) else None
    rows = paged.get("table_data") if isinstance(paged, dict) else None
    total = paged.get("total_count") if isinstance(paged, dict) else None
    if not isinstance(rows, list) or type(total) is not int or not all(isinstance(x, dict) for x in rows):
        return {"status": 200, "total": None, "rows": None}
    return {"status": 200, "total": total, "rows": [
        {"name": x["accountName"] if isinstance(x.get("accountName"), str) else None,
         "domain": x["accountDomain"] if isinstance(x.get("accountDomain"), str) else None,
         "alternates": _strings(x.get("alternateDomains")), "emails": _strings(x.get("userEmailDomains")),
         "deleted": x.get("isDeleted") is True} for x in rows[:200]]}


def demo_reply(status: object, body: object, domains: Any) -> dict[str, Any]:
    """Compare the demo-domain list the page fetched itself with ``domains`` HERE: only a boolean and a count leave."""
    if type(status) is not int:
        return {"status": 0, "hit": None, "count": None}
    if status != 200:
        return {"status": status, "hit": None, "count": None}
    listed = body.get("demoDomains") if isinstance(body, dict) else None
    if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
        return {"status": 200, "hit": None, "count": None}
    known = {_norm_domain(x) for x in listed}
    return {"status": 200, "hit": any(_norm_domain(d) in known for d in domains), "count": len(listed)}


# Service workers and WebSockets are invisible to page.route: any registration refuses the run before the form is touched.
SERVICE_WORKER_JS = """async () => {
  try {
    if (!('serviceWorker' in navigator)) return {count: 0};
    const regs = await navigator.serviceWorker.getRegistrations();
    return {count: regs.length};
  } catch (e) { return {count: null}; }
}"""

# Runs on a toggle element: true only when the element at its centre is the toggle itself or its own label, so a click
# can never land on another control (Confirm, an overlay). Reads geometry only.
HIT_TEST_JS = """(el) => {
  try {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit) return false;
    if (hit === el || el.contains(hit)) return true;
    const label = hit.closest ? hit.closest('label') : null;
    if (!label) return false;
    return label.contains(el) || (!!el.id && label.getAttribute('for') === el.id);
  } catch (e) { return false; }
}"""

REVISION_PATTERN = re.compile(r"[A-Za-z0-9_.:+-]{1,64}")


def revision_ok(value: object) -> bool:
    """A Salesforce revision stamp (letters, digits and _ . : + -, at most 64): nothing that could carry other text."""
    return isinstance(value, str) and REVISION_PATTERN.fullmatch(value) is not None


def service_worker_verdict(reply: object) -> str | None:
    """None only when the page reports exactly zero service-worker registrations (unknown fails closed)."""
    count = reply.get("count") if isinstance(reply, Mapping) else None
    if type(count) is int and count == 0:
        return None
    if type(count) is int and count > 0:
        return "service_worker_present"
    return "service_worker_check_failed"


_LOCKED_SUFFIXES = ("production_sessions_flag_missing", "production_approval_missing", "production_approval_expired",
                    "production_dry_run_not_supported", "production_runner_not_supported")
KNOWN_REASONS = frozenset({
    "production_dry_run_arguments_invalid", "backoffice_origin_not_approved", "invalid_backoffice_origin",
    "approval_ref_mismatch", "approval_revision_drift", "approval_unreadable", "approval_missing", "approval_consumed",
    "approval_expired", "approval_co_mismatch", "approval_route_mismatch", "approval_locked", "approval_locked_stale",
    "route_not_ce_only_new", "salesforce_email_domain_not_single", "salesforce_source_mismatch",
    "salesforce_prior_uuid_present", "clone_missing", "clone_stale", "clone_mismatch", "duplicate_in_production",
    "multiple_live_paid_tenants", "timing_window_not_met", "sessions_not_ready", "schema_unexpected",
    "dry_run_flag_invalid", "form_fill_failed", "form_verification_failed", "clock_unavailable", "record_write_failed",
    "unexpected_schema", "create_form_unavailable", "fill_form_schema_unavailable", "fill_value_mismatch",
    "fill_value_invalid", "advanced_control_absent", "advanced_section_not_found", "advanced_mismatch", "production_session_expired",
    "production_session_not_ready", "duplicate_ambiguous", "duplicate_check_unavailable", "demo_domain_conflict",
    "write_request_blocked", "form_left_open", "service_worker_present", "service_worker_check_failed",
    "toggle_click_failed", "approval_expired_during_run", "clone_stale_during_run", "production_locked_during_run",
    "production_dry_run_click_not_allowed", "production_dry_run_login_timeout", "playwright_runtime_unavailable", "salesforce_alias_not_pinned", "salesforce_org_not_pinned",
    "production_dry_run_page_unavailable", "production_dry_run_crashed", "salesforce_fill_source_unavailable",
    "ce_route_mismatch", "ce_license_dates_unavailable", "unmapped_text_label", "text_labels_incomplete",
    "unmapped_select", "selects_incomplete", "unmapped_toggle", "toggles_incomplete", "license_dates_invalid",
    "plan_shape_unexpected", "value_has_control_character", "leonardo_profile_unavailable", "browser_cdp_unavailable",
    "browser_tab_unavailable", "production_dry_run_form_not_reached", "dry_run_line_not_redacted",
    "invalid_co_reference", "confirm_preflight_failed", "unexpected_error", *(f"production_locked_{s}" for s in _LOCKED_SUFFIXES)})


def known_reason(code: object) -> str:
    """``code`` when it is one of the fixed reason codes, else ``unexpected_error`` (never free text)."""
    return code if isinstance(code, str) and code in KNOWN_REASONS else "unexpected_error"


# --- Request guard (stricter than the probe's) --------------------------------------------------------------------
SAFE_METHODS = frozenset({"GET", "HEAD"})
DRY_RUN_READ_POST_PATHS = (re.compile(re.escape(production_probe.TABLE_READ_PATH)),)
# Forbidden control semantics: no click may ever target a control whose name suggests one of these.
FORBIDDEN_CLICK_WORDS = ("confirm", "submit", "save")
# The only label that may open the form: the probe's create-control candidates ("Add Account" opens it client-side).
# The dry run clicks ONLY the button whose exact visible text is "Add Account" (the probe found exactly that).
CREATE_LABEL = "Add Account"
CREATE_OPEN_LABELS = frozenset({CREATE_LABEL.casefold()})


_STATIC_EXTENSIONS = frozenset({"js", "css", "png", "svg", "woff", "woff2", "ico", "json"})
_SAME_SITE = re.compile(r"https://(?:[a-z0-9-]+\.)*pentera\.io")


def _static_same_site(url: str) -> bool:
    """A same-site (pentera.io) static asset: https and a path ending in one of the allowed extensions."""
    origin = production_probe.url_origin(url)
    if not _SAME_SITE.fullmatch(origin):
        return False
    last = production_probe.url_path(url).rsplit("/", 1)[-1]
    return "." in last and last.rsplit(".", 1)[-1].casefold() in _STATIC_EXTENSIONS


def request_decision(method: str, url: str, post_data: object = None) -> tuple[bool, str]:
    """(allowed, reason). GET/HEAD only to the production origin (or a same-site static asset by extension), and POST
    only to the account-table read on the production origin WITH a validated read body (``production_probe.
    read_body_problem``: no body, a write-ish key or an unexpected shape is never a clearance). Everything else is aborted."""
    verb = (method or "").upper()
    on_production = production_probe.url_origin(url) == production_probe.PRODUCTION_ORIGIN
    if verb in SAFE_METHODS:
        if on_production or _static_same_site(url):
            return True, "read_method"
        return False, "cross_origin_blocked"
    if (verb == "POST" and on_production
            and any(p.fullmatch(production_probe.url_path(url)) for p in DRY_RUN_READ_POST_PATHS)):
        if production_probe.read_body_problem(post_data) is not None:
            return False, "table_body_not_read_only"
        return True, "allowlisted_read_post"
    return False, "write_or_unlisted_method_blocked"


class DryRunRequestGuard(production_probe.RequestGuard):
    """Routes every request of the dry-run page: reads pass, every other request is aborted and counted by name."""

    def handle(self, route: Any) -> None:
        try:
            method, url = str(route.request.method).upper(), str(route.request.url)
        except Exception:
            route.abort()
            return
        try:  # the body the guard sees: after ``route.fallback(post_data=...)`` it is the rewritten one
            post_data = getattr(route.request, "post_data", None)
        except Exception:
            post_data = None
        allowed, _reason = request_decision(method, url, post_data)
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

    def blocked_api_writes(self) -> int:
        """Aborted requests to the production API (an attempted account/add or account/edit counts here)."""
        return sum(count for (method, where), count in self.blocked.items()
                   if where.startswith("/api/") and method not in SAFE_METHODS)


# --- Click allowlist ----------------------------------------------------------------------------------------------
CLICK_OPEN_CREATE_FORM = "open_create_form"
CLICK_CANCEL_CREATE_FORM = "cancel_create_form"
CLICK_EXPAND_ADVANCED = "expand_advanced"
CLICK_SET_TOGGLE = "set_toggle"
CLICK_SORT_HEADER = "sort_table_header"  # a column-header sort: it only triggers the page's own read-only table request
ALLOWED_CLICKS = frozenset({CLICK_OPEN_CREATE_FORM, CLICK_CANCEL_CREATE_FORM, CLICK_EXPAND_ADVANCED, CLICK_SET_TOGGLE,
                            CLICK_SORT_HEADER})


class ClickRefused(RuntimeError):
    """A click outside the allowlist, or on a control named like Confirm/Submit/Save, was requested."""


class ClickLog:
    """Every click of the dry run goes through here. Nothing with Submit/Confirm/Save semantics can pass."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def click(self, kind: str, locator: Any, label: str = "", **kwargs: Any) -> None:
        low = " ".join(str(label).casefold().split())
        if kwargs.get("force"):  # a forced click can land on whatever covers the control: never in production
            raise ClickRefused("production_dry_run_click_not_allowed")
        if kind not in ALLOWED_CLICKS or any(word in low for word in FORBIDDEN_CLICK_WORDS):
            raise ClickRefused("production_dry_run_click_not_allowed")
        if kind == CLICK_OPEN_CREATE_FORM and low not in CREATE_OPEN_LABELS:
            raise ClickRefused("production_dry_run_click_not_allowed")
        locator.click(**kwargs)
        self.kinds.append(kind)


# --- Labels -------------------------------------------------------------------------------------------------------
def label_key(text: object) -> str:
    """A label with whitespace collapsed and a trailing required marker removed (case is kept: names are exact)."""
    return " ".join(str(text if isinstance(text, str) else "").split()).rstrip(" *").strip()


def label_regex(label: str) -> re.Pattern[str]:
    """Whitespace-tolerant, otherwise exact, whole-label pattern for ``page.get_by_label``."""
    words = [re.escape(word) for word in label_key(label).split()]
    return re.compile(r"^\s*" + r"\s+".join(words) + r"\s*\*?\s*$")


def slug(label: object) -> str:
    """A short machine key for a label (record keys stay under the 40-character secret-blob rule)."""
    text = label_key(label).casefold().replace("(comma separated values)", "")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:36] or "field"


# The production form, as recorded by the 2026-10-16 read-only probe (field names and labels only).
TEXT_LABELS = (
    "Company name", "Company primary domain", "User email domains (Comma Separated Values)",
    "First name", "Last name", "Organization Email",
    "Leaked Credentials scanned domains (Comma Separated Values)",
    "Number of assets", "Number of domains", "Number of subdomains",
)
BLANK_LABELS = (
    "Alternate Domains (Comma Separated Values)", "SubDomains (Comma Separated Values)",
    "Networks (Comma Separated Values)", "Phone number", "Job title",
)
OPERATOR_LABEL = "Select Operator Accounts"
SCAN_NOW_LABEL = "Scan now"
SELECT_NAMES = ("accountType", "accountCountry", "scanningInterval", "leakedCredentialsScanningInterval", "licenseType")
EXPECTED_OPTION_COUNTS = {"accountType": 3, "accountCountry": 251, "scanningInterval": 4,
                          "leakedCredentialsScanningInterval": 4, "licenseType": 5}
TOGGLE_NAMES = ("mfaRequired", "notificationsAllowed", "multipleUsersAllowed", "apiAccessAllowed", "phishingEnabled",
                "leakedCredentialsAllowed", "provisioningEnabled", "subDomainsNumberAllowed")
DATA_AM = {"license_start": "AddEditTenantModal-date-startDate", "license_end": "AddEditTenantModal-date-expirationDate"}
ADVANCED_NAMES = ("automatedDiscoveryEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled",
                  "webDorkingEnabled", "fullNucleiScanEnabled", "authenticatedTestingEnabled",
                  "staticOutboundIpEnabled", "aiEnabled", "multipleAttackStacksEnabled")
# Read only and informational: no Dev target exists for it (SpyCloud ON is the server default, docs/48 section 2).
INFO_ADVANCED_NAMES = ("spyCloudEnabled",)
ADVANCED_BUTTON_TEXT = "Advanced options"

_KNOWN_LABELS = frozenset(label_key(x) for x in (*TEXT_LABELS, *BLANK_LABELS, OPERATOR_LABEL, SCAN_NOW_LABEL))
_KNOWN_NAMES = frozenset((*SELECT_NAMES, *TOGGLE_NAMES))
_KNOWN_ADVANCED = frozenset((*ADVANCED_NAMES, *INFO_ADVANCED_NAMES))
_KNOWN_DATA_AM = frozenset(DATA_AM.values())
_UNKNOWN_REQUIRED = "unexpected_schema"


class MappingError(ValueError):
    """The Dev fill plan has a value the production form cannot take (stable code in ``args[0]``)."""


@dataclass(frozen=True, slots=True)
class IntendedForm:
    """What the dry run types and later verifies. ``selects`` are (name, option text, fill-early); no secrets."""
    texts: tuple[tuple[str, str], ...]
    blanks: tuple[str, ...]
    selects: tuple[tuple[str, str, bool], ...]
    toggles: tuple[tuple[str, bool], ...]
    advanced_targets: tuple[tuple[str, bool], ...]
    dates: tuple[tuple[str, date], ...]
    scan_now: bool | None


def map_plan(plan: Mapping[str, Any], select_names: Mapping[str, str], early_labels: tuple[str, ...]) -> IntendedForm:
    """Map the Dev CE-only fill plan onto the production control names and labels. Fails closed on anything unmapped.

    Selects are matched by their exact name attribute (via the Dev label -> name table); text controls by label with
    whitespace collapsed ("User email domains  (...)" and "User email domains (...)" are the same label).
    """
    try:
        raw_texts, raw_selects, raw_boxes = plan["texts"], plan["selects"], plan["checkboxes"]
        start, end = plan["license_start"], plan["license_end"]
    except (KeyError, TypeError) as exc:
        raise MappingError("plan_shape_unexpected") from exc
    by_key = {label_key(label): label for label in TEXT_LABELS}
    texts = []
    for dev_label, value in raw_texts.items():
        canonical = by_key.get(label_key(dev_label))
        if canonical is None or not isinstance(value, str):
            raise MappingError("unmapped_text_label")
        if re.search(r"[\x00-\x1f\x7f]", value):  # a newline in a typed value could submit a form: never typed
            raise MappingError("value_has_control_character")
        texts.append((canonical, value))
    if {label for label, _ in texts} != set(TEXT_LABELS):
        raise MappingError("text_labels_incomplete")
    selects = []
    for dev_label, option in raw_selects.items():
        name = select_names.get(dev_label)
        if name not in SELECT_NAMES or not isinstance(option, str) or not option:
            raise MappingError("unmapped_select")
        selects.append((name, option, dev_label in early_labels))
    if {name for name, _o, _e in selects} != set(SELECT_NAMES):
        raise MappingError("selects_incomplete")
    toggles, targets, scan_now = [], [], None
    for key, target in raw_boxes.items():
        if not isinstance(target, bool):
            raise MappingError("unmapped_toggle")
        if key == "scan_now":
            scan_now = target
        elif key in TOGGLE_NAMES:
            toggles.append((key, target))
        elif key in ADVANCED_NAMES:
            targets.append((key, target))
        else:
            raise MappingError("unmapped_toggle")
    if {k for k, _ in toggles} != set(TOGGLE_NAMES) or {k for k, _ in targets} != set(ADVANCED_NAMES):
        raise MappingError("toggles_incomplete")
    if not isinstance(start, date) or not isinstance(end, date):
        raise MappingError("license_dates_invalid")
    return IntendedForm(tuple(texts), BLANK_LABELS, tuple(selects), tuple(toggles), tuple(targets),
                        (("license_start", start), ("license_end", end)), scan_now)


def field_keys(intended: IntendedForm) -> list[str]:
    """The record keys of every control the dry run verifies (names only, no values)."""
    keys = [f"text.{slug(label)}" for label, _ in intended.texts]
    keys += [f"blank.{slug(label)}" for label in intended.blanks]
    keys += [f"select.{name}" for name, _o, _e in intended.selects]
    keys += [f"toggle.{name}" for name, _t in intended.toggles]
    keys += [f"date.{key}" for key, _ in intended.dates]
    keys += ["operator_account_empty"]
    if intended.scan_now is not None:
        keys.append("toggle.scan_now")
    return keys


def compare_reads(intended: IntendedForm, reads: Mapping[str, Any],
                  select_overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Compare what was read back with what was intended: ``{key: "pass" | "fail"}``. Missing or unreadable is fail.

    ``reads`` = {"texts": {label: value|None}, "blanks": {label: value|None}, "selects": {name: text|None},
    "toggles": {name: bool|None}, "dates": {key: bool|None}, "operator_empty": bool|None, "scan_now": bool|None}.
    ``select_overrides`` holds the resolved option text where it legitimately differs (the country alias rule).
    """
    overrides = select_overrides or {}
    texts, blanks = _mapping(reads.get("texts")), _mapping(reads.get("blanks"))
    selects, toggles, dates = _mapping(reads.get("selects")), _mapping(reads.get("toggles")), _mapping(reads.get("dates"))
    result: dict[str, str] = {}
    for label, expected in intended.texts:
        actual = texts.get(label)
        result[f"text.{slug(label)}"] = "pass" if isinstance(actual, str) and actual == expected else "fail"
    for label in intended.blanks:
        actual = blanks.get(label)
        result[f"blank.{slug(label)}"] = "pass" if isinstance(actual, str) and not actual.strip() else "fail"
    for name, option, _early in intended.selects:
        want = overrides.get(name, option)
        actual = selects.get(name)
        result[f"select.{name}"] = "pass" if isinstance(actual, str) and " ".join(actual.split()) == want else "fail"
    for name, target in intended.toggles:
        result[f"toggle.{name}"] = "pass" if toggles.get(name) is target else "fail"
    for key, _value in intended.dates:
        result[f"date.{key}"] = "pass" if dates.get(key) is True else "fail"
    result["operator_account_empty"] = "pass" if reads.get("operator_empty") is True else "fail"
    if intended.scan_now is not None:
        result["toggle.scan_now"] = "pass" if reads.get("scan_now") is intended.scan_now else "fail"
    return result


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# --- Schema (the probe's recorded form) ---------------------------------------------------------------------------
def schema_problem(form: object, *, after_advanced: bool = False) -> str | None:
    """``unexpected_schema`` (or ``create_form_unavailable``) unless the read form matches the recorded production form.

    ``form`` is ``production_probe.sanitize_form`` output. Checks: every expected control exists (labels, names, the
    two dates by data-am); select option counts equal the probe's (3, 251, 4, 4, 5); any control that is not part of
    the expected set AND is required is refused. With ``after_advanced`` the advanced control names are also known.
    """
    if not isinstance(form, Mapping) or not isinstance(form.get("fields"), list) or not form["fields"]:
        return "create_form_unavailable"
    names, labels, data_ams, counts = set(), set(), set(), {}
    known_names = _KNOWN_NAMES | (_KNOWN_ADVANCED if after_advanced else frozenset())
    for field in form["fields"]:
        if not isinstance(field, Mapping):
            return _UNKNOWN_REQUIRED
        name = field.get("name") if isinstance(field.get("name"), str) else ""
        label = label_key(field.get("label"))
        data_am = field.get("data_am") if isinstance(field.get("data_am"), str) else ""
        names.add(name)
        labels.add(label)
        data_ams.add(data_am)
        if name in EXPECTED_OPTION_COUNTS:
            counts[name] = field.get("option_count")
        known = (name in known_names or data_am in _KNOWN_DATA_AM or label in _KNOWN_LABELS
                 or (not name and not label and not data_am and field.get("type") == "hidden"))
        if not known and field.get("required") is True:
            return _UNKNOWN_REQUIRED
    if any(counts.get(name) != expected for name, expected in EXPECTED_OPTION_COUNTS.items()):
        return _UNKNOWN_REQUIRED
    for label in (*TEXT_LABELS, *BLANK_LABELS, OPERATOR_LABEL):
        if label_key(label) not in labels:
            return _UNKNOWN_REQUIRED
    if not set(TOGGLE_NAMES) <= names or not _KNOWN_DATA_AM <= data_ams:
        return _UNKNOWN_REQUIRED
    return None


# --- Advanced controls (read only) ---------------------------------------------------------------------------------
def advanced_report(section_found: bool, states: Mapping[str, Any], targets: Mapping[str, bool]) -> dict[str, Any]:
    """The record fields for the advanced section: found/not_found, on/off/absent per name, and the mismatches.

    ``states`` maps a control name to True (checked), False (unchecked) or None (absent). A mismatch is a control
    that is absent, or whose state differs from the Dev target (``targets``); informational controls never mismatch.
    """
    controls: dict[str, str] = {}
    mismatches: list[str] = []
    for name in (*ADVANCED_NAMES, *INFO_ADVANCED_NAMES):
        state = states.get(name)
        controls[name] = "absent" if state is None else ("on" if state is True else "off")
        if name in targets and (state is None or state is not targets[name]):
            mismatches.append(name)
    return {"advanced_section": "found" if section_found else "not_found", "advanced_controls": controls,
            "advanced_mismatches": mismatches}


def advanced_problem(report: Mapping[str, Any]) -> str | None:
    """Refuse when Dev sets a control that production does not have, or one that is not in its Dev target state.

    The fill sets each advanced control to its target first (owner go 2026-10-18; plain, hit-tested clicks only) and this
    check reads the result: a control that did not end in its target state would be submitted as found, so
    ``advanced_mismatch`` refuses before any Confirm. The record keeps the control NAMES only.
    """
    controls = report.get("advanced_controls") or {}
    if any(controls.get(name) == "absent" for name in ADVANCED_NAMES):
        return "advanced_section_not_found" if report.get("advanced_section") == "not_found" else "advanced_control_absent"
    mismatches = report.get("advanced_mismatches")
    if not isinstance(mismatches, (list, tuple)):
        return "advanced_mismatch"  # a report without the comparison is never a clearance
    return "advanced_mismatch" if mismatches else None


# --- Session, duplicate and demo-domain verdicts --------------------------------------------------------------------
def _status(reply: object) -> int | None:
    status = reply.get("status") if isinstance(reply, Mapping) else None
    return status if type(status) is int else None


def session_verdict(table_status: object, observed: Mapping[str, Any] | None = None) -> str | None:
    """None when signed in, judged from the page's OWN traffic and STATUS CODES only (docs/48 note 2026-10-18).

    ``table_status`` is the status of the page's own (rewritten) table reply: 200 is required; 401 or 403 is
    ``production_session_expired``; no reply or any other status is ``production_session_not_ready``. ``observed`` maps the
    page's own getAuthorities / accountInfo paths to the status seen when the page made those calls: a 401 or 403 there is
    also expired and any other non-200 is not ready; a call the page never made is simply absent (not required).
    """
    seen = [table_status, *(observed or {}).values()]
    if any(type(x) is int and x in (401, 403) for x in seen):
        return "production_session_expired"
    if type(table_status) is not int or table_status != 200:
        return "production_session_not_ready"
    if any(type(x) is not int or x != 200 for x in (observed or {}).values()):
        return "production_session_not_ready"
    return None


def _norm_name(value: object) -> str:
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def _norm_domain(value: object) -> str:
    return value.casefold().strip().rstrip(".") if isinstance(value, str) else ""


def search_verdict(reply: object, tenant_name: str, domains: tuple[str, ...]) -> str | None:
    """None when one table search found no tenant with the exact name or any of the domains; else the reason code."""
    status = _status(reply)
    if status in (401, 403):
        return "production_session_expired"
    rows = reply.get("rows") if isinstance(reply, Mapping) else None
    total = reply.get("total") if isinstance(reply, Mapping) else None
    if status != 200 or not isinstance(rows, list) or type(total) is not int or total < 0:
        return "duplicate_check_unavailable"
    if total > len(rows):
        return "duplicate_ambiguous"
    name = _norm_name(tenant_name)
    wanted = {d for d in map(_norm_domain, domains) if d}
    if not name or not wanted:
        return "duplicate_check_unavailable"
    for row in rows:
        if not isinstance(row, Mapping):
            return "duplicate_check_unavailable"
        theirs = {_norm_domain(row.get("domain")), *map(_norm_domain, row.get("alternates") or ()),
                  *map(_norm_domain, row.get("emails") or ())}
        if _norm_name(row.get("name")) == name or wanted & theirs:
            return "duplicate_in_production"
    return None


def demo_verdict(reply: object) -> str | None:
    """None when the demo-domain list was read and neither the primary nor an email domain is in it."""
    status = _status(reply)
    if status in (401, 403):
        return "production_session_expired"
    if status != 200 or not isinstance(reply, Mapping) or type(reply.get("hit")) is not bool:
        return "duplicate_check_unavailable"
    return "demo_domain_conflict" if reply["hit"] else None


def duplicate_verdict(search_replies: list[object], demo_reply: object, tenant_name: str,
                      domains: tuple[str, ...]) -> str | None:
    """The first refusal across the name search, the domain search(es) and the demo list; None only when all are clear."""
    if not search_replies:
        return "duplicate_check_unavailable"
    for reply in search_replies:
        problem = search_verdict(reply, tenant_name, domains)
        if problem is not None:
            return problem
    return demo_verdict(demo_reply)


# --- Redaction -----------------------------------------------------------------------------------------------------
_SECRETISH = (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), re.compile(r"(?i)\b[0-9a-f]{32}\b"),
              re.compile(r"eyJ[A-Za-z0-9_-]{8,}"), re.compile(r"(?i)bearer\s+\S+"))


def line_problem(text: str) -> str | None:
    """None when a line to print holds no email, UUID, JWT or bearer-shaped text."""
    return "dry_run_line_not_redacted" if any(p.search(text) for p in _SECRETISH) else None

