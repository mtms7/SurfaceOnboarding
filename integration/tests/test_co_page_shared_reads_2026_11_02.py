"""Owner report 2026-11-02: a CO page took ~21 s (log: page_build onboard_co 20.9 s / 21.4 s). A traced cold build of
CO-0831 made 9 Salesforce reads; the summary's values (production_review_inputs_display) and the match
(production_expectation_display) made the same CO + DealHub reads twice under two caches, and the match's own CO read
(identity) only started after them. Now the match reuses the summary's cached read and the CO page starts the identity
read together with the others (desktop only). Hermetic: fake reads; nothing reads Salesforce.
"""

from __future__ import annotations

import contextvars
import unittest
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

CO, PRODUCT, KIND = "CO-0831", "Credential Exposure", "New Product Onboarding"
INPUTS = {"engine": runner.CE_ENGINE, "co_source": {"tenant_names": ("MetroBank",), "domains": ("bank.example",)},
          "plan": {"license_type": "x"}}


def in_display(fn, *args):
    def run():
        dashboard._display_reads.set(True)
        return fn(*args)
    return contextvars.copy_context().run(run)


class SharedReadTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        self.read = mock.Mock(return_value=INPUTS)
        patch = mock.patch.object(dashboard, "production_review_inputs", self.read)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_summary_and_the_match_share_one_read(self):
        in_display(dashboard.production_review_inputs_display, CO, PRODUCT, KIND)
        state, value = in_display(dashboard.production_expectation_display, CO, PRODUCT, KIND)
        self.assertEqual(state, "ok")
        self.assertEqual(value, (INPUTS["engine"], INPUTS["co_source"], INPUTS["plan"]))
        self.assertEqual(self.read.call_count, 1)

    def test_the_match_first_then_the_summary_is_also_one_read(self):
        in_display(dashboard.production_expectation_display, CO, PRODUCT, KIND)
        in_display(dashboard.production_review_inputs_display, CO, PRODUCT, KIND)
        self.assertEqual(self.read.call_count, 1)

    def test_empty_and_none_route_values_are_one_key(self):
        in_display(dashboard.production_expectation_display, CO, "", "")
        in_display(dashboard.production_review_inputs_display, CO, None, None)
        self.assertEqual(self.read.call_count, 1)

    def test_a_refused_source_is_still_an_error_for_the_match(self):
        self.read.side_effect = RuntimeError("ce_subscription_unavailable")
        self.assertEqual(in_display(dashboard.production_expectation_display, CO, PRODUCT, KIND),
                         ("error", "ce_subscription_unavailable"))

    def test_outside_a_display_get_every_call_reads_fresh(self):
        dashboard.production_review_inputs_display(CO, PRODUCT, KIND)  # the approval POST path
        dashboard.production_expectation_display(CO, PRODUCT, KIND)
        self.assertEqual(self.read.call_count, 2)


class CoPagePrefetchTests(unittest.TestCase):
    def schedule(self, vm: bool) -> list[str]:
        scheduled: list[str] = []
        queued = [{"Name": CO, "Onboarding_Product__c": PRODUCT, "Onboarding_Type__c": KIND}]
        with mock.patch.object(dashboard, "peek_display_cache", return_value=queued), \
                mock.patch.object(dashboard, "route_for", return_value=dashboard.CE_ENGINE), \
                mock.patch.object(dashboard, "_match_stamp", return_value="s"), \
                mock.patch.object(dashboard.state_paths, "vm_mode", return_value=vm), \
                mock.patch.object(dashboard, "prefetch_display_read", lambda fn, *a: scheduled.append(fn.__name__)):
            dashboard.prefetch_onboard_page(CO)
        return scheduled

    def test_the_desktop_starts_the_identity_read_before_the_match(self):
        scheduled = self.schedule(vm=False)
        self.assertIn("production_identity_display", scheduled)
        self.assertLess(scheduled.index("production_identity_display"), scheduled.index("production_match_display"))
        self.assertLess(scheduled.index("production_review_inputs_display"), scheduled.index("production_match_display"))

    def test_the_vm_never_starts_a_salesforce_identity_read(self):
        self.assertNotIn("production_identity_display", self.schedule(vm=True))


if __name__ == "__main__":
    unittest.main()
