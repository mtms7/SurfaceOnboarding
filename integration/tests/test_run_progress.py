import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integration.onboarding import run_progress as rp
from tools import attended_ce_only_playwright as runner
from tools import serve_attended_open_onboardings_dashboard as dashboard


def ev(step, outcome="ok"):
    return {"t": "2026-10-08T10:00:00", "step": step, "outcome": outcome}


CREATE_EVENTS = [ev("source_read"), ev("license_dates"), ev("production_gate", "production_clear"),
                 ev("inventory_precheck", "inventory_clear"), ev("browser_attach"), ev("duplicate_check", "duplicate_clear"),
                 ev("add_account_open", "clicked"), ev("confirm_enable", "enabled"), ev("confirm_click", "clicked"),
                 ev("form_close_wait", "closed"), ev("post_create_search", "duplicate_found"), ev("readback", "api")]


def states(progress):
    return [step.state for step in progress.steps]


class BuildProgressTests(unittest.TestCase):
    def test_no_events_is_starting(self):
        for events in ([], None, "junk", [{"nope": 1}, 5, {"step": 3}]):
            progress = rp.build_progress(runner.CE_ENGINE, events)
            self.assertEqual(("Starting…", 5, "running"), (progress.current, progress.percent, progress.status))
            self.assertEqual(rp.ACTIVE, progress.steps[0].state)

    def test_create_routes_share_the_steps_and_advance_in_order(self):
        for route in (runner.CE_ENGINE, runner.SURFACE_ENGINE, runner.CASE3_ENGINE, None):
            last = 0
            for count in range(1, len(CREATE_EVENTS) + 1):
                progress = rp.build_progress(route, CREATE_EVENTS[:count])
                self.assertGreaterEqual(progress.percent, last)
                self.assertLessEqual(progress.percent, 95)
                last = progress.percent
                self.assertEqual("running", progress.status)
        full = rp.build_progress(runner.SURFACE_ENGINE, CREATE_EVENTS)
        self.assertEqual([rp.DONE] * 10, states(full))
        self.assertEqual(95, full.percent)  # never 100 until a result exists

    def test_midway_step_is_active_and_skipped_steps_do_not_block(self):
        progress = rp.build_progress(runner.CE_ENGINE, [ev("source_read"), ev("browser_attach")])
        self.assertEqual([rp.DONE] * 4 + [rp.ACTIVE] + [rp.PENDING] * 5, states(progress))
        self.assertEqual("Checking Leonardo Dev for duplicates", progress.current)

    def test_unknown_events_never_advance(self):
        noisy = [ev("browser_console", "error"), ev("browser_response", "200"), ev("tenant_search", "200"),
                 ev("fill_toggle", "ok"), ev("something_new")]
        progress = rp.build_progress(runner.CE_ENGINE, noisy)
        self.assertEqual("Starting…", progress.current)
        self.assertEqual(5, progress.percent)
        # an event of a known step with an unexpected outcome does not count either
        self.assertEqual("Starting…", rp.build_progress(runner.CE_ENGINE, [ev("source_read", "weird")]).current)

    def test_renewal_steps(self):
        route = "case_4_renew_surface_new_ce"
        self.assertEqual("Starting…", rp.build_progress(route, [ev("production_gate")]).current)
        progress = rp.build_progress(route, [ev("source"), ev("mirror", "skipped_verified_mirror"), ev("renewal_dry_run", "renewal_dry_run_planned")])
        self.assertEqual([rp.DONE] * 3 + [rp.ACTIVE], states(progress))
        self.assertEqual("Applying the renewal in Leonardo Dev", progress.current)
        self.assertEqual(4, len(rp.build_progress("renewal_run", []).steps))

    def test_failure_marks_the_running_step_and_keeps_the_cap(self):
        progress = rp.build_progress(runner.CE_ENGINE, CREATE_EVENTS[:5], result="duplicate_found")
        self.assertEqual("failed", progress.status)
        self.assertEqual([rp.DONE] * 4 + [rp.FAILED] + [rp.PENDING] * 5, states(progress))
        self.assertLessEqual(progress.percent, 95)
        # failure before any event still shows a failed first step
        self.assertEqual(rp.FAILED, rp.build_progress(runner.CE_ENGINE, [], result="x").steps[0].state)

    def test_success_is_done_and_100(self):
        for route, result in ((runner.CE_ENGINE, "readback_verified"), ("case_6_renew_both", "renewal_edit_verified")):
            progress = rp.build_progress(route, [], result=result, success=True)
            self.assertEqual((100, "Done", "done"), (progress.percent, progress.current, progress.status))
            self.assertTrue(all(step.state == rp.DONE for step in progress.steps))

    def test_progress_file_keeps_milestones_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            rp.record_milestone(path, "CO-1", {**ev("source_read"), "detail": "secret", "field": "x"})
            rp.record_milestone(path, "CO-1", ev("browser_response", "200"))  # not a milestone
            rp.record_milestone(path, "CO-2", ev("production_gate", "c"))
            events = rp.read_milestones(path, "CO-1")
            self.assertEqual([{"t": "2026-10-08T10:00:00", "step": "source_read", "outcome": "ok"}], events)
            self.assertEqual([], rp.read_milestones(path, "CO-1", since="2026-10-08T11:00:00"))
            self.assertEqual([], rp.read_milestones(Path(folder) / "missing.json", "CO-1"))
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))

    def test_runlog_records_milestones_but_not_the_placeholder_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            with mock.patch.object(runner, "PROGRESS_PATH", path):
                runner.RunLog("CO-9", "run").event("source_read", "ok", detail="Acme Corp")
                runner.RunLog("-", "none").event("source_read", "ok")
            self.assertEqual(["source_read"], [e["step"] for e in rp.read_milestones(path, "CO-9")])
            self.assertEqual([], rp.read_milestones(path, "-"))


class ChromeArgsTests(unittest.TestCase):
    def test_default_has_no_quiet_flags(self):
        args = runner.automation_chrome_args("chrome.exe", Path("prof"))
        self.assertEqual("chrome.exe", args[0])
        self.assertIn("--remote-debugging-port=0", args)
        self.assertFalse(any(flag in args for flag in runner.QUIET_CHROME_FLAGS))
        self.assertEqual(args, runner.automation_chrome_args("chrome.exe", Path("prof"), False))

    def test_quiet_adds_the_four_flags_before_the_anchor_url(self):
        args = runner.automation_chrome_args("chrome.exe", Path("prof"), True)
        for flag in ("--window-position=-2400,-2400", "--disable-renderer-backgrounding",
                     "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows"):
            self.assertIn(flag, args)
        self.assertEqual(runner.ANCHOR_TAB_URL, args[-1])


class DashboardBarTests(unittest.TestCase):
    RECORD = {"source_revision": "r1", "started_on": "2026-10-08T10:00:00"}

    def test_bar_while_running(self):
        with mock.patch.object(dashboard, "_progress_events", return_value=CREATE_EVENTS[:5]):
            html = dashboard._running_note("CO-0702", self.RECORD)
        self.assertIn("role='progressbar'", html)
        self.assertRegex(html, r"aria-valuenow='\d+'")
        self.assertIn("Do not start another", html)
        self.assertIn("View progress", html)
        self.assertNotIn("<script", html)
        self.assertLessEqual(int(re.search(r"aria-valuenow='(\d+)'", html).group(1)), 95)

    def test_status_page_refreshes_only_while_open_and_shows_result_when_done(self):
        with mock.patch.object(dashboard, "_progress_events", return_value=[]):
            running = dashboard.page_ce_only_runner_status({"CO-0702": dict(self.RECORD)}, "CO-0702")
        self.assertIn("role='progressbar'", running)
        self.assertIn("http-equiv='refresh' content='5'", running)
        done = dashboard.page_ce_only_runner_status(
            {"CO-0702": {**self.RECORD, "result": "readback_verified", "completed_on": "2026-10-08T10:05:00"}}, "CO-0702")
        self.assertNotIn("role='progressbar'", done)
        self.assertIn("outcome-success", done)
        for page in (running, done):
            self.assertNotIn("<script", page)

    def test_open_run_detection_drives_the_co_page_refresh(self):
        with mock.patch.object(dashboard, "load_runner_state", return_value={"CO-1": dict(self.RECORD)}):
            self.assertTrue(dashboard._open_run_for("CO-1"))
            self.assertFalse(dashboard._open_run_for("CO-2"))
        with mock.patch.object(dashboard, "load_runner_state", return_value={"CO-1": {**self.RECORD, "result": "x"}}):
            self.assertFalse(dashboard._open_run_for("CO-1"))
        with mock.patch.object(dashboard, "load_runner_state", side_effect=dashboard.RunnerStateUnavailable("x")):
            self.assertFalse(dashboard._open_run_for("CO-1"))


if __name__ == "__main__":
    unittest.main()
