"""Dashboard clone auto-refresh (owner request 2026-10-20): the production clone (Redash query 253) is collected when the
dashboard starts and again whenever it is older than 45 minutes or unusable, so a restart (or the VPN coming back) clears
"Clone stale" without a manual command. Read-only, no browser, no login, no secret, reason codes only. Hermetic: fake clock and
fake collector; nothing here touches Redash.
"""

import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from tools import clone_autorefresh as auto

NOW = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
GOOD = {"result": "inventory_collected", "refreshed": True, "refresh_failed": False}


class Failure(Exception):
    def __init__(self, reason):
        super().__init__("secret-looking detail must never be kept")
        self.reason = reason


class Harness:
    def __init__(self, age=None, outcome=GOOD):
        self.age, self.outcome, self.calls = age, outcome, 0
        auto.reset()

    def collect(self):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return dict(self.outcome)

    def tick(self, now=NOW, force=False):
        return auto.tick(now, age_seconds=lambda: self.age, collect_once=self.collect, force=force)


class TickTests(unittest.TestCase):
    def test_startup_collects_even_when_the_clone_looks_fresh(self):
        h = Harness(age=600)
        self.assertEqual(h.tick(force=True), "refreshed")
        self.assertEqual(h.calls, 1)

    def test_a_fresh_clone_is_left_alone(self):
        h = Harness(age=44 * 60)
        self.assertEqual(h.tick(), "skipped_fresh")
        self.assertEqual(h.calls, 0)

    def test_an_old_or_unusable_clone_is_collected(self):
        for age in (46 * 60, 18 * 3600, None):
            with self.subTest(age=age):
                h = Harness(age=age)
                self.assertEqual(h.tick(), "refreshed")
                self.assertEqual(h.calls, 1)

    def test_failure_is_recorded_not_raised_and_retried_only_after_five_minutes(self):
        h = Harness(age=None, outcome=Failure("redash_unreachable"))
        self.assertEqual(h.tick(NOW), "failed")
        self.assertEqual(auto.status()["state"], "failed")
        self.assertEqual(auto.status()["reason"], "redash_unreachable")
        self.assertEqual(h.tick(NOW + timedelta(minutes=4, seconds=59)), "skipped_backoff")
        self.assertEqual(h.calls, 1)
        h.outcome = GOOD
        self.assertEqual(h.tick(NOW + timedelta(minutes=5)), "refreshed")
        self.assertEqual((auto.status()["state"], auto.status()["reason"]), ("ok", ""))

    def test_the_startup_attempt_also_respects_the_backoff_after_a_failure(self):
        h = Harness(age=None, outcome=Failure("redash_unreachable"))
        h.tick(NOW)
        self.assertEqual(h.tick(NOW + timedelta(minutes=1), force=True), "skipped_backoff")
        self.assertEqual(h.calls, 1)

    def test_a_written_snapshot_whose_redash_refresh_did_not_happen_counts_as_failed(self):
        h = Harness(age=None, outcome={"result": "inventory_collected", "refresh_failed": True,
                                       "refresh_reason": "redash_refresh_not_permitted"})
        self.assertEqual(h.tick(), "failed")
        self.assertEqual(auto.status()["reason"], "redash_refresh_not_permitted")
        h = Harness(age=None, outcome={"result": "inventory_collected", "refresh_failed": True})
        h.tick()
        self.assertEqual(auto.status()["reason"], "redash_refresh_result_still_old")

    def test_unchanged_inventory_is_a_success(self):
        h = Harness(age=None, outcome={"result": "inventory_unchanged", "refresh_failed": False})
        self.assertEqual(h.tick(), "refreshed")
        self.assertEqual(auto.status()["state"], "ok")

    def test_an_unexpected_error_keeps_no_message_and_a_bad_reason_is_coerced(self):
        h = Harness(age=None, outcome=RuntimeError("Bearer abc123 leaked"))
        self.assertEqual(h.tick(), "failed")
        self.assertEqual(auto.status()["reason"], "redash_collector_unavailable")
        self.assertNotIn("abc123", repr(auto.status()))
        h = Harness(age=None, outcome=Failure("Bad Reason: has spaces and a key abc123"))
        h.tick()
        self.assertEqual(auto.status()["reason"], "redash_collector_unavailable")

    def test_a_second_tick_while_one_runs_does_nothing(self):
        started, release, results = Event(), Event(), []

        def slow():
            started.set()
            release.wait(5)
            return dict(GOOD)

        worker = Thread(target=lambda: results.append(auto.tick(NOW, age_seconds=lambda: None, collect_once=slow)))
        auto.reset()
        worker.start()
        self.assertTrue(started.wait(5))
        calls = []
        second = auto.tick(NOW, age_seconds=lambda: None, collect_once=lambda: calls.append(1) or dict(GOOD))
        release.set()
        worker.join(5)
        self.assertEqual((second, calls, results), ("busy", [], ["refreshed"]))

    def test_an_age_read_that_raises_still_collects(self):
        auto.reset()

        def broken():
            raise OSError("unreadable")

        calls = []
        self.assertEqual(auto.tick(NOW, age_seconds=broken, collect_once=lambda: calls.append(1) or dict(GOOD)), "refreshed")
        self.assertEqual(calls, [1])


class LoopTests(unittest.TestCase):
    def test_the_loop_forces_the_first_pass_then_ticks_and_stops(self):
        auto.reset()
        stop, seen = Event(), []

        def collect():
            seen.append("c")
            stop.set()  # one pass is enough
            return dict(GOOD)

        auto.run_loop(stop, clock=lambda: NOW, age_seconds=lambda: 60, collect_once=collect, wait_seconds=0)
        self.assertEqual(seen, ["c"])  # age 60 s is fresh, so only the forced startup pass collected

    def test_the_loop_never_dies_on_a_failing_pass(self):
        auto.reset()
        stop, passes = Event(), []

        def clock():
            passes.append(1)
            if len(passes) > 3:
                stop.set()
            raise RuntimeError("clock broke")

        auto.run_loop(stop, clock=clock, age_seconds=lambda: None, collect_once=lambda: dict(GOOD), wait_seconds=0)
        self.assertGreaterEqual(len(passes), 3)


class DescribeAndSwitchTests(unittest.TestCase):
    def test_plain_words_never_a_value(self):
        auto.reset()
        self.assertEqual(auto.describe(), "")
        Harness(age=None, outcome=Failure("redash_unreachable")).tick()
        text = auto.describe()
        self.assertIn("VPN", text)
        self.assertIn("redash_unreachable", text)
        self.assertIn("5 min", text)
        Harness(age=None, outcome=Failure("redash_key_unavailable")).tick(NOW + timedelta(minutes=9))
        self.assertIn("--store-key --query-id 253", auto.describe())
        Harness(age=None).tick(NOW + timedelta(minutes=20))
        self.assertIn("refreshed", auto.describe().lower())

    def test_enabled_only_on_the_desktop_and_unless_switched_off(self):
        self.assertTrue(auto.enabled(environ={}, vm=False))
        self.assertFalse(auto.enabled(environ={}, vm=True))
        self.assertFalse(auto.enabled(environ={auto.ENV_SWITCH: "0"}, vm=False))
        self.assertTrue(auto.enabled(environ={auto.ENV_SWITCH: "1"}, vm=False))

    def test_the_default_collect_reads_query_253_with_a_refresh_and_never_accepts_a_shrink(self):
        import tools.redash_inventory_collector as collector

        with mock.patch.object(collector, "load_key", return_value="k" * 24) as load, \
                mock.patch.object(collector, "build_opener", return_value="opener"), \
                mock.patch.object(collector, "collect", return_value=dict(GOOD)) as collect:
            self.assertEqual(auto.default_collect_once(), GOOD)
        load.assert_called_once_with(query_id=253)
        kwargs = collect.call_args.kwargs
        self.assertEqual((kwargs["query_id"], kwargs.get("refresh", True), kwargs.get("accept_shrink", False)), (253, True, False))
        self.assertEqual(collect.call_args.args[:2], ("opener", "k" * 24))


class DashboardWiringTests(unittest.TestCase):
    def setUp(self):
        auto.reset()

    def test_the_strip_shows_the_refresh_state_when_the_clone_is_not_fresh(self):
        stale = {"state": "stale", "captured_at": "x", "age_seconds": 18 * 3600, "data_age_seconds": 3600,
                 "limit_seconds": 21600, "reason": "query_stale", "label": ""}
        Harness(age=None, outcome=Failure("redash_unreachable")).tick()
        with mock.patch.object(dashboard, "_clone_status", return_value=stale):
            page = dashboard._clone_strip()
        self.assertIn("redash_unreachable", page)
        self.assertIn("VPN", page)

    def test_a_fresh_clone_strip_is_unchanged(self):
        fresh = {"state": "fresh", "captured_at": "x", "age_seconds": 600, "data_age_seconds": 600,
                 "limit_seconds": 21600, "reason": "", "label": ""}
        Harness(age=None, outcome=Failure("redash_unreachable")).tick()
        with mock.patch.object(dashboard, "_clone_status", return_value=fresh):
            self.assertNotIn("redash_unreachable", dashboard._clone_strip())

    def test_startup_starts_the_job_only_on_the_desktop(self):
        source = (ROOT / "tools" / "serve_attended_open_onboardings_dashboard.py").read_text(encoding="utf-8")
        main = source[source.index('if __name__ == "__main__":'):]
        self.assertRegex(main, r"if not vm_mode\(\):\n(?:.*\n)*?\s+if clone_autorefresh\.enabled\(vm=vm_mode\(\)\):")
        self.assertIn("clone_autorefresh.run_loop", main)

    def test_the_module_cannot_print_log_or_keep_a_message(self):
        source = (ROOT / "tools" / "clone_autorefresh.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"\bprint\(|logging|\.args\b|str\(error\)|repr\(error\)|traceback")
        self.assertNotIn("accept_shrink=True", source)
        self.assertNotRegex(source, r"PRODUCTION_(RUNNER|DRY_RUN)_SUPPORTED")


class RegistrationDefaultTests(unittest.TestCase):
    def test_the_hourly_task_defaults_to_the_production_query(self):
        text = (ROOT / "tools" / "register_redash_inventory_task.ps1").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"\[int\]\$QueryId = 253", text))


if __name__ == "__main__":
    unittest.main()
