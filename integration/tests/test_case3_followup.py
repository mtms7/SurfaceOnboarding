"""Path B step (d): the guarded Case 3 follow-up after a verified create (owner go 2026-10-22).

Two Salesforce writes on ONE Customer_Onboarding__c, behind a review step and a separate owner confirmation:
(1) the empty Surface_Account_ID__c / Account_UUID__c from the newest verified Case 3 readback, then (2) Request Approved ->
Account Scanning, only after (1) read back as verified. Hermetic: a fake org that parses the fixed argument list; no Salesforce,
no network, no browser. The production lock constants must both be True for any of it (patched here, never changed).
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import case3_followup as fu
from integration.onboarding import production_create_record as records
from integration.onboarding import salesforce_stage_writeback as stage_wb
from integration.onboarding import session_readiness as readiness
from integration.onboarding.production_create_ui import OneTimeTokens

NOW = datetime(2026, 10, 22, 14, 0, 0, tzinfo=timezone.utc)
CO = "CO-0802"
SF_ID = "a0X000000000001AAA"
REV = "2026-10-22T13:00:00.000+0000"
ACCOUNT_ID = "0123456789abcdef01234567"
UUID = "0123456789abcdef0123456789abcdef"
CASE3 = "case_3_combined_baseline"
ROW = {"Id": SF_ID, "Name": CO, "Onboarding_Product__c": "Surface & Credential Exposure",
       "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Stage__c": "Request Approved",
       "Surface_Account_ID__c": None, "Account_UUID__c": None, "LastModifiedDate": REV}
VERIFIED = {"outcome": "readback_verified", "route": CASE3, "co_reference": CO, "account_id": ACCOUNT_ID,
            "account_uuid": UUID, "tenant_name": "Example Tenant", "recorded_at": "2026-10-22T12:00:00Z"}


class FakeOrg:
    """Holds one CO row; ``run`` parses the fixed argument list the guarded code sends."""

    def __init__(self, row=None, outcomes=()):
        self.row = dict(ROW if row is None else row)
        self.calls = []
        self.outcomes = list(outcomes)  # per call: True (apply), False (reject), "ignored" (accepted, not applied), "raise"
        self.log_check = None

    def read(self):
        return dict(self.row)

    def run(self, argv):
        if self.log_check is not None:
            self.log_check()
        self.calls.append(list(argv))
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if outcome == "raise":
            raise OSError("boom")
        if outcome is False:
            return False
        if outcome is True:
            values = argv[argv.index("--values") + 1]
            if values.startswith(stage_wb.STAGE_FIELD):
                self.row[stage_wb.STAGE_FIELD] = values.split("=", 1)[1].strip("'")
            else:
                for pair in values.split(" "):
                    name, value = pair.split("=", 1)
                    self.row[name] = value
            self.row["LastModifiedDate"] = "2026-10-22T14:00:01.000+0000"  # any write moves the revision
        return True

    def values(self):
        return [call[call.index("--values") + 1] for call in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.parent = Path(tmp.name)
        self.tokens = OneTimeTokens()
        for patch in (mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", True),
                      mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True)):
            patch.start()
            self.addCleanup(patch.stop)
        self.org = FakeOrg()

    def inputs(self, **over):
        args = dict(reference=CO, records_parent=self.parent, now=NOW, vm=False, sf_ready=True)
        args.update(over)
        return fu.FollowupInputs(**args)

    def review(self, row="org", verified=(VERIFIED,), **over):
        return fu.review(self.inputs(**over), list(verified), self.org.read() if row == "org" else row, tokens=self.tokens)

    def confirm(self, token, verified=(VERIFIED,), typed=CO, authorized="1", **over):
        values = {"reference": CO, "nonce": token, "confirm_reference": typed, "case3_followup_authorized": authorized}
        return fu.confirm(values, self.inputs(**over), list(verified), self.org.read, self.org.run, "surface-onboarding",
                          tokens=self.tokens)

    def go(self, **over):
        code, view = self.review()
        self.assertEqual(code, "ready", view)
        return self.confirm(view["token"], **over)

    def log_text(self):
        folder = self.parent / records.RECORDS_SUBDIR
        return "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.jsonl")) if folder.exists() else ""


class ReviewTests(Base):
    def test_a_ready_review_shows_fields_stage_and_only_the_last_four_characters(self):
        code, view = self.review()
        self.assertEqual(code, "ready")
        self.assertEqual(view["fields"], ["Surface_Account_ID__c", "Account_UUID__c"])
        self.assertEqual((view["id_tail"], view["uuid_tail"]), (ACCOUNT_ID[-4:], UUID[-4:]))
        self.assertEqual((view["stage_from"], view["stage_to"]), ("Request Approved", "Account Scanning"))
        self.assertTrue(view["token"])
        text = json.dumps(view)
        self.assertNotIn(ACCOUNT_ID, text)
        self.assertNotIn(UUID, text)
        self.assertEqual(self.org.calls, [])

    def test_the_locks_refuse_everything(self):
        for name, reason in (("PRODUCTION_RUNNER_SUPPORTED", "production_runner_not_supported"),
                             ("PRODUCTION_DRY_RUN_SUPPORTED", "production_dry_run_not_supported")):
            with self.subTest(lock=name), mock.patch.object(readiness, name, False):
                self.assertEqual(self.review()[0], reason)
                self.assertEqual(self.confirm("x")["reason"], reason)
        self.assertEqual(self.org.calls, [])

    def test_the_vm_and_a_missing_session_refuse(self):
        self.assertEqual(self.review(vm=True)[0], "vm_read_only")
        self.assertEqual(self.review(sf_ready=False)[0], "salesforce_session_not_ready")
        self.assertEqual(self.review(row=None)[0], "salesforce_session_not_ready")

    def test_review_refusals(self):
        ce = dict(ROW, Onboarding_Product__c="Credential Exposure")
        cases = (("tenant_not_verified", dict(verified=())),
                 ("tenant_not_verified", dict(verified=(dict(VERIFIED, outcome="created_verified"),))),
                 ("tenant_not_verified", dict(verified=(dict(VERIFIED, co_reference="CO-0001"),))),
                 ("tenant_not_verified", dict(verified=(dict(VERIFIED, route="case_2_new_ce_only"),))),
                 ("tenant_ids_invalid", dict(verified=(dict(VERIFIED, account_uuid="xyz"),))),
                 ("tenant_ids_invalid", dict(verified=(dict(VERIFIED, account_id="12"),))),
                 ("route_not_case3_new", dict(row=ce)),
                 ("stage_not_expected", dict(row=dict(ROW, Onboarding_Stage__c="Scan Completed Successfully"))),
                 ("different_value", dict(row=dict(ROW, Surface_Account_ID__c="f" * 24))),
                 ("co_row_unreadable", dict(row={k: v for k, v in ROW.items() if k != "Account_UUID__c"})),
                 ("revision_changed", dict(row=dict(ROW, LastModifiedDate=None))))
        for reason, kwargs in cases:
            with self.subTest(reason=reason, case=sorted(kwargs)):
                self.assertEqual(self.review(**kwargs)[0], reason)
        self.assertEqual(self.org.calls, [])

    def test_the_newest_verified_record_counts(self):
        older = dict(VERIFIED, recorded_at="2026-10-21T12:00:00Z", account_id="f" * 24)
        code, view = self.review(verified=(older, VERIFIED))
        self.assertEqual((code, view["id_tail"]), ("ready", ACCOUNT_ID[-4:]))

    def test_a_finished_co_is_reported_complete(self):
        self.org.row.update({"Surface_Account_ID__c": ACCOUNT_ID, "Account_UUID__c": UUID,
                             "Onboarding_Stage__c": "Account Scanning"})
        self.assertEqual(self.review()[0], "already_complete")


class ConfirmTests(Base):
    def test_the_happy_path_writes_the_two_id_fields_then_the_stage_and_nothing_else(self):
        result = self.go()
        self.assertEqual(result, {"result": "completed", "ids": "written_verified", "stage": "written_verified"})
        self.assertEqual(len(self.org.calls), 2)
        self.assertEqual(self.org.values(), [f"Surface_Account_ID__c={ACCOUNT_ID} Account_UUID__c={UUID}",
                                             "Onboarding_Stage__c='Account Scanning'"])
        for call in self.org.calls:
            self.assertEqual(call[:5], ["sf", "data", "update", "record", "--sobject"])
            self.assertIn(SF_ID, call)
            self.assertEqual(call[-2:], ["--target-org", "surface-onboarding"])
        self.assertEqual((self.org.row["Surface_Account_ID__c"], self.org.row["Account_UUID__c"],
                          self.org.row["Onboarding_Stage__c"]), (ACCOUNT_ID, UUID, "Account Scanning"))

    def test_the_result_lines_name_intent_and_outcome_and_never_the_full_ids(self):
        self.go()
        lines = [json.loads(line) for line in self.log_text().splitlines()]
        self.assertEqual([(l["step"], l["result"]) for l in lines],
                         [("ids", "write_started"), ("ids", "written_verified"),
                          ("stage", "write_started"), ("stage", "written_verified")])
        text = self.log_text()
        self.assertNotIn(ACCOUNT_ID, text)
        self.assertNotIn(UUID, text)
        self.assertTrue(all(l["co_reference"] == CO for l in lines))

    def test_the_intent_line_is_on_disk_before_each_write(self):
        seen = []
        self.org.log_check = lambda: seen.append(self.log_text().count("write_started"))
        self.go()
        self.assertEqual(seen, [1, 2])

    def test_a_failed_intent_record_sends_nothing(self):
        code, view = self.review()
        with mock.patch.object(fu, "_record", side_effect=OSError("disk")):
            result = self.confirm(view["token"])
        self.assertEqual(result, {"result": "refused", "reason": "record_write_failed"})
        self.assertEqual(self.org.calls, [])

    def test_the_typed_reference_and_the_authorization_are_required(self):
        code, view = self.review()
        self.assertEqual(self.confirm(view["token"], typed="CO-0001")["reason"], "typed_reference_mismatch")
        code, view = self.review()
        self.assertEqual(self.confirm(view["token"], authorized="")["reason"], "authorization_missing")
        self.assertEqual(self.org.calls, [])

    def test_the_token_is_single_use_and_bound_to_the_review(self):
        code, view = self.review()
        self.assertEqual(self.confirm("wrong")["reason"], "confirmation_invalid")  # a wrong attempt also discards the stored one
        self.assertEqual(self.confirm(view["token"])["reason"], "confirmation_invalid")
        code, view = self.review()
        self.org.row["LastModifiedDate"] = "2026-10-22T13:30:00.000+0000"  # the CO changed after the review
        self.assertEqual(self.confirm(view["token"])["reason"], "confirmation_invalid")
        code, view = self.review()
        self.assertEqual(self.confirm(view["token"], verified=(dict(VERIFIED, account_id="e" * 24),))["reason"],
                         "confirmation_invalid")  # different ids than the ones reviewed
        self.assertEqual(self.org.calls, [])

    def test_a_rejected_id_write_stops_before_the_stage_and_is_never_retried_blindly(self):
        self.org.outcomes = [False]
        self.assertEqual(self.go(), {"result": "stopped", "ids": "write_rejected", "stage": "skipped_ids_not_verified"})
        self.assertEqual(len(self.org.calls), 1)
        self.assertEqual(self.org.row["Onboarding_Stage__c"], "Request Approved")
        self.assertEqual(self.review()[0], "ids_write_already_recorded")

    def test_an_unverified_or_uncertain_id_write_stops_and_blocks_a_rerun_until_reviewed(self):
        for outcome in ("ignored", "raise"):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.org.outcomes = [outcome]
                self.assertEqual(self.go(), {"result": "stopped", "ids": "write_uncertain", "stage": "skipped_ids_not_verified"})
                self.assertEqual(len(self.org.calls), 1)
                self.assertEqual(self.review()[0], "ids_write_needs_review")

    def test_a_stage_write_that_fails_is_recorded_and_blocks_a_second_stage_write(self):
        self.org.outcomes = [True, "ignored"]
        self.assertEqual(self.go(), {"result": "stopped", "ids": "written_verified", "stage": "write_uncertain"})
        self.assertEqual(self.org.row["Onboarding_Stage__c"], "Request Approved")
        code, _view = self.review()
        self.assertEqual(code, "stage_write_needs_review")
        self.assertEqual(len(self.org.calls), 2)

    def test_a_rejected_stage_write_is_never_retried_even_by_a_direct_post(self):
        self.org.outcomes = [True, False]
        self.assertEqual(self.go(), {"result": "stopped", "ids": "written_verified", "stage": "write_rejected"})
        self.assertEqual(self.review()[0], "stage_write_needs_review")
        self.assertEqual(self.confirm("anything")["reason"], "stage_write_needs_review")
        self.assertEqual(len(self.org.calls), 2)

    def test_the_locks_and_the_vm_are_checked_before_any_salesforce_read(self):
        reads = []
        values = {"reference": CO, "nonce": "x", "confirm_reference": CO, "case3_followup_authorized": "1"}
        for patch in (mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False),):
            with patch:
                result = fu.confirm(values, self.inputs(), [VERIFIED], lambda: reads.append(1) or dict(ROW), self.org.run, "alias",
                                    tokens=self.tokens)
                self.assertEqual(result["reason"], "production_runner_not_supported")
                self.assertEqual(fu.build_lock(self.inputs()), "production_runner_not_supported")
        result = fu.confirm(values, self.inputs(vm=True), [VERIFIED], lambda: reads.append(1) or dict(ROW), self.org.run, "alias",
                            tokens=self.tokens)
        self.assertEqual((result["reason"], reads), ("vm_read_only", []))

    def test_ids_already_on_the_co_skip_the_id_write_and_only_the_stage_moves(self):
        self.org.row.update({"Surface_Account_ID__c": ACCOUNT_ID, "Account_UUID__c": UUID})
        self.assertEqual(self.go(), {"result": "completed", "ids": "already_present", "stage": "written_verified"})
        self.assertEqual(self.org.values(), ["Onboarding_Stage__c='Account Scanning'"])

    def test_a_stage_that_is_already_account_scanning_is_not_written_again(self):
        self.org.row.update({"Surface_Account_ID__c": ACCOUNT_ID, "Account_UUID__c": None, "Onboarding_Stage__c": "Account Scanning"})
        self.assertEqual(self.go(), {"result": "completed", "ids": "written_verified", "stage": "already_present"})
        self.assertEqual(self.org.values(), [f"Account_UUID__c={UUID}"])  # only the empty field is written

    def test_the_row_changing_between_review_and_write_refuses(self):
        code, view = self.review()
        original = self.org.read

        def changing():
            row = original()
            self.org.row["LastModifiedDate"] = "2026-10-22T13:59:00.000+0000"
            return row
        values = {"reference": CO, "nonce": view["token"], "confirm_reference": CO, "case3_followup_authorized": "1"}
        result = fu.confirm(values, self.inputs(), [VERIFIED], changing, self.org.run, "surface-onboarding", tokens=self.tokens)
        self.assertEqual(result["result"], "refused")
        self.assertEqual(self.org.calls, [])


class IsolationTests(unittest.TestCase):
    def test_the_ce_only_stage_plan_still_refuses_a_case_3_co(self):
        self.assertEqual(stage_wb.plan(ROW, "Account Scanning").reason, "route_not_ce_only_new")

    def test_the_case_3_stage_plan_allows_exactly_one_move(self):
        self.assertEqual(fu.stage_plan(ROW).status, "ready_to_write")
        for stage in ("Account Scanning",):
            self.assertEqual(fu.stage_plan(dict(ROW, Onboarding_Stage__c=stage)).status, "already_present")
        for stage in (None, "", "New", "Scan Completed Successfully", "Onboarding Completed", "User Created"):
            self.assertEqual(fu.stage_plan(dict(ROW, Onboarding_Stage__c=stage)).status, "blocked", stage)
        self.assertEqual(fu.stage_plan(dict(ROW, Onboarding_Product__c="Credential Exposure")).status, "blocked")
        self.assertEqual(fu.stage_plan({}).status, "blocked")

    def test_the_locks_stay_false(self):
        self.assertIs(readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # dry run only unlocked for the CO-0802 dry run; the create stays locked


if __name__ == "__main__":
    unittest.main()
