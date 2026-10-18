"""Shared fake of the Playwright surface the production page-own table reads use (docs/48, docs/53).

The page under test never fetches anything itself: a trigger (sort-header click, dispatched click, reload, goto) makes the
FAKE PAGE send its OWN table request (a fixed default body, as the real Accounts page would), routed through every
registered handler exactly like Playwright does (last registered first, ``fallback`` passes the possibly rewritten request
on, ``continue_`` / ``abort`` end the chain). A served request answers through ``_serve_table(value, projection)``, a hook
of the host fake page, and emits ``response`` events. Passive GETs (getAuthorities, accountInfo, demoAccountDomains) are
emitted on every document load (reload / goto), like the real application's start-up calls.
No browser, no network.
"""

from __future__ import annotations

import fnmatch
import json
from types import SimpleNamespace
from typing import Any

from integration.onboarding import production_contract as contract
from integration.onboarding import production_dry_run as pdr

ORIGIN = "https://app.pentera.io"
TABLE_PATH = contract.ACCOUNTS_PATH
TABLE_URL = ORIGIN + TABLE_PATH
HEADER_SELECTOR = "thead th button"
# The table request the page sends on its own: an unfiltered load (offset 20 so a rewrite that forgets offset 0 shows up).
PAGE_DEFAULT_BODY = json.dumps({"tableServerData": {
    "offset": 20, "items_per_page": 25, "sort": {"direction": "desc", "key": "lastReconScan"}, "filters": {},
    "unique_fields": ["accountType", "licenseType", "firstName"], "projection": ["accountName", "lastReconScan"]}})
SECRET = "SECRET-TOKEN-VALUE"


class FakeRequest:
    def __init__(self, method, url, post_data=None):
        self.method, self.url = method, url
        if post_data is not None:  # a request without a body attribute stays possible: the guard must cope with it
            self.post_data = post_data


class FakeRoute:
    def __init__(self, method, url, post_data=None):
        self.request = FakeRequest(method, url, post_data)
        self.outcome = None
        self.abort_called = False
        self.continue_called = False
        self.fell_back = False

    def fallback(self, **kw):
        self.fell_back = True
        if kw.get("post_data") is not None:  # Playwright shows the override to the next handler
            self.request.post_data = kw["post_data"]

    def continue_(self, **kw):
        self.continue_called = True
        self.outcome = "continued"
        if kw.get("post_data") is not None:
            self.request.post_data = kw["post_data"]

    def abort(self, *_a):
        self.abort_called = True
        self.outcome = "aborted"


class FakeResponse:
    """A response event / expect_response value. ``headers`` raises: nothing may ever read a header."""

    def __init__(self, method, url, status, payload, request=None, unreadable=False):
        self.request = request if request is not None else SimpleNamespace(method=method)
        self.url, self.status, self._payload, self._unreadable = url, status, payload, unreadable
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        if self._unreadable:
            raise ValueError("body gone")
        return self._payload

    @property
    def headers(self):
        raise AssertionError("a response header was read")

    def all_headers(self):
        raise AssertionError("a response header was read")


class FakeHeaderControl:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def click(self, **_kw):
        if self.page.form_open:
            raise TimeoutError("element intercepts pointer events")  # the modal covers the table
        self.page.header_clicks += 1
        self.page._table_request()

    def dispatch_event(self, name, *_a, **_k):
        assert name == "click"
        self.page.dispatches += 1
        self.page._table_request()


class _Expect:
    def __init__(self, page, predicate):
        self.page, self.predicate, self.start, self.value = page, predicate, 0, None

    def __enter__(self):
        self.start = len(self.page.responses)
        return self

    def __exit__(self, exc_type, *_rest):
        if exc_type:
            return False
        for response in self.page.responses[self.start:]:
            if self.predicate(response):
                self.value = response
                return False
        raise TimeoutError("Timeout waiting for response")


def matches(pattern: Any, url: str) -> bool:
    if hasattr(pattern, "search"):
        return bool(pattern.search(url))
    return fnmatch.fnmatch(url, pattern)


def inverse_row(row: Any) -> Any:
    """A reduced search row (name, domain, alternates, emails, deleted) as the server's table_data row."""
    if not isinstance(row, dict) or "accountName" in row:
        return row
    return {"accountName": row.get("name"), "accountDomain": row.get("domain"), "alternateDomains": row.get("alternates"),
            "userEmailDomains": row.get("emails"), "isDeleted": row.get("deleted"), "token": SECRET,
            "primaryUser": {"email": SECRET}}


def reply_payload(reply: Any, readback: bool) -> tuple[int, Any] | None:
    """(status, body) the server answers for a reduced test reply, or None for no response at all."""
    if not isinstance(reply, dict) or not reply.get("status"):
        return None
    status = reply["status"]
    if status != 200:
        return status, {"error": SECRET}
    rows, total = reply.get("rows"), reply.get("total")
    if rows is None or total is None:
        return 200, {"unexpected": SECRET}
    table = list(rows) if readback else [inverse_row(r) for r in rows]
    return 200, {"pagination_response": {"total_count": total, "table_data": table}}


def search_value(body: Any) -> str | None:
    try:
        clause = body["tableServerData"]["filters"]["and"][0]
        clause = clause["or"][0] if "or" in clause else clause
        return clause["value"]
    except (KeyError, IndexError, TypeError):
        return None


def demo_payload(reply: Any, domain: str) -> tuple[int, Any] | None:
    """The demoAccountDomains answer for a reduced demo reply {status, hit, count}; ``domain`` is what a hit contains."""
    if not isinstance(reply, dict) or not reply.get("status"):
        return None
    if reply["status"] != 200:
        return reply["status"], {"error": SECRET}
    if reply.get("hit") is None:
        return 200, {"unexpected": SECRET}
    listed = ["other.example", SECRET] + ([domain.upper() + "."] if reply["hit"] else [])
    return 200, {"demoDomains": listed}


class PageOwnTable:
    """Mixin for a fake page. The host defines ``form_open`` and ``_serve_table(value, projection)`` (a reduced reply)."""

    def _init_table(self, *, header: bool = True, passive: dict[str, Any] | None = None, demo: Any = None,
                    demo_domain: str = "") -> None:
        self.routes, self.routed, self.handlers = [], [], {}
        self.header_present = header
        self.header_clicks = self.dispatches = self.reloads = self.gotos = 0
        self.responses, self.fetches, self.table_values = [], [], []
        self.table_bodies = []
        self.passive = passive if passive is not None else {}
        self.demo_reply, self.demo_domain = demo, demo_domain
        self.skip_table_response = False  # simulates a page that never answers its own table request

    # -- routing, exactly like Playwright: the last registered handler runs first, fallback passes on
    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def unroute(self, pattern, handler=None):
        self.routes = [(p, h) for p, h in self.routes if not (p is pattern and (handler is None or h == handler))]

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def _emit(self, event, obj):
        for handler in list(self.handlers.get(event, [])):
            handler(obj)

    def fire(self, method, url, post_data=None):
        route = FakeRoute(method, url, post_data)
        self.routed.append(route)
        for pattern, handler in reversed(list(self.routes)):
            if not matches(pattern, url):
                continue
            handler(route)
            if route.outcome:
                break
        return route.outcome

    # -- the page's own traffic
    def _emit_passive(self):
        for path, reply in self.passive.items():
            if self.fire("GET", ORIGIN + path) == "aborted" or not isinstance(reply, dict) or not reply.get("status"):
                continue
            self._emit("response", FakeResponse("GET", ORIGIN + path, reply["status"], None))
        demo = demo_payload(self.demo_reply, self.demo_domain) if self.demo_reply is not None else None
        if demo is not None and self.fire("GET", ORIGIN + pdr.DEMO_DOMAINS_PATH) != "aborted":
            self._emit("response", FakeResponse("GET", ORIGIN + pdr.DEMO_DOMAINS_PATH, demo[0], demo[1]))

    def _table_request(self):
        if self.fire("POST", TABLE_URL, PAGE_DEFAULT_BODY) == "aborted" or self.skip_table_response:
            return
        request = self.routed[-1].request
        body = json.loads(request.post_data)
        self.table_bodies.append(body)
        value = search_value(body)
        projection = body["tableServerData"].get("projection")
        self.table_values.append(value)
        self.fetches.append(("POST", TABLE_PATH))
        answer = reply_payload(self._serve_table(value, projection), projection == list(contract.READBACK_PICK_PATHS))
        if answer is None:
            return
        response = FakeResponse("POST", TABLE_URL, answer[0], answer[1], request=request)
        self.responses.append(response)
        self._emit("response", response)

    def reload(self, **_kw):
        self.reloads += 1
        self._emit_passive()
        self._table_request()

    def goto(self, url, **_kw):
        if url == "about:blank":
            return None
        self.gotos += 1
        self._emit_passive()
        self._table_request()
        return SimpleNamespace(status=200)

    def expect_response(self, predicate, timeout=None):
        return _Expect(self, predicate)

    def _header(self):
        return FakeHeaderControl(self) if self.header_present else None
