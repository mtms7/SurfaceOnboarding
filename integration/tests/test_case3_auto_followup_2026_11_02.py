"""Owner request 2026-11-02 (CO-0757): the Case 3 Account ID / UUID write and Request Approved -> Account Scanning waited ~8 min for
a review click (the progress bar looked stuck on "Your review: ... (running)"). They now run automatically when the create runner
ends, through the same guarded writes as the owner's confirmation (gate, intent line, fixed argv, read-after-write, no retry).
The CE scan card no longer says "UUID not written" for a Case 3 CO: its scan is checked by hand.
Hermetic: the fake org of test_case3_followup; no Salesforce, no browser.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from integration.onboarding import case3_followup as fu
from integration.onboarding import production_create_record as records
from integration.onboarding import production_create_ui as create_ui
from integration.tests import test_case3_followup as base
import tools.serve_attended_open_onboardings_dashboard as dashboard


class AutoFollowupTests(base.Base):
    def run_auto(self, verified=(base.VERIFIED,), **over):
        return fu.after_verified_create(self.inputs(**over), list(verified), self.org.read, self.org.run, "surface-onboarding")

    def lines(self):
        path = self.parent / records.RECORDS_SUBDIR / fu.WRITES_FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_a_verified_create_writes_the_ids_then_the_stage(self):
        self.assertEqual(self.run_auto(), {"ids": "written_verified", "stage": "written_verified"})
        self.assertEqual(self.org.row["Account_UUID__c"], base.UUID)
        self.assertEqual(self.org.row["Surface_Account_ID__c"], base.ACCOUNT_ID)
        self.assertEqual(self.org.row["Onboarding_Stage__c"], "Account Scanning")
        self.assertEqual([(l["step"], l["result"]) for l in self.lines()],
                         [("ids", "write_started"), ("ids", "written_verified"), ("stage", "write_started"),
                          ("stage", "written_verified")])

    def test_without_a_verified_create_nothing_is_sent(self):
        self.assertEqual(self.run_auto(verified=()), {"ids": "tenant_not_verified", "stage": "skipped_ids_not_verified"})
        self.assertEqual(self.org.calls, [])

    def test_the_lock_and_the_vm_refuse_before_any_read(self):
        self.org.read = mock.Mock(side_effect=AssertionError("no read"))
        self.assertEqual(self.run_auto(vm=True)["ids"], "vm_read_only")

    def test_a_rejected_id_write_stops_before_the_stage(self):
        self.org.outcomes = [False]
        self.assertEqual(self.run_auto(), {"ids": "write_rejected", "stage": "skipped_ids_not_verified"})
        self.assertEqual(self.org.row["Onboarding_Stage__c"], "Request Approved")
        self.assertEqual(len(self.org.calls), 1)

    def test_never_repeated(self):
        self.org.outcomes = ["ignored"]  # accepted but not applied: uncertain
        self.assertEqual(self.run_auto()["ids"], "write_uncertain")
        self.assertEqual(self.run_auto()["ids"], "ids_write_needs_review")
        self.assertEqual(len(self.org.calls), 1)

    def test_a_second_run_after_success_changes_nothing(self):
        self.run_auto()
        calls = len(self.org.calls)
        self.assertEqual(self.run_auto(), {"ids": "already_present", "stage": "already_present"})
        self.assertEqual(len(self.org.calls), calls)

    def test_a_salesforce_session_not_ready_sends_nothing(self):
        self.assertEqual(self.run_auto(sf_ready=False)["ids"], "salesforce_session_not_ready")
        self.assertEqual(self.org.calls, [])


class ProgressTests(base.Base):
    def progress(self, follow=None):
        history = [{**base.VERIFIED}]
        with mock.patch.object(create_ui, "load_co_records", return_value=history):
            return fu.progress(self.parent, base.CO, "2026-10-22T00:00:00Z", running=False, follow=follow)

    def test_no_step_waits_for_a_review_any_more(self):
        self.assertFalse(any("review" in label.casefold() for label in fu.PROGRESS_STEPS))

    def test_a_follow_up_that_refused_before_writing_is_shown_as_failed(self):
        state = self.progress({"ids": "salesforce_session_not_ready", "stage": "skipped_ids_not_verified"})
        self.assertEqual(state["failed"], "salesforce_session_not_ready")
        self.assertFalse(state["done"])

    def test_while_the_follow_up_runs_the_ids_step_is_active(self):
        state = self.progress(None)
        self.assertIsNone(state["failed"])
        self.assertEqual(state["steps"][3][1], "active")

    def test_done_after_both_writes(self):
        fu.after_verified_create(self.inputs(), [base.VERIFIED], self.org.read, self.org.run, "surface-onboarding")
        state = self.progress({"ids": "written_verified", "stage": "written_verified"})
        self.assertTrue(state["done"])
        self.assertEqual(state["percent"], 100)


class ScanCardTests(unittest.TestCase):
    def test_a_case_3_co_is_told_its_scan_is_checked_by_hand(self):
        history = [{"outcome": "readback_verified", "route": "case_3_combined_baseline", "co_reference": "CO-0757",
                    "recorded_at": "2026-11-02T22:49:47Z"}]
        with mock.patch.object(create_ui, "load_co_records", return_value=history), \
                mock.patch.object(create_ui, "co_status", return_value={"code": create_ui.CREATED_VERIFIED}):
            self.assertEqual(create_ui.scan_check_problem(mock.sentinel.parent, "CO-0757"), "case3_scan_manual")
        self.assertIn("by hand", create_ui.GATE_TEXT["case3_scan_manual"])


class DashboardWorkerTests(unittest.TestCase):
    def test_a_case_3_run_starts_its_automatic_follow_up(self):
        started = []
        with mock.patch.object(dashboard, "_launch_production_create", return_value=object()), \
                mock.patch.object(dashboard, "Thread", side_effect=lambda target, args, daemon: started.append(target) or
                                  mock.Mock()):
            dashboard._launch_production_create_followed("CO-0757", "rev", {}, route="case_3_combined_baseline")
        self.assertEqual(started, [dashboard._follow_case3_create])
        dashboard.PRODUCTION_RUNS.pop("CO-0757", None)

    def test_a_ce_only_run_keeps_its_own_follow_up(self):
        started = []
        with mock.patch.object(dashboard, "_launch_production_create", return_value=object()), \
                mock.patch.object(dashboard, "Thread", side_effect=lambda target, args, daemon: started.append(target) or
                                  mock.Mock()):
            dashboard._launch_production_create_followed("CO-0831", "rev", {})
        self.assertEqual(started, [dashboard._follow_production_create])
        dashboard.PRODUCTION_RUNS.pop("CO-0831", None)

    def test_the_worker_records_a_failure_instead_of_raising(self):
        dashboard.PRODUCTION_RUNS["CO-0757"] = {"since": "x", "follow": None, "route": "case_3_combined_baseline"}
        self.addCleanup(dashboard.PRODUCTION_RUNS.pop, "CO-0757", None)
        with mock.patch.object(dashboard, "_create_ui_inputs", side_effect=RuntimeError("boom")):
            dashboard._follow_case3_create("CO-0757", mock.Mock())
        self.assertEqual(dashboard.PRODUCTION_RUNS["CO-0757"]["follow"]["ids"], "follow_up_failed")


if __name__ == "__main__":
    unittest.main()
