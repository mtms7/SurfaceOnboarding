"""Owner request 2026-10-19 (after the CO-0798 create): the production create stays on the CO page with a progress bar
(like the Dev runs) instead of a separate plain "Started." page, and confirms when the onboarding is done.

Progress comes only from recorded evidence (create records, the UUID-write log, the stage-write log and the follow-up
result), never simulated. Hermetic: temporary folders, fake processes; nothing is launched or sent.
"""

import unittest
from datetime import timedelta
from unittest import mock

from integration.onboarding import production_create_ui as ui
from integration.tests import test_production_create_ui as base
import tools.serve_attended_open_onboardings_dashboard as dashboard

CO, UUID, NOW = base.CO, base.UUID, base.NOW
SINCE = (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProgressTests(base.Base):
    def progress(self, running=True, follow=None):
        return ui.production_progress(self.dir, CO, SINCE, running=running, follow=follow)

    def states(self, progress):
        return [state for _label, state in progress["steps"]]

    def test_just_launched_waits_for_the_sign_in(self):
        p = self.progress()
        self.assertEqual(self.states(p), ["active", "pending", "pending", "pending", "pending"])
        self.assertIn("SSO", p["steps"][0][0])
        self.assertFalse(p["done"])
        self.assertLess(p["percent"], 100)

    def test_records_before_the_launch_do_not_count(self):
        self.record("readback_verified", when=NOW - timedelta(hours=1), account_uuid=UUID)
        self.assertEqual(self.states(self.progress()), ["active", "pending", "pending", "pending", "pending"])

    def test_steps_follow_the_recorded_evidence(self):
        self.record("attempt_started")
        self.assertEqual(self.states(self.progress()), ["done", "active", "pending", "pending", "pending"])
        self.record("readback_verified", when=NOW + timedelta(seconds=30), account_uuid=UUID)
        self.assertEqual(self.states(self.progress(running=False)), ["done", "done", "done", "active", "pending"])
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        self.assertEqual(self.states(self.progress(running=False)), ["done", "done", "done", "done", "active"])
        ui.record_stage_write(self.dir, CO, "Account Scanning", "written_verified", NOW)
        p = self.progress(running=False, follow={"uuid": "written_verified", "stage": "written_verified"})
        self.assertEqual(self.states(p), ["done"] * 5)
        self.assertTrue(p["done"])
        self.assertEqual(p["percent"], 100)
        self.assertIsNone(p["failed"])

    def test_a_refusal_fails_the_running_step_with_its_reason(self):
        self.record("refused", refusal_reason="production_session_not_ready")
        p = self.progress(running=False)
        self.assertEqual(self.states(p), ["failed", "pending", "pending", "pending", "pending"])
        self.assertEqual(p["failed"], "production_session_not_ready")

    def test_an_unverified_create_fails_the_readback_step(self):
        self.record("attempt_started")
        self.record("created_unverified", when=NOW + timedelta(seconds=30))
        self.assertEqual(self.states(self.progress(running=False))[:3], ["done", "done", "failed"])

    def test_a_follow_up_that_did_not_move_the_stage_is_shown(self):
        self.record("attempt_started")
        self.record("readback_verified", when=NOW + timedelta(seconds=30), account_uuid=UUID)
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        p = self.progress(running=False, follow={"uuid": "written_verified", "stage": "stage_not_expected"})
        self.assertEqual(self.states(p), ["done", "done", "done", "done", "failed"])
        self.assertEqual(p["failed"], "stage_not_expected")

    def test_the_card_has_no_control_and_never_the_uuid(self):
        self.record("readback_verified", account_uuid=UUID)
        html = ui.progress_card_html(self.progress(running=False))
        for tag in ("<form", "<button", "<input", "<script"):
            self.assertNotIn(tag, html)
        self.assertNotIn(UUID, html)
        self.assertIn("role='progressbar'", html)


class DashboardFollowTests(base.Base):
    def setUp(self):
        super().setUp()
        dashboard.PRODUCTION_RUNS.clear()
        self.addCleanup(dashboard.PRODUCTION_RUNS.clear)

    def test_a_launch_is_registered_and_followed(self):
        proc = mock.Mock()
        threads = []
        with mock.patch.object(dashboard, "_launch_production_create", return_value=proc), \
                mock.patch.object(dashboard, "Thread", side_effect=lambda **kw: threads.append(kw) or mock.Mock()):
            self.assertIs(dashboard._launch_production_create_followed(CO, base.REV, {}), proc)
        self.assertIn(CO, dashboard.PRODUCTION_RUNS)
        self.assertEqual(threads[0]["target"], dashboard._follow_production_create)
        self.assertEqual(threads[0]["args"], (CO, proc))

    def test_a_failed_launch_registers_nothing(self):
        with mock.patch.object(dashboard, "_launch_production_create", return_value=None):
            self.assertIsNone(dashboard._launch_production_create_followed(CO, base.REV, {}))
        self.assertNotIn(CO, dashboard.PRODUCTION_RUNS)

    def test_the_follow_up_waits_for_the_runner_then_runs_the_ce_follow_up_once(self):
        proc = mock.Mock()
        dashboard.PRODUCTION_RUNS[CO] = {"since": SINCE, "follow": None}
        with mock.patch.object(dashboard.create_ui, "after_verified_create",
                               return_value={"uuid": "written_verified", "stage": "written_verified"}) as follow, \
                mock.patch.object(dashboard, "_create_ui_inputs", return_value=self.inputs()):
            dashboard._follow_production_create(CO, proc)
        proc.wait.assert_called_once()
        follow.assert_called_once()
        self.assertEqual(dashboard.PRODUCTION_RUNS[CO]["follow"], {"uuid": "written_verified", "stage": "written_verified"})

    def test_a_crashing_follow_up_is_recorded_not_raised(self):
        dashboard.PRODUCTION_RUNS[CO] = {"since": SINCE, "follow": None}
        with mock.patch.object(dashboard.create_ui, "after_verified_create", side_effect=RuntimeError("boom")), \
                mock.patch.object(dashboard, "_create_ui_inputs", return_value=self.inputs()):
            dashboard._follow_production_create(CO, mock.Mock())
        self.assertEqual(dashboard.PRODUCTION_RUNS[CO]["follow"], {"uuid": "follow_up_failed", "stage": "follow_up_failed"})

    def test_the_writeback_read_includes_the_stage(self):
        with mock.patch.object(dashboard, "sf_json", return_value={"status": 0, "result": {"records": []}}) as query:
            dashboard._read_production_writeback_row(CO)
        self.assertIn("Onboarding_Stage__c", query.call_args.args[0][3])


if __name__ == "__main__":
    unittest.main()
