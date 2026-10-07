"""CO page read speed (owner decision 2026-10-07): timing log, one DealHub query,
overlapped reads, queue-driven warming, 300 s cache.

No network, no `sf` CLI, no browser: sf_json / subprocess are patched.
"""

from __future__ import annotations

import contextvars
import inspect
import json
import re
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard

LATENCY = 0.2
ACCOUNT = "001000000000DEMO"
RENEWAL_ROW = {"Name": "CO-0767", "Account__c": ACCOUNT, "Onboarding_Product__c": "Surface & Credential Exposure",
               "Onboarding_Type__c": "Renewal of Existing Product", "Onboarding_Approval_Status__c": "Approved",
               "Onboarding_Stage__c": "Request Approved", "Surface_Account_ID__c": None, "Account_UUID__c": None}
DEALHUB = [{"Product_Full_Name__c": "Pentera Surface Prime - 1000 Subdomains", "DealHub_Status__c": "Pending",
            "DealHub_Subscription_Start_Date__c": "2026-10-27", "DealHub_Subscription_End_Date__c": "2029-10-26"},
           {"Product_Full_Name__c": "Pentera Core Plus Commercial - 500 End Points", "DealHub_Status__c": "Pending",
            "DealHub_Subscription_Start_Date__c": "2026-10-27", "DealHub_Subscription_End_Date__c": "2029-10-26"}]


def detail_record(row: dict) -> dict:
    return {field: row.get(field) for field in dashboard.DETAIL_FIELDS} | {"Name": row["Name"]}


class FakeSalesforce:
    """Stands in for sf_json: counts reads by SOQL object, optional latency, canned replies."""

    def __init__(self, row: dict, latency: float = 0.0) -> None:
        self.row, self.latency = row, latency
        self.queries: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, args, label=None):
        query = args[args.index("--query") + 1]
        with self.lock:
            self.queries.append(query)
        if self.latency:
            time.sleep(self.latency)
        if "FROM DealHub_Subscription__c" in query:
            return {"status": 0, "result": {"records": list(DEALHUB)}}
        if "FROM Customer_Onboarding__c" in query:
            return {"status": 0, "result": {"records": [detail_record(self.row)]}}
        raise AssertionError("unexpected read")

    def count(self, obj: str) -> int:
        return sum(1 for q in self.queries if q.split(" FROM ")[1].startswith(obj + " "))


class FakeHandler:
    """Just enough of Handler for _display_get on a /co/ path."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.pages: list[tuple[object, str]] = []

    def send_page(self, status, page):
        self.pages.append((status, page))


PAGE_PATCHES = (("load_runner_state", {}), ("attended_leonardo_readbacks", {}), ("manual_start_ack_nonce", None),
                ("load_check_state", {}), ("attended_scan_statuses", {}), ("attended_validations", {}))


def get_co_page(path: str = "/co/CO-0767") -> FakeHandler:
    handler = FakeHandler(path)
    contextvars.copy_context().run(dashboard.Handler._display_get, handler)  # type: ignore[arg-type]
    return handler


class ReadSpeedCase(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        dashboard.READ_TIMING_LOG.clear()


class CoPageReadTests(ReadSpeedCase):
    def _page(self, row, latency=0.0):
        fake = FakeSalesforce(row, latency)
        patches = [patch.object(dashboard, "sf_json", fake)] + [
            patch.object(dashboard, name, return_value=value) for name, value in PAGE_PATCHES]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        started = time.monotonic()
        handler = get_co_page("/co/" + row["Name"])
        return fake, handler, time.monotonic() - started

    def test_renewal_page_issues_exactly_one_dealhub_query(self):
        fake, handler, _ = self._page(RENEWAL_ROW)
        self.assertEqual(len(handler.pages), 1)
        self.assertIn("Renewal plan", handler.pages[0][1])
        self.assertEqual(fake.count("DealHub_Subscription__c"), 1)
        self.assertEqual(fake.count("Customer_Onboarding__c"), 1)
        query = next(q for q in fake.queries if "FROM DealHub_Subscription__c" in q)
        self.assertIn("DealHub_Account__c IN (SELECT Account__c FROM Customer_Onboarding__c WHERE Name = 'CO-0767')", query)
        self.assertIn("LIMIT 100", query)
        self.assertNotIn(ACCOUNT, query)

    def test_surface_create_page_issues_one_dealhub_query_and_commercial_check_uses_it(self):
        row = dict(RENEWAL_ROW, Onboarding_Type__c="New Product Onboarding", Onboarding_Approval_Status__c="Pending")
        fake, handler, _ = self._page(row)
        self.assertEqual(len(handler.pages), 1)
        self.assertEqual(fake.count("DealHub_Subscription__c"), 1)
        self.assertEqual(len(fake.queries), 2)

    def test_ce_only_page_needs_no_dealhub_query(self):
        row = dict(RENEWAL_ROW, Onboarding_Product__c="Credential Exposure", Onboarding_Type__c="New Product Onboarding")
        self.assertFalse(dashboard.dealhub_needed(row))
        # The cached queue row tells the handler the product, so it skips the DealHub prefetch.
        patcher = patch.object(dashboard, "peek_display_cache", return_value=[dict(row)])
        patcher.start()
        self.addCleanup(patcher.stop)
        fake, handler, _ = self._page(row)
        self.assertEqual(len(handler.pages), 1)
        self.assertEqual(fake.count("DealHub_Subscription__c"), 0)

    def test_co_row_and_dealhub_reads_overlap(self):
        fake, handler, elapsed = self._page(RENEWAL_ROW, latency=LATENCY)
        self.assertEqual(len(fake.queries), 2)
        self.assertLess(elapsed, 2 * LATENCY * 0.9)  # sequential would be at least 2 x latency

    def test_read_error_keeps_todays_unavailable_outcome(self):
        def failing(args, label=None):
            raise dashboard.ReadUnavailable()
        with patch.object(dashboard, "sf_json", failing):
            handler = get_co_page()
        self.assertEqual(handler.pages[0][0], dashboard.HTTPStatus.SERVICE_UNAVAILABLE)

    def test_missing_account_keeps_account_missing_and_never_trusts_the_semi_join(self):
        row = dict(RENEWAL_ROW, Account__c=None, Onboarding_Type__c="New Product Onboarding", Onboarding_Approval_Status__c="Pending")
        with patch.object(dashboard, "sf_json", FakeSalesforce(row)):
            self.assertEqual(dashboard.surface_commercial_readiness(dict(row))["reason"], "account_missing")
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.renewal_subscription_rows("", "CO-0767")

    def test_renewal_subscription_rows_signature_still_works(self):
        fake = FakeSalesforce(RENEWAL_ROW)
        with patch.object(dashboard, "sf_json", fake):
            self.assertEqual(len(dashboard.renewal_subscription_rows(ACCOUNT)), 2)
            self.assertIn(f"DealHub_Account__c = '{ACCOUNT}'", fake.queries[0])
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.dealhub_rows_for_co("CO-1'; DROP")


class CacheTests(ReadSpeedCase):
    def display(self, fn, *args):
        def run():
            dashboard._display_reads.set(True)
            return fn(*args)
        return contextvars.copy_context().run(run)

    def test_ttl_is_five_minutes(self):
        self.assertEqual(dashboard.DISPLAY_READ_TTL_SECONDS, 300.0)

    def test_display_reads_are_cached_and_refresh_clears(self):
        fake = FakeSalesforce(RENEWAL_ROW)
        with patch.object(dashboard, "sf_json", fake):
            self.display(dashboard.detail_row, "CO-0767")
            self.display(dashboard.detail_row, "CO-0767")
            self.assertEqual(len(fake.queries), 1)
            dashboard.clear_display_cache()
            self.display(dashboard.detail_row, "CO-0767")
            self.assertEqual(len(fake.queries), 2)

    def test_refresh_query_and_every_post_clear_the_cache(self):
        self.assertIn('"refresh"', inspect.getsource(dashboard.Handler.do_GET))
        self.assertIn("clear_display_cache()", inspect.getsource(dashboard.Handler.do_GET))
        post = inspect.getsource(dashboard.Handler.do_POST)
        self.assertLess(post.index("clear_display_cache()"), post.index("login_required()"))

    def test_write_and_confirmation_paths_never_read_through_the_cache(self):
        fake = FakeSalesforce(RENEWAL_ROW)
        with patch.object(dashboard, "sf_json", fake):
            self.display(dashboard.detail_row, "CO-0767")  # primes the cache
            self.assertEqual(len(fake.queries), 1)
            # Outside a display GET (POST handlers, the runner) every call is a fresh read.
            self.assertFalse(dashboard._display_reads.get())
            dashboard.detail_row("CO-0767")
            dashboard.detail_row("CO-0767")
            dashboard.dealhub_rows_for_co("CO-0767")
            dashboard.dealhub_rows_for_co("CO-0767")
        self.assertEqual(fake.count("Customer_Onboarding__c"), 3)
        self.assertEqual(fake.count("DealHub_Subscription__c"), 2)

    def test_cached_values_are_copies(self):
        with patch.object(dashboard, "sf_json", FakeSalesforce(RENEWAL_ROW)):
            first = self.display(dashboard.dealhub_rows_for_co, "CO-0767")
            first.clear()
            self.assertEqual(len(self.display(dashboard.dealhub_rows_for_co, "CO-0767")), 2)


class WarmFromQueueTests(ReadSpeedCase):
    @staticmethod
    def queue_row(number: int, approval="Approved", stage="Request Approved", product="Surface & Credential Exposure"):
        return {"Name": f"CO-{number:04d}", "Onboarding_Approval_Status__c": approval, "Onboarding_Stage__c": stage,
                "Onboarding_Product__c": product, "Onboarding_Type__c": "Renewal of Existing Product",
                "Account__r.Name": "Acme", "Submission_Date__c": "2026-10-01"}

    def in_display(self, fn, *args):
        def run():
            dashboard._display_reads.set(True)
            return fn(*args)
        return contextvars.copy_context().run(run)

    def test_bounded_most_relevant_first_and_reads_only(self):
        rows = [self.queue_row(n, "Pending", "New") for n in range(1, 11)] + [self.queue_row(n) for n in range(20, 30)]
        scheduled: list[tuple[str, str]] = []
        with patch.object(dashboard, "prefetch_display_read", lambda fn, *a: scheduled.append((fn.__name__, a[0]))):
            count = self.in_display(dashboard.warm_co_pages, rows, {})
        names = {fn for fn, _ in scheduled}
        self.assertLessEqual(len({ref for _, ref in scheduled}), dashboard.WARM_CO_LIMIT)
        self.assertEqual(dashboard.WARM_CO_LIMIT, 12)
        self.assertLessEqual(names, {"detail_row", "dealhub_rows_for_co"})
        self.assertEqual(count, len(scheduled))
        first = scheduled[0][1]
        self.assertEqual(dashboard.classify_queue_row(next(r for r in rows if r["Name"] == first), None)[0], "ready")

    def test_outside_a_display_get_nothing_is_scheduled(self):
        with patch.object(dashboard, "prefetch_display_read", side_effect=AssertionError("no prefetch")):
            self.assertEqual(dashboard.warm_co_pages([self.queue_row(1)], {}), 0)

    def test_ce_only_rows_skip_dealhub(self):
        rows = [self.queue_row(1, product="Credential Exposure") | {"Onboarding_Type__c": "New Product Onboarding"}]
        scheduled: list[str] = []
        with patch.object(dashboard, "prefetch_display_read", lambda fn, *a: scheduled.append(fn.__name__)):
            self.in_display(dashboard.warm_co_pages, rows, {})
        self.assertEqual(scheduled, ["detail_row"])

    def test_cached_reads_are_skipped(self):
        fake = FakeSalesforce(RENEWAL_ROW)
        with patch.object(dashboard, "sf_json", fake):
            self.in_display(dashboard.detail_row, "CO-0767")
            self.in_display(dashboard.dealhub_rows_for_co, "CO-0767")
            with patch.object(dashboard, "prefetch_display_read", side_effect=AssertionError("cached")):
                self.assertEqual(self.in_display(dashboard.warm_co_pages, [self.queue_row(767)], {}), 0)

    def test_scheduling_does_not_wait_and_uses_the_existing_pool_size(self):
        self.assertEqual(dashboard._display_prefetch_pool._max_workers, 4)
        fake = FakeSalesforce(RENEWAL_ROW, latency=0.5)
        pool = ThreadPoolExecutor(max_workers=4)
        rows = [self.queue_row(n) for n in range(1, 16)]
        try:
            with patch.object(dashboard, "sf_json", fake), patch.object(dashboard, "_display_prefetch_pool", pool):
                started = time.monotonic()
                scheduled = self.in_display(dashboard.warm_co_pages, rows, {})
                elapsed = time.monotonic() - started
                self.assertEqual(scheduled, 2 * dashboard.WARM_CO_LIMIT)
                self.assertLess(elapsed, 0.2)
                pool.shutdown(wait=True)  # let the background reads finish while sf_json is still patched
        finally:
            pool.shutdown(wait=True)
        self.assertEqual(len(fake.queries), 2 * dashboard.WARM_CO_LIMIT)
        self.assertTrue(all(q.startswith("SELECT") for q in fake.queries))

    def test_render_dashboard_schedules_warming_with_its_rows(self):
        rows = [self.queue_row(5)]
        with patch.object(dashboard, "queue_rows", return_value=rows), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "closed_history", return_value=None), \
                patch.object(dashboard, "queue_start_dates", return_value={}), \
                patch.object(dashboard, "load_check_state", return_value={}), \
                patch.object(dashboard, "page_queue", return_value="<html></html>"), \
                patch.object(dashboard, "warm_co_pages") as warm:
            self.assertEqual(dashboard.render_dashboard(), "<html></html>")
        warm.assert_called_once_with(rows, {})

    def test_warming_cannot_reach_browser_or_write_code(self):
        source = inspect.getsource(dashboard.warm_co_pages) + inspect.getsource(dashboard.dealhub_rows_for_co)
        code = re.sub(r'""".*?"""', "", source, flags=re.S).lower()
        for forbidden in ("leonardo", "sf_write_json", "playwright", "preflight", "subprocess", "launch"):
            self.assertNotIn(forbidden, code)


class TimingLogTests(ReadSpeedCase):
    def run_cli(self, reference="CO-0767"):
        def fake_run(command, **_kwargs):
            records = [detail_record(RENEWAL_ROW)] if "Customer_Onboarding__c" in " ".join(command) else []
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"status": 0, "result": {"records": records}}), stderr="")
        with patch.object(dashboard.subprocess, "run", fake_run), \
                patch.object(dashboard, "salesforce_cli_command", return_value="sf"), \
                patch.object(dashboard, "salesforce_target_org", return_value="org"):
            dashboard.detail_row(reference)
            dashboard.dealhub_rows_for_co(reference)

    def test_each_read_is_logged_with_object_label_duration_and_status_only(self):
        with self.assertLogs("surface_dashboard.timing", level="INFO") as logs:
            self.run_cli()
        text = "\n".join(logs.output)
        self.assertEqual(len(logs.records), 2)
        self.assertIn("Customer_Onboarding__c", text)
        self.assertIn("DealHub_Subscription__c", text)
        self.assertRegex(text, r"\d+ ms ok")
        for secret in ("CO-0767", ACCOUNT, "Name =", "SELECT", "Acme", "Pending", "Prime"):
            self.assertNotIn(secret, text)
        self.assertEqual([e["label"] for e in dashboard.READ_TIMING_LOG], ["Customer_Onboarding__c", "DealHub_Subscription__c"])
        for entry in dashboard.READ_TIMING_LOG:
            self.assertEqual(set(entry), {"kind", "label", "ms", "ok"})

    def test_failed_read_is_logged_as_fail(self):
        failing = subprocess.CompletedProcess(["sf"], 1, stdout="secret-output", stderr="")
        with patch.object(dashboard.subprocess, "run", return_value=failing), \
                patch.object(dashboard, "salesforce_cli_command", return_value="sf"), \
                patch.object(dashboard, "salesforce_target_org", return_value="org"), \
                self.assertLogs("surface_dashboard.timing", level="INFO") as logs:
            with self.assertRaises(dashboard.ReadUnavailable):
                dashboard.sf_json(["data", "query", "--query", "SELECT Id FROM Account WHERE Id = '001ABC'", "--json"])
        self.assertIn("Account", logs.output[0])
        self.assertIn("fail", logs.output[0])
        self.assertNotIn("001ABC", logs.output[0])
        self.assertNotIn("secret-output", logs.output[0])

    def test_labels(self):
        label = dashboard.read_label
        self.assertEqual(label(["data", "query", "--query", "SELECT A FROM Customer_Onboarding__c WHERE Name = 'x'", "--json"]),
                         "Customer_Onboarding__c")
        self.assertEqual(label(["api", "request", "rest", "/services/data/v67.0/sobjects/Customer_Onboarding__c/listviews/0ABC/results"]),
                         "rest:Customer_Onboarding__c")
        self.assertEqual(label(["org", "display", "--json"]), "org display")
        self.assertIsNotNone(re.fullmatch(r"[A-Za-z0-9_: -]+", label(["x" * 400])))

    def test_page_build_time_is_logged_for_queue_and_co_pages(self):
        fake = FakeSalesforce(RENEWAL_ROW)
        patches = [patch.object(dashboard, "sf_json", fake), patch.object(dashboard, "render_dashboard", return_value="<html></html>")] + [
            patch.object(dashboard, name, return_value=value) for name, value in PAGE_PATCHES]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.assertLogs("surface_dashboard.timing", level="INFO") as logs:
            get_co_page("/")
            get_co_page("/co/CO-0767")
        pages = [e["label"] for e in dashboard.READ_TIMING_LOG if e["kind"] == "page_build"]
        self.assertEqual(pages, ["queue", "co"])
        self.assertTrue(any("page_build co" in line for line in logs.output))
        self.assertNotIn("CO-0767", "\n".join(line for line in logs.output if "page_build" in line))


if __name__ == "__main__":
    unittest.main()
