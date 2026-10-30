"""Owner request 2026-10-30: confirm the CE scan from BackOffice soon after the create, without a new sign-in.

The clone is refreshed about every 12 h, but a CE scan ends ~26 min after the create (CO-0801: started +16 min, ran 10 min).
While the production window is kept and signed in, the dashboard runs the button's own read-only scan check with
--kept-window-only: 20 min after the create, then every 5 min until 90 min, then once per sign-in. It never launches a
browser, never waits for a sign-in, never runs under another production run, and leaves a hand-moved stage alone for a day.
Hermetic: temporary folders and fakes; nothing is launched, opened or sent.
"""

from __future__ import annotations

import json
import sys
import unittest
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_create_ui as ui
from integration.tests import test_production_create_ui as base

CO, UUID, NOW = base.CO, base.UUID, base.NOW
MIN = timedelta(minutes=1)


class DueTests(base.Base):
    def setUp(self):
        super().setUp()
        self.record("readback_verified", when=NOW, account_uuid=UUID, tenant_name="Tenant - CE Only")

    def due(self, minutes, last=None, sign_in=None):
        return ui.kept_scan_due(self.dir, CO, NOW + minutes * MIN, last, sign_in)

    def test_the_first_look_is_20_minutes_after_the_create(self):
        self.assertFalse(self.due(19))
        self.assertTrue(self.due(20))

    def test_never_twice_within_5_minutes(self):
        self.assertFalse(self.due(24, last=NOW + 21 * MIN))
        self.assertTrue(self.due(26, last=NOW + 21 * MIN))

    def test_after_90_minutes_only_once_per_sign_in(self):
        self.assertTrue(self.due(90, last=NOW + 80 * MIN))
        self.assertFalse(self.due(91, last=NOW + 80 * MIN))
        self.assertFalse(self.due(300))
        self.assertTrue(self.due(300, last=NOW + 80 * MIN, sign_in=NOW + 299 * MIN))
        self.assertFalse(self.due(300, last=NOW + 290 * MIN, sign_in=NOW + 200 * MIN))  # already looked since that sign-in
        self.assertFalse(self.due(300, sign_in=NOW + 301 * MIN))  # a sign-in "in the future" is ignored

    def test_no_verified_create_is_never_due(self):
        self.assertFalse(ui.kept_scan_due(self.dir, "CO-0999", NOW + 30 * MIN, None))
        with mock.patch.object(ui, "load_co_records", side_effect=OSError):
            self.assertFalse(self.due(30))


class KeptProbeTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name) / "production-profile"
        self.profile.mkdir()
        patch = mock.patch.object(runner, "leonardo_profile", return_value=(self.profile, True))
        patch.start()
        self.addCleanup(patch.stop)

    def keep_until(self, moment):
        self.profile.with_name(self.profile.name + runner.PRODUCTION_KEEP_SUFFIX).write_text(moment.isoformat(),
                                                                                               encoding="utf-8")

    def test_kept_only_with_a_future_deadline_and_a_live_browser(self):
        with mock.patch.object(runner, "_verified_automation_port", return_value=9222):
            self.assertFalse(runner.production_window_kept(NOW))  # no deadline
            self.keep_until(NOW + timedelta(hours=1))
            self.assertTrue(runner.production_window_kept(NOW))
            self.assertFalse(runner.production_window_kept(NOW + timedelta(hours=1)))  # passed
        with mock.patch.object(runner, "_verified_automation_port", return_value=None):
            self.assertFalse(runner.production_window_kept(NOW))  # the window is gone

    def test_a_temporary_profile_is_never_kept(self):
        self.keep_until(NOW + timedelta(hours=1))
        with mock.patch.object(runner, "leonardo_profile", return_value=(self.profile, False)), \
                mock.patch.object(runner, "_verified_automation_port", return_value=9222):
            self.assertFalse(runner.production_window_kept(NOW))


class FakePlaywright:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class KeptPageTests(unittest.TestCase):
    """_kept_browser_page: a tab in the kept window or nothing; never a launch, a sign-in wait or bring_to_front."""

    def setUp(self):
        fake = SimpleNamespace(sync_playwright=FakePlaywright)
        patch = mock.patch.dict(sys.modules, {"playwright": SimpleNamespace(sync_api=fake), "playwright.sync_api": fake})
        patch.start()
        self.addCleanup(patch.stop)
        self.launch = mock.patch.object(runner, "_launch_automation_chrome").start()
        self.addCleanup(mock.patch.stopall)

    def open(self):
        with runner._kept_browser_page(runner.production_probe.probe_target(), 5) as page:
            return page

    def test_no_kept_window_opens_nothing(self):
        with mock.patch.object(runner, "production_window_kept", return_value=False), \
                mock.patch.object(runner, "_cdp_open_tab") as open_tab:
            with self.assertRaisesRegex(RuntimeError, "production_window_not_kept"):
                self.open()
        open_tab.assert_not_called()
        self.launch.assert_not_called()

    def test_a_signed_out_window_stops_without_prompting_and_cleans_up(self):
        page = mock.Mock()
        released, finished = [], []
        with mock.patch.object(runner, "production_window_kept", return_value=True), \
                mock.patch.object(runner, "leonardo_profile", return_value=(Path("p"), True)), \
                mock.patch.object(runner, "_verified_automation_port", return_value=9222), \
                mock.patch.object(runner, "_cdp_page_targets", return_value=[{"id": "A"}]), \
                mock.patch.object(runner, "_cdp_open_tab", return_value="B"), \
                mock.patch.object(runner, "_probe_attach", return_value=(mock.Mock(), page)), \
                mock.patch.object(runner, "_probe_snapshot", return_value=("/login", None)), \
                mock.patch.object(runner.production_probe, "wait_for_login", return_value=False) as wait, \
                mock.patch.object(runner, "_release_automation_tab", side_effect=released.append), \
                mock.patch.object(runner, "_finish_production_window", side_effect=finished.append):
            with self.assertRaisesRegex(RuntimeError, "production_window_signed_out"):
                self.open()
        self.assertEqual(wait.call_args.args[1], 5)
        page.bring_to_front.assert_not_called()
        self.assertEqual(len(released), 1)
        self.assertTrue(released[0].reused and released[0].persist and released[0].chrome_proc is None)
        self.assertEqual(finished, released)
        self.launch.assert_not_called()


class RunnerTests(base.Base):
    def run_check(self, error, **kwargs):
        self.record("readback_verified", account_uuid=UUID, tenant_name="Tenant - CE Only")
        opened = []

        @contextmanager
        def fake(target, wait):
            opened.append(wait)
            raise RuntimeError(error)
            yield  # pragma: no cover

        with mock.patch.object(runner, "_kept_browser_page", fake), \
                mock.patch.object(runner, "_pdr_browser_page", side_effect=AssertionError("no sign-in window")), \
                mock.patch.object(runner, "_launch_os_name", return_value="nt"):
            line = json.loads(runner.run_production_scan_check(CO, environ={}, now=NOW, records_parent=self.dir,
                                                               kept_only=True, **kwargs))
        return line, opened

    def test_kept_only_uses_the_kept_page_with_the_short_wait(self):
        line, opened = self.run_check("production_window_not_kept")
        self.assertEqual(line["result"], "production_window_not_kept")
        self.assertEqual(opened, [runner.KEPT_SIGN_IN_SECONDS])
        self.assertFalse((self.dir / "production-scan-checks").exists())  # nothing recorded

    def test_a_signed_out_window_is_its_own_result(self):
        line, _ = self.run_check("production_window_signed_out")
        self.assertEqual(line["result"], "production_window_signed_out")
        self.assertEqual(line["production_write"], "not_performed")


class TickTests(base.Base):
    def setUp(self):
        super().setUp()
        self.record("readback_verified", when=NOW, account_uuid=UUID, tenant_name="Tenant - CE Only")
        self.started = []
        for target, value in ((dashboard, "vm_mode"), (dashboard, "_production_run_active")):
            mock.patch.object(target, value, return_value=False).start()
        self.kept = mock.patch.object(dashboard.attended_runner, "production_window_kept", return_value=True).start()
        mock.patch.object(dashboard, "production_records_parent", return_value=self.dir).start()
        mock.patch.object(dashboard.create_ui, "auto_scan_candidates", return_value=[CO]).start()
        mock.patch.object(dashboard.create_ui.LAUNCHES, "claim", side_effect=lambda start: start() is not None).start()
        mock.patch.object(dashboard, "_launch_scan_check",
                          side_effect=lambda ref, kept_only=False: self.started.append((ref, kept_only)) or object()).start()
        mock.patch.dict(dashboard.KEPT_SCAN_STATE, clear=True).start()
        mock.patch.dict(dashboard.PRODUCTION_SCAN_RUNS, clear=True).start()
        mock.patch.object(dashboard, "KEPT_SCAN_SIGN_IN_AT", None).start()
        self.addCleanup(mock.patch.stopall)

    def test_a_due_co_starts_one_kept_only_check_and_waits_5_minutes(self):
        self.assertIsNone(dashboard._kept_scan_tick(NOW + 10 * MIN))
        self.assertEqual(dashboard._kept_scan_tick(NOW + 20 * MIN), CO)
        self.assertEqual(self.started, [(CO, True)])
        self.assertIsNone(dashboard._kept_scan_tick(NOW + 22 * MIN))
        self.assertEqual(dashboard._kept_scan_tick(NOW + 25 * MIN), CO)

    def test_nothing_starts_without_a_kept_window_under_a_run_or_on_the_vm(self):
        self.kept.return_value = False
        self.assertIsNone(dashboard._kept_scan_tick(NOW + 30 * MIN))
        self.kept.return_value = True
        with mock.patch.object(dashboard, "_production_run_active", return_value=True):
            self.assertIsNone(dashboard._kept_scan_tick(NOW + 30 * MIN))
        with mock.patch.object(dashboard, "vm_mode", return_value=True):
            self.assertIsNone(dashboard._kept_scan_tick(NOW + 30 * MIN))
        self.assertEqual(self.started, [])

    def test_a_held_lock_starts_nothing(self):
        with mock.patch.object(dashboard.create_ui.LAUNCHES, "claim", return_value=False):
            self.assertIsNone(dashboard._kept_scan_tick(NOW + 30 * MIN))
        self.assertNotIn("at", dashboard.KEPT_SCAN_STATE.get(CO, {}))

    def test_a_hand_moved_stage_is_left_alone_for_a_day(self):
        self.assertEqual(dashboard._kept_scan_tick(NOW + 20 * MIN), CO)
        dashboard.PRODUCTION_SCAN_RUNS[CO] = {"running": False, "stages": ["stage_not_expected"]}
        with mock.patch.object(dashboard, "KEPT_SCAN_SIGN_IN_AT", NOW + 60 * MIN):
            self.assertIsNone(dashboard._kept_scan_tick(NOW + 61 * MIN))
        self.assertEqual(dashboard.KEPT_SCAN_STATE[CO]["hold_until"], NOW + 20 * MIN + timedelta(hours=24))
        self.assertEqual(len(self.started), 1)

    def test_a_sign_in_looks_once_more_after_90_minutes(self):
        self.assertIsNone(dashboard._kept_scan_tick(NOW + 120 * MIN))
        with mock.patch.object(dashboard, "KEPT_SCAN_SIGN_IN_AT", NOW + 119 * MIN):
            self.assertEqual(dashboard._kept_scan_tick(NOW + 120 * MIN), CO)
            self.assertIsNone(dashboard._kept_scan_tick(NOW + 130 * MIN))

    def test_errors_never_escape(self):
        with mock.patch.object(dashboard.create_ui, "auto_scan_candidates", side_effect=RuntimeError("x")):
            self.assertIsNone(dashboard._kept_scan_tick(NOW + 30 * MIN))


class WiringTests(unittest.TestCase):
    def test_sign_in_marks_the_time_and_wakes_both_jobs(self):
        with mock.patch.object(dashboard, "vm_mode", return_value=False), \
                mock.patch.object(dashboard, "KEPT_SCAN_SIGN_IN_AT", None):
            dashboard.KEPT_SCAN_WAKE.clear()
            dashboard.AUTO_SCAN_WAKE.clear()
            dashboard.request_auto_scan()
            self.assertIsNotNone(dashboard.KEPT_SCAN_SIGN_IN_AT)
            self.assertTrue(dashboard.KEPT_SCAN_WAKE.is_set() and dashboard.AUTO_SCAN_WAKE.is_set())
        dashboard.KEPT_SCAN_WAKE.clear()
        dashboard.AUTO_SCAN_WAKE.clear()

    def test_the_launch_adds_the_flag_only_for_the_automatic_check(self):
        calls = []
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                mock.patch.object(dashboard, "ATTENDED_CE_ONLY_RUNNER", mock.Mock(is_file=lambda: True)), \
                mock.patch.object(dashboard, "runner_log_dir", return_value=None), \
                mock.patch.object(dashboard.subprocess, "Popen", side_effect=lambda argv, **kw: calls.append(argv) or mock.Mock()), \
                mock.patch.object(dashboard, "Thread"), \
                mock.patch.dict(dashboard.PRODUCTION_SCAN_RUNS, clear=True):
            dashboard._launch_scan_check(CO, kept_only=True)
            dashboard._launch_scan_check(CO)
        self.assertEqual(calls[0][-1], "--kept-window-only")
        self.assertNotIn("--kept-window-only", calls[1])

    def test_the_cli_flag_is_only_for_the_scan_check(self):
        with mock.patch.object(sys, "argv", ["runner", "--kept-window-only"]), \
                mock.patch.object(runner, "run_production_scan_check") as check, \
                mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                runner.main()
        check.assert_not_called()
        with mock.patch.object(sys, "argv", ["runner", "--production-scan-check", "--co", CO, "--kept-window-only"]), \
                mock.patch.object(runner, "run_production_scan_check", return_value="{}") as check, \
                mock.patch("builtins.print"):
            self.assertEqual(runner.main(), 0)
        check.assert_called_once_with(CO, kept_only=True)


if __name__ == "__main__":
    unittest.main()
