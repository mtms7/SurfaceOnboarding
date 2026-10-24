"""Dashboard wiring of the Case 3 production dry run (owner decision 2026-10-20: a launch button with a progress bar).

The card appears on a Case 3 CO page, the button posts to ``/attended/production-dry-run``, the dashboard starts the runner with
``--production-dry-run --route case_3_combined_baseline`` (never ``--owner-go``, never a create), and while it runs the page refreshes
itself and shows the recorded milestones. The VM never gets a control. Hermetic: fake process, temporary folders, no browser.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_approval as approval
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import production_dry_run_ui as dry_ui
from integration.onboarding import run_progress
from integration.onboarding import session_readiness as readiness
from integration.tests import test_production_dry_run_ui as base
import tools.attended_ce_only_playwright as runner

CO, REF, REV, OWNER, NOW, CASE3 = base.CO, base.REF, base.REV, base.OWNER, base.NOW, base.CASE3


class Process:
    def __init__(self, running=True):
        self.running = running

    def poll(self):
        return None if self.running else 0


class Fixture(base.Base):
    def setUp(self):
        super().setUp()
        runs = mock.patch.dict(dashboard.PRODUCTION_DRY_RUNS, clear=True)
        runs.start()
        self.addCleanup(runs.stop)
        progress = Path(tempfile.mkdtemp()) / "progress.json"
        patch = mock.patch.object(runner, "PROGRESS_PATH", progress)
        patch.start()
        self.addCleanup(patch.stop)
        self.progress = progress

    def patched_inputs(self, **over):
        return mock.patch.object(dashboard, "_create_ui_inputs", lambda reference, row, operator=None: self.inputs(**over))


class PageTests(Fixture):
    def test_locked_and_other_routes(self):
        with self.patched_inputs():
            html = dashboard._production_dry_run_html(CO, base.row())
        self.assertIn("Locked", html)
        self.assertFalse(any(tag in html for tag in base.FORBIDDEN))
        with self.patched_inputs(row=base.row(Onboarding_Product__c="Surface")):
            self.assertEqual(dashboard._production_dry_run_html(CO, base.row(Onboarding_Product__c="Surface")), "")

    def test_ready_shows_the_button_and_a_failing_read_shows_nothing(self):
        self.unlocked()
        with self.patched_inputs():
            html = dashboard._production_dry_run_html(CO, base.row())
        self.assertIn("Run production dry run", html)
        with mock.patch.object(dashboard, "_create_ui_inputs", side_effect=RuntimeError("boom")):
            self.assertEqual(dashboard._production_dry_run_html(CO, base.row()), "")  # fail closed: no control

    def test_a_running_run_shows_real_milestones_and_asks_the_page_to_refresh(self):
        self.unlocked()
        since = "2026-10-20T11:59:00Z"
        dashboard.PRODUCTION_DRY_RUNS[CO] = {"since": since, "process": Process(True)}
        for step in ("pdr_source", "pdr_gate"):
            run_progress.record_milestone(runner.PROGRESS_PATH, CO, {"t": "2026-10-20T11:59:30Z", "step": step, "outcome": "ok"})
        with self.patched_inputs():
            html = dashboard._production_dry_run_html(CO, base.row())
        self.assertIn("role='progressbar'", html)
        self.assertIn("35%", html)  # two of six steps recorded, nothing simulated
        self.assertIn("Signing in to BackOffice production", html)
        self.assertNotIn("<form", html)
        self.assertIn("http-equiv='refresh'", dashboard._dry_run_refresh(CO))

    def test_a_finished_run_stops_the_refresh_and_shows_the_recorded_result(self):
        self.unlocked()
        since = "2026-10-20T11:00:00Z"
        dashboard.PRODUCTION_DRY_RUNS[CO] = {"since": since, "process": Process(False)}
        self.record("dry_run_passed", when=NOW - timedelta(minutes=2), observations={"max_scan_duration_control": "present"})
        self.assertEqual(dashboard._dry_run_refresh(CO), "")
        with self.patched_inputs():
            html = dashboard._production_dry_run_html(CO, base.row())
        self.assertIn("Dry run passed", html)
        self.assertIn("100%", html)
        self.assertIn("Maximum scan Duration control: present", html)

    def test_other_cos_and_unknown_runs_never_refresh(self):
        self.assertEqual(dashboard._dry_run_refresh("CO-0001"), "")


class LaunchTests(Fixture):
    def launch(self, **env):
        env = {"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1", "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": REF,
               "SOMETHING_ELSE": "not passed on", **env}
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                mock.patch.object(dashboard.subprocess, "Popen", return_value=Process(True)) as popen:
            result = dashboard._launch_production_dry_run(CO, REV, CASE3, env)
        return result, popen

    def test_it_starts_only_the_dry_run_of_the_case_3_route(self):
        process, popen = self.launch()
        self.assertIsNotNone(process)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[2:], ["--production-dry-run", "--co", CO, "--revision", REV, "--route", CASE3])
        self.assertNotIn("--owner-go", argv)
        self.assertNotIn("--production-create", argv)
        child = popen.call_args.kwargs["env"]
        self.assertEqual(child["SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"], REF)
        self.assertNotIn("SOMETHING_ELSE", child)  # only SURFACE_ONBOARDING_PRODUCTION_* values are taken from the gate's env
        self.assertIn(CO, dashboard.PRODUCTION_DRY_RUNS)
        self.assertRegex(dashboard.PRODUCTION_DRY_RUNS[CO]["since"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_a_wrong_route_or_reference_or_a_machine_that_cannot_launch_starts_nothing(self):
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                mock.patch.object(dashboard.subprocess, "Popen") as popen:
            for args in ((CO, REV, "case_2_renewal", {}), ("not-a-co", REV, CASE3, {}), (CO, "", CASE3, {})):
                self.assertIsNone(dashboard._launch_production_dry_run(*args))
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=False), \
                mock.patch.object(dashboard.subprocess, "Popen") as popen:
            self.assertIsNone(dashboard._launch_production_dry_run(CO, REV, CASE3, {}))
        popen.assert_not_called()

    def test_an_os_error_is_a_failed_launch_not_a_crash(self):
        with mock.patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                mock.patch.object(dashboard.subprocess, "Popen", side_effect=OSError("no")):
            self.assertIsNone(dashboard._launch_production_dry_run(CO, REV, CASE3, {}))
        self.assertNotIn(CO, dashboard.PRODUCTION_DRY_RUNS)


class PostTests(Fixture):
    def handler(self):
        sent = []
        handler = object.__new__(dashboard.Handler)
        handler.send_page = lambda status, page: sent.append(("page", int(status), page))
        handler.send_redirect = lambda target: sent.append(("redirect", target))
        handler.request_operator = lambda: OWNER
        return handler, sent

    def test_the_route_is_registered_and_the_vm_still_refuses_it(self):
        self.assertIn(dry_ui.DRY_RUN_PATH, dashboard.POST_ROUTES)
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())
        self.assertNotIn(dry_ui.DRY_RUN_PATH, create_ui.POST_PATHS)  # a separate action, never a create path

    def test_locked_refuses_and_launches_nothing(self):
        handler, sent = self.handler()
        launch = mock.Mock(side_effect=AssertionError("must not launch"))
        with mock.patch.object(dashboard, "detail_row", return_value=base.row()), self.patched_inputs(), \
                mock.patch.object(dashboard, "_launch_production_dry_run", launch):
            handler._post_production_dry_run(CO, {"reference": [CO], "nonce": ["x"]})
        self.assertEqual(sent[0][:2], ("page", 409))
        self.assertIn("locked in this build", sent[0][2])
        self.assertIn("Nothing was sent", sent[0][2])
        launch.assert_not_called()

    def test_a_good_post_launches_once_and_redirects_to_the_co_page(self):
        self.unlocked()
        handler, sent = self.handler()
        with self.patched_inputs():
            html = dry_ui.card_html(self.inputs())
        values = {"reference": [CO], "nonce": [re.search(r"name='nonce' value='([^']+)'", html).group(1)],
                  "production_dry_run_authorized": ["1"]}
        launch = mock.Mock(return_value=Process(False))
        with mock.patch.object(dashboard, "detail_row", return_value=base.row()), self.patched_inputs(), \
                mock.patch.object(dashboard, "_launch_production_dry_run", launch):
            handler._post_production_dry_run(CO, values)
        self.assertEqual(sent, [("redirect", "/co/" + CO + "?env=prod")])
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[:3], (CO, REV, CASE3))

    def test_an_unreadable_salesforce_row_is_a_service_unavailable_page(self):
        handler, sent = self.handler()
        with mock.patch.object(dashboard, "detail_row", side_effect=dashboard.ReadUnavailable()):
            handler._post_production_dry_run(CO, {"reference": [CO]})
        self.assertEqual(sent[0][:2], ("page", 503))


class SourceTests(unittest.TestCase):
    def test_the_pure_module_starts_nothing_and_reads_nothing_external(self):
        text = Path(dry_ui.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "import socket", "urllib", "playwright", "print(", "sf_json", "sf_write_json",
                          "PRODUCTION_RUNNER_SUPPORTED"):
            self.assertNotIn(forbidden, text)

    def test_the_launcher_never_passes_a_create_flag(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        body = source[source.index("def _launch_production_dry_run"):]
        body = body[:body.index("\ndef ", 10)]
        for forbidden in ("--production-create", "--owner-go", "--confirm-write"):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
