"""Runner and dashboard wiring of the Leonardo tenant inventory (2026-10-03).

Fake pages and responses only: no browser, Leonardo, or Salesforce call.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration.onboarding import leonardo_inventory as inventory
from integration.tests.test_leonardo_inventory import capture, tenant_row
import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

API = runner.DEVELOPMENT_ORIGIN + runner.INVENTORY_API_PATH
_login_patch = None


def setUpModule():
    # Handlers are called directly here; the SSO login gate is tested in test_session_readiness.py.
    global _login_patch
    _login_patch = patch.object(dashboard, "login_required", return_value=False)
    _login_patch.start()


def tearDownModule():
    _login_patch.stop()


class _Request:
    def __init__(self, body, method="POST"):
        self.method, self.post_data = method, json.dumps(body)


class _Response:
    def __init__(self, request_body, body, *, status=200, url=API, content_type="application/json", method="POST"):
        self.request = _Request(request_body, method)
        self.status, self.url, self._body = status, url, body
        self.headers = {"content-type": content_type}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class _Info:
    value = None


class _Button:
    def __init__(self, page):
        self.page = page

    def click(self, timeout=None):
        self.page.advance()

    def count(self):
        return 1


class _NoMatch:
    def count(self):
        return 0


class _FakePage:
    """Serves captured (request, response) pages; reload -> page 1, Next -> the following page."""

    def __init__(self, responses, *, url=runner.TENANT_MANAGEMENT, next_button=True):
        self.responses, self.url, self.next_button = list(responses), url, next_button
        self.index, self.pending = -1, None

    def reload(self, wait_until=None):
        self.index = 0
        self.pending = self.responses[0]

    def advance(self):
        self.index += 1
        self.pending = self.responses[self.index]

    @contextmanager
    def expect_response(self, predicate, timeout=None):
        info = _Info()
        yield info
        if self.pending is None or not predicate(self.pending):
            raise TimeoutError("no matching response")
        info.value, self.pending = self.pending, None

    def get_by_role(self, role, name=None):
        return _Button(self) if self.next_button else _NoMatch()

    def locator(self, selector):
        return _NoMatch()


def _responses(count=5, size=2, **kwargs):
    return [_Response(request, body, **kwargs) for request, body in capture(count, size)]


class CaptureTests(unittest.TestCase):
    def test_only_the_exact_table_post_on_the_development_origin_counts(self):
        ok = runner._is_inventory_response
        self.assertTrue(ok(_Response({}, {})))
        self.assertFalse(ok(_Response({}, {}, method="GET")))
        self.assertFalse(ok(_Response({}, {}, url="https://evil.example" + runner.INVENTORY_API_PATH)))
        self.assertFalse(ok(_Response({}, {}, url=runner.DEVELOPMENT_ORIGIN + "/api/v1/backoffice/account/add")))
        self.assertFalse(ok(_Response({}, {}, url="https://app.pentera.io" + runner.INVENTORY_API_PATH)))

    def _capture(self, **kwargs):
        request, body = capture(2, 2)[0]
        page = _FakePage([_Response(request, body, **kwargs)])
        return runner._capture_table_page(page, page.reload)

    def test_statuses_fail_closed(self):
        self.assertIn("tableServerData", self._capture()[0])
        with self.assertRaises(runner.LeonardoSessionExpired):
            self._capture(status=401)
        with self.assertRaises(runner.LeonardoSessionExpired):
            self._capture(status=403)
        cases = {"inventory_waf_or_redirect": dict(status=403, content_type="text/html"),
                 "inventory_rate_limited": dict(status=429),
                 "inventory_server_error": dict(status=503),
                 "inventory_waf_or_redirect ": dict(status=200, content_type="text/html")}
        for code, kwargs in cases.items():
            with self.subTest(code=code), self.assertRaises(RuntimeError) as raised:
                self._capture(**kwargs)
            self.assertEqual(str(raised.exception), code.strip())

    def test_no_response_and_a_redirected_tab_fail_closed(self):
        page = _FakePage([])
        with self.assertRaises(RuntimeError) as raised:
            runner._capture_table_page(page, lambda: None)
        self.assertEqual(str(raised.exception), "inventory_no_signal")
        request, body = capture(2, 2)[0]
        page = _FakePage([_Response(request, body)], url=runner.DEVELOPMENT_LOGIN)
        with self.assertRaises(RuntimeError) as raised:
            runner._capture_table_page(page, page.reload)
        self.assertEqual(str(raised.exception), "inventory_waf_or_redirect")

    def test_filters(self):
        self.assertFalse(runner._filter_present({}))
        self.assertFalse(runner._filter_present(None))
        self.assertFalse(runner._filter_present({"and": []}))
        self.assertTrue(runner._filter_present({"and": [{"method": "search", "value": "x"}]}))
        self.assertTrue(runner._filter_present({"weird": 1}))
        self.assertEqual(runner._filter_methods({"and": [{"method": "search", "value": "secret"}]}), ["search"])

    def test_tenant_management_needs_the_development_origin(self):
        self.assertTrue(runner._is_tenant_management_url(runner.TENANT_MANAGEMENT + "?tab=1"))
        self.assertFalse(runner._is_tenant_management_url("https://evil.example/backoffice/tenantManagement"))
        # Leonardo's own spelling after a fresh SSO sign-in (2026-10-03).
        self.assertTrue(runner._is_tenant_management_url(runner.DEVELOPMENT_ORIGIN + "/backOffice/tenantManagement"))
        self.assertFalse(runner._is_tenant_management_url("https://evil.example/backOffice/tenantManagement"))


class CollectTests(unittest.TestCase):
    def test_pages_through_every_page(self):
        pages, size = runner._collect_inventory_pages(_FakePage(_responses(5, 2)))
        self.assertEqual((len(pages), size), (3, 2))
        assembled = inventory.assemble_pages(pages, page_size=size)
        self.assertEqual(assembled.total_count, 5)

    def test_a_filtered_first_page_is_refused(self):
        request, body = capture(2, 2)[0]
        request["tableServerData"]["filters"] = {"and": [{"method": "search", "value": "x"}]}
        with self.assertRaises(RuntimeError) as raised:
            runner._collect_inventory_pages(_FakePage([_Response(request, body)]))
        self.assertEqual(str(raised.exception), "inventory_filtered")

    def test_missing_pagination_and_too_large_fail_closed(self):
        with self.assertRaises(RuntimeError) as raised:
            runner._collect_inventory_pages(_FakePage(_responses(5, 2), next_button=False))
        self.assertEqual(str(raised.exception), "inventory_pagination_unavailable")
        request, body = capture(2, 2)[0]
        body["pagination_response"]["total_count"] = inventory.MAX_TOTAL + 1
        with self.assertRaises(RuntimeError) as raised:
            runner._collect_inventory_pages(_FakePage([_Response(request, body)]))
        self.assertEqual(str(raised.exception), "inventory_too_large")

    def setUp(self):
        patcher = patch.object(runner, "INVENTORY_PAGE_DELAY_SECONDS", 0)
        patcher.start(); self.addCleanup(patcher.stop)


@contextmanager
def _fake_playwright():
    yield object()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for target, value in ((runner, "INVENTORY_PAGE_DELAY_SECONDS"),):
            patcher = patch.object(target, value, 0)
            patcher.start(); self.addCleanup(patcher.stop)

    def _export(self, page, **kwargs):
        @contextmanager
        def attended(_playwright):
            yield page
        with patch("playwright.sync_api.sync_playwright", _fake_playwright), \
                patch.object(runner, "_attended_page", attended):
            return runner.run_export_tenants("dev", root=self.root, **kwargs)

    def test_export_writes_the_minimized_snapshot_and_prints_counts_only(self):
        summary = self._export(_FakePage(_responses(5, 2)), write_csv=True)
        self.assertEqual(summary["result"], "inventory_exported")
        self.assertEqual((summary["total_count"], summary["row_count"], summary["pages"]), (5, 5, 3))
        self.assertNotIn("Example Tenant", json.dumps(summary))
        written = (self.root / "dev" / summary["file"]).read_text(encoding="utf-8")
        for secret in ("pat.sample@example.com", "+1 555 0100", "FAKE-NOT-A-KEY", "/secret-path", "alt.example.com"):
            self.assertNotIn(secret, written)
        self.assertTrue((self.root / "dev" / summary["file"]).with_suffix(".csv").is_file())
        loaded = inventory.load_latest(self.root, "dev", max_age=timedelta(hours=1), now=datetime.now(timezone.utc))
        self.assertEqual(loaded["row_count"], 5)

    def test_production_is_refused_before_any_browser(self):
        with patch.object(runner, "_attended_page", side_effect=AssertionError("no browser")):
            self.assertEqual(runner.run_export_tenants("prod", root=self.root)["result"],
                             "production_inventory_not_approved")
        self.assertFalse((self.root / "prod").exists())

    def test_inconsistent_pages_retry_once_then_fail_with_nothing_written(self):
        bad = _responses(5, 2)
        bad[1].__dict__["_body"]["pagination_response"]["total_count"] = 6
        self.assertEqual(self._export(_FakePage(bad + bad))["result"], "inventory_inconsistent")
        self.assertFalse((self.root / "dev").exists())

    def test_session_expiry_writes_nothing(self):
        request, body = capture(2, 2)[0]
        summary = self._export(_FakePage([_Response(request, body, status=401)]))
        self.assertEqual(summary["result"], "leonardo_session_expired")
        self.assertFalse((self.root / "dev").exists())


class SweepTests(unittest.TestCase):
    def test_sweep_matches_by_id_and_uuid(self):
        rows = (tenant_row(1), tenant_row(2))
        readbacks = {"CO-0001": {"surface_account_id": rows[0]["id"], "account_uuid": rows[0]["accountUuid"]},
                     "CO-0002": {"surface_account_id": rows[1]["id"], "account_uuid": "f" * 32}}
        folder = Path(tempfile.mkdtemp())
        path = folder / "readbacks.json"
        path.write_text(json.dumps(readbacks), encoding="utf-8")
        recorded = []

        def inputs(reference):
            return ("route", "name", runner._readback_ids(reference), None, "", None)
        with patch.object(runner, "READBACK_PATH", path), \
                patch.object(runner, "_validation_inputs", side_effect=inputs), \
                patch.object(runner, "_record_validation",
                             side_effect=lambda ref, *a: recorded.append(ref) or "validation_recorded"), \
                patch.object(runner, "_record_check"):
            results = runner._sweep_from_inventory(rows)
        self.assertEqual(results, {"CO-0001": "validation_recorded", "CO-0002": "validation_tenant_not_found"})
        self.assertEqual(recorded, ["CO-0001"])


class SafetyTests(unittest.TestCase):
    def test_the_inventory_code_never_builds_its_own_requests_or_reads_cookies(self):
        source = "".join(inspect.getsource(fn) for fn in (
            runner._capture_table_page, runner._collect_inventory_pages, runner._api_session_check,
            runner.run_probe_inventory_shape, runner.run_export_tenants, runner._sweep_from_inventory))
        for forbidden in ("evaluate(", "cookies(", "storage_state", "page.request", "fetch(", "route("):
            self.assertNotIn(forbidden, source)

    def test_the_api_session_check_stays_off_until_the_probe_confirms_it(self):
        self.assertFalse(runner.SESSION_API_CHECK_ENABLED)


class DashboardTabTests(unittest.TestCase):
    """The dashboard opens as a tab of the automation window (2026-10-03); nothing else new may open."""

    def test_only_the_exact_loopback_sign_in_url_qualifies(self):
        self.assertEqual(runner._dashboard_port("http://127.0.0.1:8012/login"), 8012)
        for url in ("http://localhost:8012/login", "https://127.0.0.1:8012/login", "http://127.0.0.1:8012/",
                    "http://127.0.0.1:8012/login?next=x", "http://127.0.0.1:80/login", "http://127.0.0.1:99999/login",
                    "http://127.0.0.1:8012/attended/validate-all"):
            with self.subTest(url=url):
                self.assertIsNone(runner._dashboard_port(url))

    def test_the_tab_opener_accepts_the_sign_in_page_but_no_other_loopback_page(self):
        with patch.object(runner, "_cdp_json", return_value={"id": "T1"}) as cdp:
            self.assertEqual(runner._cdp_open_tab(1, "http://127.0.0.1:8012/login"), "T1")
            self.assertIsNone(runner._cdp_open_tab(1, "http://127.0.0.1:8012/attended/validate-all"))
        cdp.assert_called_once()

    def _open(self, targets, *, port=1234, persist=True):
        calls = []
        with patch.object(runner, "leonardo_profile", return_value=(Path(tempfile.mkdtemp()), persist)), \
                patch.object(runner, "_verified_automation_port", return_value=port), \
                patch.object(runner, "_launch_automation_chrome", side_effect=lambda *a, **k: calls.append("launch") or (None, 4321)), \
                patch.object(runner, "_chrome_executable", return_value="chrome.exe"), \
                patch.object(runner, "_cdp_page_targets", return_value=targets), \
                patch.object(runner, "_cdp_request", side_effect=lambda p, path, **k: calls.append(path) or b"{}"), \
                patch.object(runner, "_cdp_open_tab", side_effect=lambda p, url: calls.append(url) or "T9"):
            return runner.open_dashboard_tab(8012), calls

    def test_an_existing_dashboard_tab_is_brought_to_the_front(self):
        result, calls = self._open([{"id": "ABC", "url": "http://127.0.0.1:8012/connection"}])
        self.assertEqual((result, calls), ("dashboard_tab_activated", ["/json/activate/ABC"]))

    def test_a_new_tab_opens_in_the_running_window_or_a_launched_one(self):
        self.assertEqual(self._open([{"id": "X", "url": "about:blank"}]),
                         ("dashboard_tab_opened", ["http://127.0.0.1:8012/login"]))
        self.assertEqual(self._open([], port=None), ("dashboard_tab_opened", ["launch", "http://127.0.0.1:8012/login"]))

    def test_a_temporary_profile_is_refused(self):
        self.assertEqual(self._open([], persist=False)[0], "automation_profile_not_persisted")


class InventoryPageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = patch.object(dashboard, "inventory_root", return_value=self.root)
        patcher.start(); self.addCleanup(patcher.stop)

    def _write(self, captured_at):
        assembled = inventory.assemble_pages(capture(3, 2), page_size=2)
        inventory.write_snapshot(inventory.snapshot_payload(inventory.ENVIRONMENTS["dev"], assembled, captured_at), self.root)

    def test_no_snapshot_yet(self):
        page = dashboard.render_inventory()
        self.assertIn("No tenant inventory has been exported yet", page)
        self.assertIn("/attended/inventory-refresh", page)

    def test_table_links_cos_flags_orphans_and_marks_stale(self):
        self._write(datetime.now(timezone.utc) - timedelta(hours=7))
        row = tenant_row(1)
        readbacks = {"CO-0649": {"surface_account_id": row["id"], "account_uuid": row["accountUuid"]}}
        with patch.object(dashboard, "attended_leonardo_readbacks", return_value=readbacks):
            page = dashboard.render_inventory()
            filtered = dashboard.render_inventory("tenant 2")
        self.assertIn("Example Tenant 1", page)
        self.assertIn("href='/co/CO-0649'", page)
        self.assertIn("no CO", page)
        self.assertIn("stale", page)
        self.assertNotIn("pat.sample@example.com", page)
        self.assertNotIn("<script", page.lower())
        self.assertIn("Example Tenant 2", filtered)
        self.assertNotIn("Example Tenant 1", filtered)

    def _post_refresh(self, problem):
        class _FakeRequest:
            path = "/attended/inventory-refresh"
            def __init__(self):
                self.pages, self.redirects = [], []
            def send_page(self, status, page):
                self.pages.append(int(status))
            def send_redirect(self, location):
                self.redirects.append(location)
        request, launched = _FakeRequest(), []
        with patch.object(dashboard, "action_readiness_problem", return_value=problem), \
                patch.object(dashboard, "_start_runner_mode", side_effect=lambda *a: launched.append(a) or True):
            dashboard.Handler.do_POST(request)
        return request, launched

    def test_refresh_is_gated_and_launches_the_read_only_export(self):
        request, launched = self._post_refresh("leonardo_expired")
        self.assertEqual((request.pages, launched), ([409], []))
        request, launched = self._post_refresh(None)
        self.assertEqual(launched, [("--export-tenants", "--env", "dev", "--with-sweeps", "--csv")])
        self.assertEqual(request.redirects, ["/inventory?refresh=started"])


if __name__ == "__main__":
    unittest.main()
