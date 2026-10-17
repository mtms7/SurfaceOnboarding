"""Pure building blocks of the guarded LIVE production CE-only create (docs/53, docs/45 D1-D3, docs/49).

Nothing here opens a connection, starts a process or reads a file. It is plain logic plus duck-typed page helpers:
the request guard that allows exactly ONE create POST, the click log that allows exactly ONE Confirm click, the
confirm click itself, the live duplicate pre-check verdict and the bounded post-create readback. Everything is
unreachable while ``session_readiness.PRODUCTION_RUNNER_SUPPORTED`` is False: the only caller is
``run_production_create`` in tools/attended_ce_only_playwright.py, whose first step is the live production target.

Result words (see ``production_create_ui.ui_status``): ``created_verified`` (readback matches docs/49 exactly),
``created_mismatch`` (a tenant exists but the readback differs: stop, record, never retry) and
``create_outcome_unknown`` (the click may or may not have created a tenant: block the CO for the owner, never retry).

Secrets and values: the readback keeps only the compared fields of ONE row; no token, cookie or header is read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from . import production_contract, production_dry_run, production_probe

CREATE_PATH = "/api/v1/backoffice/account/add"
CONFIRM_LABEL = "Confirm"
CLICK_CONFIRM_CREATE = "confirm_create"
SUBMITTED = "submitted"           # same words as production_create (the orchestrator compares them)
UNCERTAIN = "uncertain"
NOT_CLICKED = "not_clicked"       # nothing was sent: the confirm control could not be used safely
RESPONSE_POLLS = 40               # x 500 ms: how long the one create response is awaited
RESPONSE_POLL_MS = 500
READBACK_ATTEMPTS = 6
READBACK_WAIT_MS = 3_000

LIVE_REASONS = frozenset({
    "live_duplicate_check_missing", "confirm_not_clicked", "production_create_arguments_invalid",
    "production_create_crashed", "production_create_click_not_allowed", "create_second_post_blocked",
    "owner_go_missing", "dry_run_required", "prior_attempt_needs_review", "prior_records_invalid",
    "production_locked_production_dry_run_not_supported", "launch_in_progress", "operator_not_owner",
    "readback_unavailable", "readback_contract_unchecked", "readback_contract_mismatch",
    "readback_name_mismatch", "readback_domains_mismatch", "readback_account_id_missing",
    "readback_account_id_mismatch", "readback_uuid_invalid", "uncertain_submit", "attempt_started",
    "approval_consume_failed", "approval_superseded", "approval_locked", "approval_locked_stale",
    "production_dry_run_login_timeout", "production_session_expired", "production_session_not_ready",
    "dry_run_old", "salesforce_org_not_pinned", "country_code_unavailable", "salesforce_alias_not_pinned", "intended_unavailable", "production_dry_run_form_not_reached", "search_unproven", "search_control_unavailable",
    "confirm_preflight_failed", "extra_pages_open", "launch_ticket_invalid", "source_recheck_unavailable",
    "approval_revision_drift", "salesforce_prior_uuid_present", "live_source_check_missing", "service_worker_present", "service_worker_check_failed",
})


def live_known_reason(code: object) -> str:
    """``code`` when it is one of the fixed reason codes (dry run or live), else ``unexpected_error`` (never free text)."""
    if isinstance(code, str) and code in LIVE_REASONS:
        return code
    return production_dry_run.known_reason(code)


# --- Request guard: reads plus exactly one armed create POST -------------------------------------------------------

def is_create_request(method: object, url: object) -> bool:
    """True only for POST on the exact production origin and the exact create path (no other path, query ignored)."""
    return (str(method).upper() == "POST" and production_probe.url_origin(url) == production_probe.PRODUCTION_ORIGIN
            and production_probe.url_path(url) == CREATE_PATH)


def live_request_decision(method: str, url: str, *, armed: bool) -> tuple[bool, str]:
    """(allowed, reason): the dry-run table, plus the create POST while ``armed``. Everything else is aborted."""
    if is_create_request(method, url):
        return (True, "armed_create_post") if armed else (False, "create_post_not_armed")
    return production_dry_run.request_decision(method, url)


class LiveRequestGuard(production_dry_run.DryRunRequestGuard):
    """The dry-run guard that lets ONE armed create POST through and aborts every other write, a second create included.

    ``arm()`` is called immediately before the one Confirm click. The first create request disarms the guard at once
    (before it is continued), so a repeated click, a retry by the page or a second fetch is aborted and counted.
    """

    def __init__(self) -> None:
        super().__init__()
        self.armed = False
        self.create_used = False
        self.create_blocked = 0
        self.popups = 0

    def install(self, page: Any) -> None:
        """Route at CONTEXT level (``page.route`` does not cover popups or new pages) and count every popup / new page.

        Raises when the context cannot be routed: the live create must not run with a guard that misses a page.
        """
        context = page.context
        context.route("**/*", self.handle)
        for target, event, handler in ((context, "response", self.on_response), (page, "response", self.on_response),
                                       (context, "page", self._on_popup), (page, "popup", self._on_popup)):
            try:
                target.on(event, handler)
            except Exception:
                pass

    def _on_popup(self, _page: Any) -> None:
        self.popups += 1

    def arm(self) -> None:
        if not self.create_used:
            self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def handle(self, route: Any) -> None:
        try:
            method, url = str(route.request.method).upper(), str(route.request.url)
        except Exception:
            route.abort()
            return
        if not is_create_request(method, url):
            super().handle(route)
            return
        where = self._where(url)
        if not (self.armed and not self.create_used):
            self.blocked[(method, where)] = self.blocked.get((method, where), 0) + 1
            self.create_blocked += 1
            route.abort()
            return
        self.create_used, self.armed = True, False
        entry = self.api_calls.setdefault((method, where), {"count": 0, "statuses": set()})
        entry["count"] += 1
        route.continue_()

    def create_statuses(self) -> list[int]:
        entry = self.api_calls.get(("POST", CREATE_PATH))
        return sorted(entry["statuses"]) if entry else []

    def create_requests(self) -> int:
        entry = self.api_calls.get(("POST", CREATE_PATH))
        return int(entry["count"]) if entry else 0

    def pre_click_create_attempts(self) -> int:
        """Create POSTs aborted because the guard was not armed (a stray submit during the fill)."""
        return self.create_blocked if not self.create_used else 0


# --- Click log: the one Confirm ----------------------------------------------------------------------------------------

class LiveClickLog(production_dry_run.ClickLog):
    """The dry-run click log plus exactly one ``confirm_create`` click on a control labelled exactly "Confirm"."""

    def __init__(self) -> None:
        super().__init__()
        self.confirm_clicks = 0

    def click(self, kind: str, locator: Any, label: str = "", **kwargs: Any) -> None:
        if kind != CLICK_CONFIRM_CREATE:
            super().click(kind, locator, label, **kwargs)
            return
        if kwargs.get("force") or label != CONFIRM_LABEL or self.confirm_clicks >= 1:
            raise production_dry_run.ClickRefused("production_create_click_not_allowed")
        self.confirm_clicks += 1  # counted BEFORE the click: even a click that raises can never be repeated
        locator.click(**kwargs)
        self.kinds.append(kind)


def pre_arm_problem(page: Any, guard: LiveRequestGuard) -> str | None:
    """Re-checked immediately before arming: no service worker, no popup opened, no second production page."""
    try:
        worker = page.evaluate(production_dry_run.SERVICE_WORKER_JS)
    except Exception:
        worker = None
    problem = production_dry_run.service_worker_verdict(worker)
    if problem:
        return problem
    if guard.popups:
        return "extra_pages_open"
    try:
        pages = list(page.context.pages)
    except Exception:
        return "extra_pages_open"
    same = [p for p in pages if p is not page and production_probe.url_origin(getattr(p, "url", "")) == production_probe.PRODUCTION_ORIGIN]
    return "extra_pages_open" if same else None


def post_click_problem(page: Any, guard: LiveRequestGuard) -> str | None:
    """After the click: ANY popup, new page or service worker (before or after the click) makes the outcome unknown."""
    return pre_arm_problem(page, guard)


def confirm_preflight(page: Any, *, locate: Callable[[Any], Any] | None = None,
                      wait_enabled: Callable[[Any, Any], bool] | None = None) -> str | None:
    """The locate / enabled / scroll / hit-test checks of the Confirm control WITHOUT clicking (also run by the dry run).

    None when the one exact "Confirm" control is present, enabled, visible and is the element under the pointer after
    scrolling it into view; else ``confirm_preflight_failed``.
    """
    try:
        found = locate(page) if locate is not None else page.get_by_role("button", name=CONFIRM_LABEL, exact=True)
        if found is None or production_probe._count(found) != 1:
            return "confirm_preflight_failed"
        button = found.first
        if wait_enabled is not None and not wait_enabled(page, found):
            return "confirm_preflight_failed"
        if not button.is_visible() or not button.is_enabled():
            return "confirm_preflight_failed"
        button.scroll_into_view_if_needed()
        if button.evaluate(production_dry_run.HIT_TEST_JS) is not True:
            return "confirm_preflight_failed"
    except Exception:
        return "confirm_preflight_failed"
    return None


def click_confirm_once(page: Any, guard: LiveRequestGuard, clicks: LiveClickLog, *, timeout_ms: int = 5_000,
                       locate: Callable[[Any], Any] | None = None,
                       wait_enabled: Callable[[Any, Any], bool] | None = None) -> str:
    """Click the one enabled "Confirm" control exactly once and judge the single create response.

    ``NOT_CLICKED`` when the control is missing, not unique, disabled or covered (nothing was sent). Otherwise the
    guard is armed, ONE click is made (no retry, no force), the guard is disarmed again and the result is
    ``SUBMITTED`` only for exactly one create request answered 200 and no blocked second create; every other case
    (no response, another status, a second create attempt, an exception during the click) is ``UNCERTAIN``.
    """
    # ``locate`` / ``wait_enabled`` are the Dev runner's proven helpers (``_locate_confirm_button``, ``_confirm_enabled``):
    # the single exact-name "Confirm" control and the bounded wait for it to enable (no date fallback, nothing changed).
    try:
        found = locate(page) if locate is not None else page.get_by_role("button", name=CONFIRM_LABEL, exact=True)
        if found is None or production_probe._count(found) != 1:
            return NOT_CLICKED
        button = found.first
        if wait_enabled is not None and not wait_enabled(page, found):
            return NOT_CLICKED
        if not button.is_visible() or not button.is_enabled():
            return NOT_CLICKED
        button.scroll_into_view_if_needed()  # the hit test must run on the control as the click will see it
        if button.evaluate(production_dry_run.HIT_TEST_JS) is not True:  # the element under the pointer must be Confirm
            return NOT_CLICKED
        if pre_arm_problem(page, guard) is not None:
            return NOT_CLICKED
    except Exception:
        return NOT_CLICKED
    guard.arm()
    try:
        clicks.click(CLICK_CONFIRM_CREATE, button, CONFIRM_LABEL, timeout=timeout_ms)
    except production_dry_run.ClickRefused:
        guard.disarm()
        return NOT_CLICKED
    except Exception:
        guard.disarm()
        return UNCERTAIN  # the click may have reached the page before the error: unknown, never repeated
    try:
        for _poll in range(RESPONSE_POLLS):
            if guard.create_statuses():
                break
            page.wait_for_timeout(RESPONSE_POLL_MS)
    except Exception:
        pass
    finally:
        guard.disarm()
    if guard.create_blocked or guard.create_requests() != 1 or guard.create_statuses() != [200]:
        return UNCERTAIN
    if post_click_problem(page, guard) is not None:
        return UNCERTAIN  # a page opened or a worker appeared around the click: never SUBMITTED-clean
    return SUBMITTED


# --- Live duplicate pre-check verdict ------------------------------------------------------------------------------------

def live_search_body(value: str) -> dict[str, Any]:
    """The verified name/domain search grammar WITHOUT a projection, so a returned row keeps its domain fields."""
    return production_contract.build_readback_body(value)


def live_duplicate_problem(name_reply: object, domain_reply: object, demo_reply: object, tenant_name: str,
                           primary_domain: str) -> str | None:
    """None only when the name search, the domain search and the demo list are all readable and clear; else a reason.

    Stricter than the dry run on purpose: the DOMAIN search is a text search, so ANY row it returns (or any count other
    than zero) refuses even when the row's own fields were not returned. A missing match is never a clearance: an
    unreadable, partial, non-200 or odd reply is ``duplicate_check_unavailable`` / ``duplicate_ambiguous``.
    """
    problem = production_dry_run.search_verdict(name_reply, tenant_name, (primary_domain,))
    if problem is not None:
        return problem
    status = production_dry_run._status(domain_reply)
    if status in (401, 403):
        return "production_session_expired"
    rows = domain_reply.get("rows") if isinstance(domain_reply, Mapping) else None
    total = domain_reply.get("total") if isinstance(domain_reply, Mapping) else None
    if status != 200 or not isinstance(rows, list) or type(total) is not int or total < 0:
        return "duplicate_check_unavailable"
    if rows or total != 0:
        return "duplicate_in_production"
    return production_dry_run.demo_verdict(demo_reply)


def _norm(value: object) -> str:
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def clone_search_control(tenants: object) -> dict[str, str] | None:
    """Deterministic positive-control tenant from the fresh Redash clone: live (enabled, not deleted), a non-empty name and
    primary domain, each unique in the clone (so one row is expected back). Smallest SHA-256 of name|domain wins; None if no
    tenant qualifies. The name itself is never recorded (callers keep a hash)."""
    import hashlib

    if not isinstance(tenants, (list, tuple)):
        return None
    usable = []
    owners: dict[str, int] = {}  # how many clone tenants claim a domain (primary or alternate, deleted ones included)
    for tenant in tenants:
        if not isinstance(tenant, Mapping):
            continue
        claimed = {_norm(tenant.get("account_domain")), *(_norm(d) for d in (tenant.get("alternate_domains") or ())
                                                          if isinstance(d, str))}
        for domain in claimed - {""}:
            owners[domain] = owners.get(domain, 0) + 1
    for tenant in tenants:
        if (isinstance(tenant, Mapping) and tenant.get("enabled") is True and tenant.get("is_deleted") is not True
                and isinstance(tenant.get("account_name"), str) and isinstance(tenant.get("account_domain"), str)
                and tenant["account_name"].strip() and tenant["account_domain"].strip()):
            usable.append((_norm(tenant["account_name"]), _norm(tenant["account_domain"]), tenant))
    names = [u[0] for u in usable]

    def leaks(name: str, domain: str) -> bool:
        # A name that contains the domain (or its first label) would let the DOMAIN control pass through the name field.
        label = domain.split(".", 1)[0].strip()
        return domain in name or (bool(label) and label in name)

    unique = [u for u in usable if names.count(u[0]) == 1 and owners.get(u[1]) == 1 and not leaks(u[0], u[1])]
    if not unique:
        return None
    best = min(unique, key=lambda u: hashlib.sha256((u[0] + "|" + u[1]).encode("utf-8")).hexdigest())
    return {"name": best[2]["account_name"].strip(), "domain": best[2]["account_domain"].strip()}


def control_problem(control: object, name_reply: object, domain_reply: object) -> str | None:
    """RUNTIME POSITIVE CONTROL of the two searches, in the same session right before the real checks.

    ``control`` = {"name", "domain"} of a live tenant taken from the fresh Redash clone. The name search for its name and
    the domain search for its domain must each return a row of THAT tenant; otherwise the search is unproven and the
    run refuses (``search_control_unavailable`` without a control, ``search_unproven`` when it is not found). An empty
    reply therefore never counts as a clearance.
    """
    if not (isinstance(control, Mapping) and isinstance(control.get("name"), str) and control["name"].strip()
            and isinstance(control.get("domain"), str) and control["domain"].strip()):
        return "search_control_unavailable"
    wanted = _norm(control["name"])
    for reply in (name_reply, domain_reply):
        status = production_dry_run._status(reply)
        if status in (401, 403):
            return "production_session_expired"
        rows = reply.get("rows") if isinstance(reply, Mapping) else None
        if status != 200 or not isinstance(rows, list):
            return "search_unproven"
        if not any(isinstance(r, Mapping) and _norm(r.get("name")) == wanted for r in rows):
            return "search_unproven"
    return None


def checked_duplicate_problem(control: object, control_replies: tuple[object, object], name_reply: object,
                              domain_reply: object, demo_reply: object, tenant_name: str, primary_domain: str) -> str | None:
    """The positive control first (it must pass), then ``live_duplicate_problem`` on the real target."""
    problem = control_problem(control, control_replies[0], control_replies[1])
    if problem is not None:
        return problem
    return live_duplicate_problem(name_reply, domain_reply, demo_reply, tenant_name, primary_domain)


# --- Post-create readback ---------------------------------------------------------------------------------------------------

def analyse_readback(reply: object, intended: Mapping[str, Any], expected_row: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """(state, found). states: ``session_expired``, ``unavailable``, ``not_found`` (poll again) or ``found``.

    ``found`` is the mapping ``production_create.run`` compares: tenant_name, domains (the primary domain), account_id,
    account_uuid and ``contract_mismatches`` (field NAMES from ``production_contract.compare_readback``; a UUID that is
    not 32 lowercase hex adds ``accountUuid``). A row problem (duplicate name, other domain, case variant) is reported
    as a mismatch name ``row_<code>`` with no identity fields, so it can never verify.
    """
    status = reply.get("status") if isinstance(reply, Mapping) else None
    if status in (401, 403):
        return "session_expired", None
    rows = reply.get("rows") if isinstance(reply, Mapping) else None
    total = reply.get("total") if isinstance(reply, Mapping) else None
    if status != 200 or not isinstance(rows, list) or type(total) is not int or total < 0:
        return "unavailable", None
    name, domain = str(intended.get("tenant_name") or ""), str(intended.get("primary_domain") or "")
    if total > len(rows):
        return "found", {"contract_mismatches": ["row_ambiguous_rows"]}
    row, code = production_contract.select_unique_row(rows, name, domain)
    if row is None:
        if code == "no_match":
            return "not_found", None
        return "found", {"contract_mismatches": [f"row_{code}"]}
    mismatches, uuid_ok = production_contract.compare_readback(expected_row, row)
    mismatches = list(mismatches) + ([] if uuid_ok else ["accountUuid"])
    found: dict[str, Any] = {"contract_mismatches": mismatches}
    for key, source in (("tenant_name", "accountName"), ("account_id", "id"), ("account_uuid", "accountUuid")):
        value = row.get(source)
        if isinstance(value, str):
            found[key] = value
    found["domains"] = [row["accountDomain"]] if isinstance(row.get("accountDomain"), str) else []
    return "found", found


def poll_readback(fetch: Callable[[], object], intended: Mapping[str, Any], expected_row: Mapping[str, Any],
                  wait: Callable[[int], None], *, attempts: int = READBACK_ATTEMPTS,
                  wait_ms: int = READBACK_WAIT_MS) -> dict[str, Any] | None:
    """Bounded, read-only polling: ``found`` as soon as the row exists, else None (never an invented success).

    ``fetch`` performs one page-context search and returns its reply; ``wait`` sleeps ``wait_ms`` between attempts.
    A session problem or an unreadable reply stops at once (None): nothing here ever clicks or retries a create.
    """
    for attempt in range(max(1, int(attempts))):
        try:
            state, found = analyse_readback(fetch(), intended, expected_row)
        except Exception:
            return None
        if state == "found":
            return found
        if state != "not_found":
            return None
        if attempt + 1 < attempts:
            try:
                wait(wait_ms)
            except Exception:
                return None
    return None
