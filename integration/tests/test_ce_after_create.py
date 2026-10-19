"""Owner decision 2026-10-19 (after the CO-0798 production create): for a CE-only New onboarding the Account UUID write
runs AUTOMATICALLY right after a verified create, and the Salesforce stage then moves Request Approved -> Account
Scanning. CE-only never uses "User Created": later, after the CE scan is confirmed finished, Account Scanning -> Scan
Completed Successfully -> Onboarding Completed.

Every stage write is one fixed field on one CO, only from the exact expected stage, only on the CE-only route, only after
the UUID was written and verified, read back after the write, recorded before and after, and never retried after an
uncertain result. Hermetic: fake Salesforce reads and writes, temporary folders.
"""

import json
import unittest
from unittest import mock

from integration.onboarding import production_create_ui as ui
from integration.onboarding import salesforce_stage_writeback as sw
from integration.tests import test_production_create_ui as base

CO, UUID = base.CO, base.UUID


def sf_row(**over):
    return base.row(**{"Onboarding_Stage__c": "Request Approved", **over})


class StagePlanTests(unittest.TestCase):
    def test_the_ce_only_flow_never_uses_user_created(self):
        self.assertEqual(sw.CE_ONLY_FLOW, (("Request Approved", "Account Scanning"),
                                           ("Account Scanning", "Scan Completed Successfully"),
                                           ("Scan Completed Successfully", "Onboarding Completed")))
        self.assertNotIn("User Created", [stage for pair in sw.CE_ONLY_FLOW for stage in pair])

    def test_a_ready_plan_needs_the_exact_from_stage_and_the_ce_route(self):
        plan = sw.plan(sf_row(), "Account Scanning")
        self.assertEqual((plan.status, plan.from_stage, plan.to_stage), ("ready_to_write", "Request Approved", "Account Scanning"))
        self.assertEqual(sw.plan(sf_row(Onboarding_Stage__c="Account Scanning"), "Account Scanning").status, "already_present")
        for row, reason in ((sf_row(Onboarding_Stage__c="New"), "stage_not_expected"),
                            (sf_row(Onboarding_Stage__c="User Created"), "stage_not_expected"),
                            (sf_row(Onboarding_Product__c="Surface"), "route_not_ce_only_new"),
                            (sf_row(Onboarding_Type__c="Renewal"), "route_not_ce_only_new"),
                            ({"Id": "x"}, "row_unreadable"), (None, "row_unreadable")):
            plan = sw.plan(row, "Account Scanning")
            self.assertEqual((plan.status, plan.reason), ("blocked", reason), row)
        self.assertEqual(sw.plan(sf_row(), "User Created").reason, "transition_not_allowed")
        self.assertEqual(sw.plan(sf_row(), "Onboarding Completed").reason, "stage_not_expected")

    def test_the_update_is_one_fixed_field_argv(self):
        argv = sw.update_argv(sw.plan(sf_row(), "Account Scanning"), "a0B5g00000AbCdEFGH", "surface-onboarding")
        self.assertEqual(argv, ["sf", "data", "update", "record", "--sobject", "Customer_Onboarding__c", "--record-id",
                                "a0B5g00000AbCdEFGH", "--values", "Onboarding_Stage__c='Account Scanning'",
                                "--target-org", "surface-onboarding"])
        for bad in ((sw.plan(sf_row(Onboarding_Stage__c="New"), "Account Scanning"), "a0B5g00000AbCdEFGH", "surface-onboarding"),
                    (sw.plan(sf_row(), "Account Scanning"), "x' --values Name=y", "surface-onboarding"),
                    (sw.plan(sf_row(), "Account Scanning"), "a0B5g00000AbCdEFGH", "org; rm")):
            with self.assertRaises(ValueError):
                sw.update_argv(*bad)

    def test_read_after_write(self):
        self.assertEqual(sw.verify_after_write(sf_row(Onboarding_Stage__c="Account Scanning"), "Account Scanning"), "verified")
        self.assertEqual(sw.verify_after_write(sf_row(), "Account Scanning"), "mismatch")
        self.assertEqual(sw.verify_after_write({"Id": "x"}, "Account Scanning"), "unreadable")


class AfterCreateTests(base.Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record("readback_verified", account_uuid=UUID)
        self.salesforce = sf_row()
        self.writes = []

    def read(self):
        return dict(self.salesforce)

    def run_argv(self, argv):
        self.writes.append(argv)
        value = argv[argv.index("--values") + 1]
        field, _, raw = value.partition("=")
        self.salesforce[field] = raw.strip("'")
        return True

    def follow(self, **kw):
        return ui.after_verified_create(self.inputs(**kw), self.read, self.run_argv, "surface-onboarding")

    def test_a_verified_create_writes_the_uuid_then_moves_to_account_scanning(self):
        self.assertEqual(self.follow(), {"uuid": "written_verified", "stage": "written_verified"})
        self.assertEqual([a[a.index("--values") + 1] for a in self.writes],
                         ["Account_UUID__c=" + UUID, "Onboarding_Stage__c='Account Scanning'"])
        self.assertEqual(ui.last_uuid_write(self.dir, CO), "written_verified")
        self.assertEqual(ui.last_stage_write(self.dir, CO), ("Account Scanning", "written_verified"))
        lines = (self.dir / "production-create-records" / ui.STAGE_WRITES_FILE).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["result"] for l in lines], ["write_started", "written_verified"])
        self.assertNotIn(UUID, "".join(lines))

    def test_it_runs_once_a_second_follow_writes_nothing(self):
        self.follow()
        self.writes.clear()
        self.assertEqual(self.follow(), {"uuid": "uuid_write_already_recorded", "stage": "already_present"})
        self.assertEqual(self.writes, [])

    def test_no_stage_move_unless_the_uuid_write_verified(self):
        def reject(argv):
            self.writes.append(argv)
            return False
        result = ui.after_verified_create(self.inputs(), self.read, reject, "surface-onboarding")
        self.assertEqual(result, {"uuid": "write_rejected", "stage": "skipped_uuid_not_verified"})
        self.assertEqual(len(self.writes), 1)
        self.assertIsNone(ui.last_stage_write(self.dir, CO))

    def test_an_unexpected_stage_is_left_alone(self):
        self.salesforce["Onboarding_Stage__c"] = "New"
        self.assertEqual(self.follow(), {"uuid": "written_verified", "stage": "stage_not_expected"})
        self.assertEqual(len(self.writes), 1)  # the UUID only

    def test_an_uncertain_stage_write_is_recorded_and_never_retried(self):
        calls = []

        def flaky(argv):
            calls.append(argv)
            if "Onboarding_Stage__c" in argv[argv.index("--values") + 1]:
                return None  # timeout / unknown
            return self.run_argv(argv)
        self.assertEqual(ui.after_verified_create(self.inputs(), self.read, flaky, "surface-onboarding")["stage"],
                         "write_uncertain")
        self.assertEqual(ui.last_stage_write(self.dir, CO), ("Account Scanning", "write_uncertain"))
        calls.clear()
        self.assertEqual(ui.after_verified_create(self.inputs(), self.read, flaky, "surface-onboarding")["stage"],
                         "stage_write_needs_review")
        self.assertEqual([c for c in calls if "Onboarding_Stage__c" in c[c.index("--values") + 1]], [])

    def test_on_the_vm_nothing_runs(self):
        self.assertEqual(ui.after_verified_create(self.inputs(vm=True), self.read, self.run_argv, "surface-onboarding"),
                         {"uuid": "vm_read_only", "stage": "skipped_uuid_not_verified"})
        self.assertEqual(self.writes, [])

    def test_a_locked_build_writes_nothing(self):
        with mock.patch.object(ui.readiness, "PRODUCTION_RUNNER_SUPPORTED", False):
            self.assertEqual(self.follow()["uuid"], "production_runner_not_supported")
        self.assertEqual(self.writes, [])


class NoVerifiedCreateTests(base.Base):
    def test_nothing_runs_without_a_verified_create(self):
        self.unlocked()
        writes = []
        for outcome in ("created_unverified", "uncertain_submit", "refused"):
            self.record(outcome)
            result = ui.after_verified_create(self.inputs(), lambda: sf_row(), lambda argv: writes.append(argv) or True,
                                              "surface-onboarding")
            self.assertEqual(result, {"uuid": "readback_not_verified", "stage": "skipped_uuid_not_verified"}, outcome)
        self.assertEqual(writes, [])


class LaterStagesTests(base.Base):
    """Account Scanning -> Scan Completed Successfully -> Onboarding Completed, only after a confirmed finished scan."""

    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record("readback_verified", account_uuid=UUID)
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", base.NOW)
        self.salesforce = sf_row(Onboarding_Stage__c="Account Scanning", Account_UUID__c=UUID)
        self.writes = []

    def read(self):
        return dict(self.salesforce)

    def run_argv(self, argv):
        self.writes.append(argv)
        self.salesforce["Onboarding_Stage__c"] = argv[argv.index("--values") + 1].partition("=")[2].strip("'")
        return True

    def test_a_confirmed_finished_scan_completes_the_onboarding_in_two_steps(self):
        result = ui.after_scan_finished(self.inputs(), self.read, self.run_argv, "surface-onboarding", scan_state="scan_completed")
        self.assertEqual(result, ["written_verified", "written_verified"])
        self.assertEqual(self.salesforce["Onboarding_Stage__c"], "Onboarding Completed")
        self.assertEqual(ui.last_stage_write(self.dir, CO), ("Onboarding Completed", "written_verified"))

    def test_any_other_scan_state_writes_nothing(self):
        for state in ("scan_started", "no_scan", "unrecognized", "scan_failed", None):
            self.assertEqual(ui.after_scan_finished(self.inputs(), self.read, self.run_argv, "surface-onboarding",
                                                    scan_state=state), ["scan_not_finished"], state)
        self.assertEqual(self.writes, [])

    def test_the_uuid_must_be_on_the_co(self):
        self.salesforce["Account_UUID__c"] = None
        self.assertEqual(ui.after_scan_finished(self.inputs(), self.read, self.run_argv, "surface-onboarding",
                                                scan_state="scan_completed"), ["uuid_not_on_co"])
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
