"""Owner report 2026-11-02 (CO-0757, Case 3): the CO page and the page after the click took ~20-25 s, and the readiness list
said "Create ready" while the run then refused with case3_alternate_domains_not_supported (nothing created).

A traced Case 3 page read the same CO + DealHub source three times (preflight, values card, match) and the start date last;
after a click the route was unknown until the CO row arrived. Now one cached source read per CO serves all three, the page
starts its reads together (with the route remembered from an earlier page), and the readiness list applies the run's own
Case 3 domain rule. Hermetic: fake reads; nothing reads Salesforce.
"""

from __future__ import annotations

import contextvars
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_views as views

CO = "CO-0757"
CASE3_ROW = {"Name": CO, "Onboarding_Product__c": runner.CASE3_ROUTE_PRODUCT, "Onboarding_Type__c": runner.CASE3_ROUTE_TYPE}


def in_display(fn, *args):
    def run():
        dashboard._display_reads.set(True)
        return fn(*args)
    return contextvars.copy_context().run(run)


def checklist(**overrides):
    args = dict(vm=False, locked=False, lock_reason=None, clone=None, match=None, domain_count=None, uuid_state="empty",
                ce_route=False, start_iso=None, today=date(2026, 11, 2), days_before=2, approval=None, create=None)
    args.update(overrides)
    return {item["key"]: item for item in views.onboard_checklist(**args)}["data"]


class DomainRuleTests(unittest.TestCase):
    def test_the_rule_matches_the_run(self):
        # Since the same day's owner go, alternate domains pass (they are typed); subdomains are still refused.
        self.assertIsNone(runner.case3_domain_problem(SimpleNamespace(alternate_domains=("a.example",), subdomains=())))
        self.assertEqual(runner.case3_domain_problem(SimpleNamespace(alternate_domains=(), subdomains=("x.a.example",))),
                         "case3_subdomains_not_supported")
        self.assertIsNone(runner.case3_domain_problem(SimpleNamespace(alternate_domains=(), subdomains=())))

    def test_the_run_still_refuses_with_the_same_code(self):
        source = SimpleNamespace(alternate_domains=(), subdomains=("x.a.example",))
        with self.assertRaises(runner.production_dry_run.MappingError) as raised:
            runner._pdr_plan(runner.CASE3_ENGINE, source, date(2026, 11, 2))
        self.assertEqual(str(raised.exception), "case3_subdomains_not_supported")

    def test_the_readiness_row_is_blocked_with_a_plain_reason(self):
        item = checklist(data_problem="case3_subdomains_not_supported")
        self.assertEqual(item["status"], "blocked")
        self.assertIn("subdomains", item["detail"])

    def test_an_unreadable_source_is_unavailable_never_ready(self):
        self.assertEqual(checklist(data_problem=views.DATA_SOURCE_UNREADABLE)["status"], "unavailable")

    def test_a_set_uuid_still_wins_and_no_problem_is_ready(self):
        self.assertEqual(checklist(uuid_state="set", data_problem="case3_subdomains_not_supported")["detail"],
                         "Salesforce already holds an Account UUID for this CO.")
        self.assertEqual(checklist()["status"], "ready")


class SharedSourceTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        self.read = mock.Mock(return_value=SimpleNamespace(alternate_domains=(), subdomains=()))
        patch = mock.patch.object(runner, "read_case3_fill_source", self.read)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_dashboard_hooks_the_runner_source(self):
        self.assertIs(runner.CASE3_SOURCE_READ, dashboard.case3_source_display)

    def test_one_read_serves_every_caller_of_a_page(self):
        for _ in range(3):  # preflight, values card, match: each calls case3_fill_source
            in_display(runner.case3_fill_source, CO)
        self.assertEqual(self.read.call_count, 1)

    def test_outside_a_page_build_every_call_reads_fresh(self):
        runner.case3_fill_source(CO)  # the create and the approval POST
        runner.case3_fill_source(CO)
        self.assertEqual(self.read.call_count, 2)

    def test_a_run_day_bypasses_the_hook(self):
        in_display(runner.case3_fill_source, CO, date(2026, 11, 2))
        self.read.assert_called_once_with(CO, date(2026, 11, 2))

    def test_without_the_hook_the_runner_reads_directly(self):
        with mock.patch.object(runner, "CASE3_SOURCE_READ", None):
            runner.case3_fill_source(CO)
        self.read.assert_called_once_with(CO, None)


class PrefetchTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        dashboard._ROUTE_HINTS.pop(CO, None)
        self.addCleanup(dashboard._ROUTE_HINTS.pop, CO, None)

    def schedule(self, queue_rows):
        scheduled: list[str] = []
        with mock.patch.object(dashboard, "peek_display_cache", side_effect=lambda name, *a: queue_rows
                               if name == "queue_source_rows" else None), \
                mock.patch.object(dashboard, "_match_stamp", return_value="s"), \
                mock.patch.object(dashboard.state_paths, "vm_mode", return_value=False), \
                mock.patch.object(dashboard, "prefetch_display_read", lambda fn, *a: scheduled.append(fn.__name__)):
            started = dashboard.prefetch_onboard_page(CO)
        return started, scheduled

    def test_salesforce_reads_start_before_the_reads_built_on_them(self):
        started, scheduled = self.schedule([CASE3_ROW])
        self.assertTrue(started)
        first_derived = min(scheduled.index(name) for name in ("evaluate_surface_fill_preflight", "case3_values_display",
                                                               "production_review_inputs_display", "production_match_display"))
        for name in ("detail_row", "case3_source_display", "production_identity_display", "queue_start_dates"):
            self.assertLess(scheduled.index(name), first_derived, name)

    def test_after_a_click_cleared_the_queue_the_remembered_route_starts_everything(self):
        self.schedule([CASE3_ROW])  # an earlier page
        started, scheduled = self.schedule(None)  # the click cleared the queue lists
        self.assertTrue(started)
        self.assertIn("case3_source_display", scheduled)

    def test_an_unknown_co_starts_only_its_row(self):
        started, scheduled = self.schedule(None)
        self.assertFalse(started)
        self.assertEqual(scheduled, ["detail_row"])

    def test_the_start_date_comes_from_the_queue_batch_when_cached(self):
        with mock.patch.object(dashboard, "subscription_summaries",
                               return_value={CO: {"Subscription_Start": "2026-11-03"}, "CO-0001": {"Subscription_Start": "x"}}):
            in_display(dashboard.queue_start_dates, (CO, "CO-0001"))
        with mock.patch.object(dashboard, "queue_start_dates", side_effect=AssertionError("no new read")):
            self.assertEqual(dashboard._prod_view_start(CO), "2026-11-03")


if __name__ == "__main__":
    unittest.main()
