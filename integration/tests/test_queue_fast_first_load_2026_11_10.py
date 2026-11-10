"""Owner report 2026-11-10: after a restart the Onboarding queue waited ~2 min for every open CO's Salesforce reads.

The queue now waits only QUEUE_MATCH_WAIT_SECONDS for the prefetched matches; a CO still being read shows "Checking…",
counts as blocked (never ready, never a clearance) and the page refreshes itself until every check is in.
"""
from __future__ import annotations

import unittest
from concurrent.futures import Future
from time import monotonic
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_views


def _finished(value):
    future = Future()
    future.set_result(value)
    return future


class MatchNowaitTests(unittest.TestCase):
    KEY = ("production_match_display", "CO-0901", "Credential Exposure", "New", "STAMP")

    def setUp(self):
        with dashboard._display_cache_lock:  # noqa: SLF001
            self.saved = dict(dashboard._display_cache)  # noqa: SLF001
            dashboard._display_cache.pop(self.KEY, None)  # noqa: SLF001
        self.addCleanup(self.restore)

    def restore(self):
        with dashboard._display_cache_lock:  # noqa: SLF001
            dashboard._display_cache.clear()  # noqa: SLF001
            dashboard._display_cache.update(self.saved)  # noqa: SLF001

    def call(self, wait=0.0):
        return dashboard._match_of_nowait("CO-0901", "Credential Exposure", "New", "STAMP", monotonic() + wait)  # noqa: SLF001

    def test_a_cold_co_is_checking_and_blocked(self):
        match, pending = self.call()
        self.assertTrue(pending)
        check = production_views.production_check(match)
        self.assertTrue(check["blocked"])
        self.assertEqual(check["code"], "not_checked")
        self.assertIn("Salesforce", check["reason"])

    def test_an_unfinished_read_is_still_checking(self):
        with dashboard._display_cache_lock:  # noqa: SLF001
            dashboard._display_cache[self.KEY] = (monotonic() + 300, None, Future())  # noqa: SLF001
        self.assertTrue(self.call(wait=0.2)[1])

    def test_a_finished_read_is_used(self):
        done = {"status": "not_checked", "reason": "route_not_supported"}
        with dashboard._display_cache_lock:  # noqa: SLF001
            dashboard._display_cache[self.KEY] = (monotonic() + 300, None, _finished(done))  # noqa: SLF001
        with mock.patch.object(dashboard, "production_match_display", return_value=done) as read:
            match, pending = self.call()
        self.assertFalse(pending)
        self.assertEqual(match["reason"], "route_not_supported")
        self.assertEqual(read.call_args.args[3], "STAMP")  # the same stamp as the page: no second, blocking read

    def test_an_expired_entry_is_not_used(self):
        with dashboard._display_cache_lock:  # noqa: SLF001
            dashboard._display_cache[self.KEY] = (monotonic() - 1, None, _finished({}))  # noqa: SLF001
        self.assertTrue(self.call()[1])


if __name__ == "__main__":
    unittest.main()
