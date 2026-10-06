"""One read-only retry of the invisible tenant search after a reload timeout (2026-10-06).

Leonardo Development intermittently never answers a page reload. The search is read-only, so it is re-sent
once through a fresh navigation; a second failure still fails closed (None) with a page_unresponsive hint.
"""
from __future__ import annotations

import contextlib
import json
import types
import unittest
import unittest.mock

import tools.attended_ce_only_playwright as runner

TABLE_URL = runner.DEVELOPMENT_ORIGIN + runner.INVENTORY_API_PATH


class PlaywrightTimeout(Exception):
    pass


PlaywrightTimeout.__name__ = "TimeoutError"


class _Response:
    def __init__(self, request, rows):
        self.request, self.url, self.status = request, TABLE_URL, 200
        self._rows = rows

    def json(self):
        return {"pagination_response": {"table_data": self._rows, "total_count": len(self._rows)}}


class FakePage:
    """reload/goto either hang (raise a timeout) or let the app send its table request through the route."""

    def __init__(self, reload_hangs: bool, goto_hangs: bool):
        self.reload_hangs, self.goto_hangs = reload_hangs, goto_hangs
        self.handler = None
        self.calls: list[str] = []
        self.sent_bodies: list[dict] = []
        self._pending = None

    def route(self, pattern, handler):
        self.handler = handler

    def unroute(self, pattern, handler=None):
        self.handler = None

    def _app_request(self):
        page = self
        request = types.SimpleNamespace(method="POST", post_data=json.dumps(
            {"tableServerData": {"offset": 0, "items_per_page": 10, "filters": {}}}))

        class _Route:
            def __init__(self):
                self.request = request

            def continue_(self, post_data=None):
                if post_data is not None:
                    page.sent_bodies.append(json.loads(post_data))
        self.handler(_Route())
        self._pending = _Response(request, [{"id": "a" * 24, "accountName": "Acme"}])

    def reload(self, **kwargs):
        self.calls.append("reload")
        if self.reload_hangs:
            raise PlaywrightTimeout("Page.reload: Timeout 30000ms exceeded.")
        self._app_request()

    def goto(self, url, **kwargs):
        if url == "about:blank":
            self.calls.append("blank")
            self._pending = None  # the hung document and its late reply are gone
            return None
        self.calls.append("goto")
        if self.goto_hangs:
            raise PlaywrightTimeout("Page.goto: Timeout 30000ms exceeded.")
        self._app_request()
        return types.SimpleNamespace(status=200)

    @contextlib.contextmanager
    def expect_response(self, predicate, timeout=None):
        info = types.SimpleNamespace(value=None)
        self._pending = None
        yield info
        if self._pending is None or not predicate(self._pending):
            raise PlaywrightTimeout("expect_response timed out")
        info.value = self._pending


class SearchRetryTests(unittest.TestCase):
    def setUp(self):
        self.log = runner.RunLog("CO-0001", "dry_run")
        runner._ACTIVE_RUN_LOG = self.log

    def tearDown(self):
        runner._ACTIVE_RUN_LOG = None

    def test_a_working_reload_needs_no_retry(self):
        page = FakePage(reload_hangs=False, goto_hangs=False)
        result = runner._search_tenants(page, None, "acme.example")
        self.assertEqual((len(result.rows), result.total_count), (1, 1))
        self.assertEqual(page.calls, ["reload"])

    def test_a_hung_reload_is_retried_once_by_navigation_with_the_same_filter(self):
        page = FakePage(reload_hangs=True, goto_hangs=False)
        result = runner._search_tenants(page, None, "acme.example")
        self.assertIsNotNone(result)
        self.assertEqual(page.calls, ["reload", "blank", "goto"])
        self.assertEqual(page.sent_bodies[-1]["tableServerData"]["filters"],
                         {"and": [{"method": "search", "value": "acme.example"}]})
        self.assertIsNone(page.handler)  # route removed afterwards

    def test_a_late_reply_of_the_hung_document_never_counts_on_the_retry(self):
        page = FakePage(reload_hangs=False, goto_hangs=True)
        original_reload = page.reload

        def late(**kwargs):
            original_reload()  # the app's request goes out and is rewritten ...
            raise PlaywrightTimeout("Page.reload: Timeout 30000ms exceeded.")  # ... but the load never ends
        page.reload = late
        self.assertIsNone(runner._search_tenants(page, None, "acme.example"))
        self.assertEqual(page.calls, ["reload", "blank", "goto"])

    def test_two_failures_fail_closed_without_a_third_attempt(self):
        page = FakePage(reload_hangs=True, goto_hangs=True)
        self.assertIsNone(runner._search_tenants(page, None, "acme.example"))
        self.assertEqual(page.calls, ["reload", "blank", "goto"])
        self.assertIsNone(page.handler)

    def test_a_non_timeout_error_is_not_retried(self):
        page = FakePage(reload_hangs=False, goto_hangs=False)

        def broken(**kwargs):
            page.calls.append("reload")
            raise RuntimeError("Target closed")
        page.reload = broken
        self.assertIsNone(runner._search_tenants(page, None, "acme.example"))
        self.assertEqual(page.calls, ["reload"])


class DiagnosticsTitleTests(unittest.TestCase):
    def test_title_is_called_not_its_bound_method(self):
        import inspect
        self.assertIn("page.title()", inspect.getsource(runner._capture_search_diagnostics))


class _Count:
    def __init__(self, counts):
        self.counts = list(counts)

    def count(self):
        return self.counts.pop(0) if len(self.counts) > 1 else self.counts[0]

    def click(self):
        pass


class SearchToolbarWaitTests(unittest.TestCase):
    """2026-10-06: wait for the slowly rendered tenant toolbar instead of failing closed at once."""

    def page(self, textbox, button):
        return types.SimpleNamespace(get_by_role=lambda role, **kw: textbox if role == "textbox" else button)

    def test_a_late_toolbar_is_waited_for(self):
        textbox, button = _Count([0, 0, 0, 1]), _Count([0, 0, 1])
        with unittest.mock.patch.object(runner, "sleep", lambda s: None):
            self.assertIs(runner._open_search(self.page(textbox, button)), textbox)

    def test_a_toolbar_that_never_renders_fails_closed_after_the_wait(self):
        clock = iter(range(0, 1000, 10))
        with unittest.mock.patch.object(runner, "sleep", lambda s: None),                 unittest.mock.patch.object(runner, "monotonic", lambda: next(clock)):
            self.assertIsNone(runner._open_search(self.page(_Count([0]), _Count([0]))))

    def test_two_search_buttons_never_count(self):
        clock = iter(range(0, 1000, 10))
        with unittest.mock.patch.object(runner, "sleep", lambda s: None),                 unittest.mock.patch.object(runner, "monotonic", lambda: next(clock)):
            self.assertIsNone(runner._open_search(self.page(_Count([0]), _Count([2]))))


if __name__ == "__main__":
    unittest.main()
