"""Owner report 2026-10-30: moving between Sessions, Onboarding and a CO page was very slow again. The whole production match
of every CO was cached under one stamp (clone capture + newest create record + newest scan check), so each automatic scan
check, create or clone push re-read every open CO through the Salesforce CLI (~5 s per read; 52 s for the queue). The two
Salesforce reads of a match now have their own per-CO display cache; a new stamp only redoes the local clone comparison.
Hermetic: fake reads, a fake clone; nothing reads Salesforce.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_match as match

CO, PRODUCT, KIND = "CO-0806", "Credential Exposure", "New Product Onboarding"


class MatchReadCacheTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        token = dashboard._display_reads.set(True)
        self.addCleanup(dashboard._display_reads.reset, token)
        self.expectation = mock.Mock(return_value=("ce", {"source": 1}, {"plan": 1}))
        self.identity = mock.Mock(return_value={"uuid": "", "names": (), "domains": ()})
        self.full = mock.Mock(side_effect=lambda *a, **k: {"status": "not_in_production", "reason": ""})
        for patch in (mock.patch.object(runner, "_production_expectation", self.expectation),
                      mock.patch.object(runner, "_identity_for", self.identity),
                      mock.patch.object(runner, "clone_root", return_value=Path("unused")),
                      mock.patch.object(match, "load_snapshot", return_value={"rows": []}),
                      mock.patch.object(match, "production_match", self.full),
                      mock.patch.object(match, "identity_match", side_effect=lambda *a, **k: {"status": "not_checked",
                                                                                              "reason": "identity"}),
                      mock.patch.object(runner, "_snapshot_account_uuid", return_value=None),
                      mock.patch.object(runner, "_apply_verified_tenant", side_effect=lambda result, *_: result),
                      mock.patch("integration.onboarding.state_paths.vm_mode", return_value=False)):
            patch.start()
            self.addCleanup(patch.stop)

    def show(self, stamp, co=CO):
        return dashboard.production_match_display(co, PRODUCT, KIND, stamp)

    def test_a_new_stamp_recomputes_the_match_without_reading_salesforce_again(self):
        self.assertEqual(self.show("clone-1")["status"], "not_in_production")
        self.show("clone-2|new-scan-check")
        self.show("clone-3|new-create-record")
        self.assertEqual(self.expectation.call_count, 1)
        self.assertEqual(self.identity.call_count, 1)
        self.assertEqual(self.full.call_count, 3)  # the clone comparison itself is never replayed over a newer stamp

    def test_each_co_keeps_its_own_reads(self):
        self.show("s", "CO-0806")
        self.show("s", "CO-0831")
        self.assertEqual([c.args[0] for c in self.expectation.call_args_list], ["CO-0806", "CO-0831"])

    def test_a_refused_source_is_cached_and_still_falls_back_to_the_identity_check(self):
        self.expectation.side_effect = RuntimeError("ce_subscription_unavailable")
        self.assertEqual(self.show("a")["reason"], "identity")
        self.assertEqual(self.show("b")["reason"], "identity")
        self.assertEqual(self.expectation.call_count, 1)
        self.full.assert_not_called()

    def test_an_unexpected_error_is_not_cached(self):
        self.expectation.side_effect = ZeroDivisionError()
        for stamp in ("a", "b"):
            with self.assertRaises(ZeroDivisionError):
                self.show(stamp)
        self.assertEqual(self.expectation.call_count, 2)

    def test_a_write_to_the_co_clears_its_reads(self):
        self.show("a")
        dashboard.clear_display_cache_for(CO)
        self.show("a")
        self.assertEqual((self.expectation.call_count, self.identity.call_count), (2, 2))

    def test_the_hook_is_only_set_during_the_dashboards_match(self):
        self.show("a")
        self.assertIsNone(runner.MATCH_READS.get())
        runner.production_match_for(CO, PRODUCT, KIND)  # a direct call (runner, gate) reads Salesforce itself
        self.assertEqual(self.expectation.call_count, 2)

    def test_outside_a_display_page_nothing_is_cached(self):
        dashboard._display_reads.set(False)
        self.show("a")
        self.show("a")
        self.assertEqual(self.expectation.call_count, 2)


class TimingTests(unittest.TestCase):
    def test_the_co_page_build_is_timed(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertIn('record_timing("page_build", "onboard_co", started, True)', source)


if __name__ == "__main__":
    unittest.main()
