"""Owner decision 2026-10-29: the desktop production window stays signed in for up to 8 hours so the next run reuses the
BackOffice sign-in; it closes at dashboard start, at sign-out and once the 8 hours have passed, never under a running run. Also
the CO summary of a CE-only CO with only a Pending Core Plus term shows its values and says the create needs the early
activation box. Hermetic: temporary folders, fakes; no browser is opened or closed.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

NOW = datetime(2026, 10, 30, 2, 15, tzinfo=timezone.utc)


class WindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name) / "production-profile"
        self.profile.mkdir()
        self.closed = []
        patch = mock.patch.object(runner, "_close_automation_browser_at",
                                  side_effect=lambda path: self.closed.append(path) or "automation_browser_closed")
        patch.start()
        self.addCleanup(patch.stop)

    def tab(self, reused, persist=True):
        return SimpleNamespace(persist=persist, reused=reused, profile_dir=self.profile)

    def test_a_window_the_run_launched_is_kept_for_8_hours(self):
        runner._finish_production_window(self.tab(reused=False), NOW)
        self.assertEqual(self.closed, [])
        self.assertEqual(runner.production_window_deadline(self.profile), NOW + timedelta(hours=8))
        self.assertFalse((self.profile / (self.profile.name + runner.PRODUCTION_KEEP_SUFFIX)).exists())  # never inside

    def test_a_reuse_keeps_the_deadline_and_never_extends_it(self):
        runner._finish_production_window(self.tab(reused=False), NOW)
        runner._finish_production_window(self.tab(reused=True), NOW + timedelta(hours=3))
        self.assertEqual(self.closed, [])
        self.assertEqual(runner.production_window_deadline(self.profile), NOW + timedelta(hours=8))

    def test_past_the_deadline_or_without_one_the_window_closes(self):
        runner._finish_production_window(self.tab(reused=False), NOW)
        runner._finish_production_window(self.tab(reused=True), NOW + timedelta(hours=8))
        self.assertEqual(self.closed, [self.profile])
        self.assertIsNone(runner.production_window_deadline(self.profile))
        runner._finish_production_window(self.tab(reused=True), NOW)  # a running window nobody kept
        self.assertEqual(len(self.closed), 2)

    def test_an_unreadable_or_non_utc_deadline_closes(self):
        path = self.profile.with_name(self.profile.name + runner.PRODUCTION_KEEP_SUFFIX)
        for text in ("tomorrow", "2026-10-30T10:00:00", "2026-10-30T10:00:00+02:00"):
            path.write_text(text, encoding="utf-8")
            self.assertIsNone(runner.production_window_deadline(self.profile), text)
        runner._finish_production_window(self.tab(reused=True), NOW)
        self.assertEqual(self.closed, [self.profile])

    def test_a_temporary_profile_is_never_kept(self):
        runner._finish_production_window(self.tab(reused=False, persist=False), NOW)
        self.assertIsNone(runner.production_window_deadline(self.profile))
        self.assertEqual(self.closed, [])

    def test_close_production_window_modes(self):
        with mock.patch.object(runner, "leonardo_profile", return_value=(self.profile, True)):
            runner._finish_production_window(self.tab(reused=False), NOW)
            self.assertEqual(runner.close_production_window(only_if_expired=True, now=NOW + timedelta(hours=1)),
                             "production_window_kept")
            self.assertEqual(runner.close_production_window(expire=True, now=NOW + timedelta(hours=1)),
                             "production_window_expired")
            self.assertEqual(self.closed, [])  # expire only moves the deadline; the run closes it when it ends
            self.assertEqual(runner.close_production_window(only_if_expired=True, now=NOW + timedelta(hours=1)),
                             "automation_browser_closed")
            self.assertEqual(runner.close_production_window(now=NOW), "automation_browser_closed")

    def test_the_production_runs_finish_through_the_keep_rule(self):
        source = Path(runner.__file__).read_text(encoding="utf-8")
        # 2026-10-30: the third is the kept-window opener of the automatic scan check (_kept_browser_page).
        self.assertEqual(source.count("_finish_production_window(tab)"), 3)
        self.assertNotIn("if tab.persist:  # end the loopback CDP exposure", source)


class DashboardWindowTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for patch in (mock.patch.object(dashboard, "vm_mode", return_value=False),
                      mock.patch.object(dashboard.attended_runner, "close_production_window",
                                        side_effect=lambda **kw: self.calls.append(kw) or "ok")):
            patch.start()
            self.addCleanup(patch.stop)

    def active(self, value):
        patch = mock.patch.object(dashboard, "_production_run_active", return_value=value)
        patch.start()
        self.addCleanup(patch.stop)

    def test_start_and_sign_out_close_when_no_run_uses_the_window(self):
        self.active(False)
        dashboard.close_kept_production_window("start")
        dashboard.close_kept_production_window("sign_out")
        self.assertEqual(self.calls, [{"only_if_expired": False, "reason": "dashboard_start"},
                                      {"only_if_expired": False, "reason": "dashboard_sign_out"}])

    def test_expiry_only_closes_an_expired_window(self):
        self.active(False)
        dashboard.close_kept_production_window("expiry")
        self.assertEqual(self.calls, [{"only_if_expired": True, "reason": "dashboard_expiry"}])

    def test_a_running_run_is_never_interrupted(self):
        self.active(True)
        self.assertEqual(dashboard.close_kept_production_window("expiry"), "production_window_in_use")
        dashboard.close_kept_production_window("sign_out")
        self.assertEqual(self.calls, [{"expire": True, "reason": "dashboard_sign_out"}])

    def test_the_vm_does_nothing_and_errors_are_reported(self):
        with mock.patch.object(dashboard, "vm_mode", return_value=True):
            self.assertEqual(dashboard.close_kept_production_window("start"), "vm_read_only")
        self.active(False)
        with mock.patch.object(dashboard.attended_runner, "close_production_window", side_effect=RuntimeError("x")):
            self.assertEqual(dashboard.close_kept_production_window("start"), "production_window_close_failed")

    def test_a_running_scan_check_counts_as_a_run(self):
        with mock.patch.object(dashboard.create_ui.LAUNCHES, "busy", return_value=False), \
                mock.patch.dict(dashboard.PRODUCTION_SCAN_RUNS, {"CO-0801": {"running": True}}, clear=True):
            self.assertTrue(dashboard._production_run_active())

    def test_the_loop_checks_expiry_until_stopped(self):
        stop = mock.Mock()
        stop.wait.side_effect = [False, True]
        self.active(False)
        dashboard._production_window_loop(stop)
        self.assertEqual(self.calls, [{"only_if_expired": True, "reason": "dashboard_expiry"}])


class PerCoClearTests(unittest.TestCase):
    """Owner request 2026-10-29: an "Onboard in production" click made every page cold; it now clears only its CO."""

    def handler(self, body, pending=True):
        data = body.encode("utf-8")
        return SimpleNamespace(headers={"Content-Length": str(len(data))}, rfile=__import__("io").BytesIO(data),
                               display_clear_pending=pending)

    def test_a_production_post_clears_only_its_co(self):
        with mock.patch.object(dashboard, "clear_display_cache_for") as one, \
                mock.patch.object(dashboard, "clear_display_cache") as everything:
            form = dashboard.post_form(self.handler("reference=CO-0801&nonce=x"))
        self.assertEqual(form["reference"], ["CO-0801"])
        one.assert_called_once_with("CO-0801")
        everything.assert_not_called()

    def test_a_form_without_a_valid_co_clears_everything(self):
        for body in ("nonce=x", "reference=../x", "reference=CO-1&reference=CO-2"):
            with mock.patch.object(dashboard, "clear_display_cache_for") as one, \
                    mock.patch.object(dashboard, "clear_display_cache") as everything:
                dashboard.post_form(self.handler(body))
            one.assert_not_called()
            everything.assert_called_once()

    def test_other_posts_are_unchanged(self):
        with mock.patch.object(dashboard, "clear_display_cache_for") as one, \
                mock.patch.object(dashboard, "clear_display_cache") as everything:
            dashboard.post_form(self.handler("reference=CO-0801", pending=False))
        one.assert_not_called()
        everything.assert_not_called()

    def test_the_per_co_paths_are_the_production_buttons(self):
        self.assertIn(dashboard.create_ui.CREATE_PATH, dashboard._PER_CO_POSTS)
        self.assertNotIn("/attended/review-approve", dashboard._PER_CO_POSTS)


class PendingTermSummaryTests(unittest.TestCase):
    def source(self):
        return runner.CeFillSource("CO-0801", "2026-10-20T20:38:05.000+0000", "001AAAAAAAAAAAAAAA", "LISI Automotive",
                                   "lisi-group.com", "France", date(2026, 11, 6), date(2027, 11, 5),
                                   "LISI Automotive - CE Only", "lisiautomotive")

    def expectation(self, strict):
        calls = []

        def fill_source(reference, early_activation=False):  # the strict read (CE_ROUTE) and the early one share it
            calls.append(early_activation)
            if not early_activation and strict is not None:
                raise strict
            return self.source()

        with mock.patch.object(runner, "ce_fill_source", side_effect=fill_source), \
                mock.patch("integration.onboarding.state_paths.vm_mode", return_value=False):
            result = runner._production_expectation("CO-0801", runner.CE_ROUTE_PRODUCT, runner.CE_ROUTE_TYPE)
        return result, calls

    def test_only_a_pending_term_shows_the_values_and_flags_the_box(self):
        (_engine, _source, plan), calls = self.expectation(RuntimeError("ce_subscription_unavailable"))
        self.assertEqual(calls, [False, True])
        self.assertIs(plan["pending_term"], True)
        self.assertEqual(str(plan["license_end"]), "2027-11-05")

    def test_an_active_term_has_no_flag_and_other_problems_still_raise(self):
        (_engine, _source, plan), calls = self.expectation(None)
        self.assertEqual(calls, [False])
        self.assertNotIn("pending_term", plan)
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_ambiguous"):
            self.expectation(RuntimeError("ce_subscription_ambiguous"))

    def test_the_summary_text_names_the_box(self):
        self.assertIn("Early activation", dashboard.REVIEW_BLOCKER_TEXT["early_activation_needed"])


if __name__ == "__main__":
    unittest.main()
