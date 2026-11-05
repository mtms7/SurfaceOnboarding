"""Owner report 2026-11-04 (CO-0816): a CE-only CO at User Created with an Account UUID (tenant in the EU clone) showed
"Not checked" with the wrong reason "Production inputs are only prepared for CE-only COs", plus "Not created" and
"overdue 3 days". Its plan cannot be built (no DealHub subscription) and the dashboard's identity read had failed, so the
match ran as production_match(None, ...). Now:

* the runner says "not checked / co_identity_unreadable" (honest reason) in that case;
* that result is never kept for the match TTL (the next page reads the CO again);
* the queue's own batched Salesforce read records whether the CO holds a UUID (a bool, never the value), and a UUID plus a
  stage past approval while the clone check could not decide shows "Onboarded (UUID in Salesforce), not verified", done.

Hermetic: fake reads, a fake clone; nothing reads Salesforce.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_match as match_module
from integration.onboarding import production_views as views
from integration.tests.test_production_first_dashboard import MATCHES, ROWS, STARTS, Env, approval, match, page_of

CO, PRODUCT, KIND = "CO-0816", "Credential Exposure", "New Product Onboarding"
SNAPSHOT = {"tenants": [{"account_name": "Other", "account_uuid": "b" * 32, "alternate_domains": []}], "captured_at": None}


class RunnerReasonTests(unittest.TestCase):
    def run_for(self, identity, vm=False, snapshot=SNAPSHOT):
        full = mock.Mock(return_value={"status": "not_checked", "reason": "co_source_unreadable"})
        with patch.object(runner, "_production_expectation", side_effect=RuntimeError("ce_subscription_unavailable")), \
                patch.object(runner, "_identity_for", return_value=identity), \
                patch.object(runner, "clone_root", return_value=Path("unused")), \
                patch.object(match_module, "load_snapshot", return_value=snapshot), \
                patch.object(match_module, "production_match", full), \
                patch.object(runner, "_snapshot_account_uuid", return_value=None), \
                patch.object(runner, "_snapshot_identity", return_value=None), \
                patch.object(runner, "_apply_verified_tenant", side_effect=lambda result, *_: result), \
                patch("integration.onboarding.state_paths.vm_mode", return_value=vm):
            return runner.production_match_for(CO, PRODUCT, KIND), full

    def test_no_plan_and_a_failed_identity_read_is_the_honest_reason(self):
        found, full = self.run_for(None)
        self.assertEqual((found["status"], found["reason"]), ("not_checked", "co_identity_unreadable"))
        full.assert_not_called()  # never production_match(None, ...)
        chip = views.production_check(found)
        self.assertEqual(chip["label"], "Not checked")
        self.assertTrue(chip["blocked"])
        self.assertNotIn("only prepared for CE-only", chip["reason"])
        self.assertIn("Salesforce", chip["reason"])

    def test_a_readable_identity_with_the_uuid_still_finds_the_tenant(self):
        snapshot = {"tenants": [{"account_name": "Ministry - CE Only", "account_uuid": "a" * 32, "alternate_domains": [],
                                 "account_domain": "mof.example"}], "captured_at": None}
        found, _ = self.run_for({"names": ("Ministry",), "domains": ("mof.example",), "uuid": "a" * 32}, snapshot=snapshot)
        self.assertEqual(found["status"], "onboarded")

    def test_the_vm_path_is_unchanged(self):
        found, full = self.run_for(None, vm=True)
        self.assertEqual(found["reason"], "production_inputs_not_prepared")
        full.assert_called_once()

    def test_the_old_wrong_text_is_gone(self):
        self.assertNotIn("only prepared for CE-only", views.NOT_CHECKED_TEXT["production_inputs_not_prepared"])


class MatchNotKeptTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        token = dashboard._display_reads.set(True)
        self.addCleanup(dashboard._display_reads.reset, token)

    def results(self, *values):
        calls = iter(values)
        return patch.object(dashboard, "production_match_for", side_effect=lambda *a: dict(next(calls)))

    def test_a_failed_identity_read_is_shown_once_then_read_again(self):
        unread = {"status": "not_checked", "reason": "co_identity_unreadable"}
        onboarded = {"status": "onboarded", "reason": "x", "candidates": []}
        with self.results(unread, onboarded) as fake, patch.object(dashboard, "_match_stamp", return_value="s"), \
                patch.object(dashboard, "UNSTAMPED_MATCH_ALLOWED", True):
            self.assertEqual(dashboard._match_of(CO, PRODUCT, KIND)["reason"], "co_identity_unreadable")
            self.assertEqual(dashboard._match_of(CO, PRODUCT, KIND)["status"], "onboarded")
            self.assertEqual(fake.call_count, 2)

    def test_a_good_match_is_still_cached(self):
        onboarded = {"status": "onboarded", "reason": "x", "candidates": []}
        with self.results(onboarded, onboarded) as fake, patch.object(dashboard, "_match_stamp", return_value="s"):
            dashboard._match_of(CO, PRODUCT, KIND)
            dashboard._match_of(CO, PRODUCT, KIND)
            self.assertEqual(fake.call_count, 1)


class QueueUuidReadTests(unittest.TestCase):
    def setUp(self):
        dashboard.QUEUE_UUID_SET.clear()
        self.addCleanup(dashboard.QUEUE_UUID_SET.clear)

    def read(self, records):
        response = {"status": 0, "result": {"records": records}}
        with patch.object(dashboard, "sf_json", return_value=response) as fake, \
                patch.object(dashboard, "vm_mode", return_value=False):
            dashboard.subscription_summaries(tuple(record["Name"] for record in records))
        return fake.call_args[0][0]

    def test_the_same_batched_read_records_only_whether_a_uuid_is_set(self):
        argv = self.read([{"Name": "CO-0816", "Onboarding_Comments__c": None, "Account_UUID__c": "a" * 32},
                          {"Name": "CO-0835", "Onboarding_Comments__c": None, "Account_UUID__c": None},
                          {"Name": "CO-0836", "Onboarding_Comments__c": None, "Account_UUID__c": "  "}])
        self.assertIn("Account_UUID__c", " ".join(argv))
        self.assertEqual(dashboard.QUEUE_UUID_SET, {"CO-0816": True, "CO-0835": False, "CO-0836": False})
        self.assertNotIn("a" * 32, repr(dashboard.QUEUE_UUID_SET))

    def test_a_record_without_the_field_stays_unknown(self):
        dashboard.QUEUE_UUID_SET["CO-0816"] = True
        self.read([{"Name": "CO-0816", "Onboarding_Comments__c": None}])
        self.assertNotIn("CO-0816", dashboard.QUEUE_UUID_SET)


class RuleTests(unittest.TestCase):
    def test_onboarded_unverified_rule(self):
        for code, stage, uuid_set, expected in (
                ("not_checked", "User Created", True, True), ("unavailable", "Account Scanning", True, True),
                ("clone_stale", "Onboarding Completed", True, True), ("not_checked", "Request Approved", True, False),
                ("not_checked", "User Created", False, False), ("not_checked", "User Created", None, False),
                ("duplicate", "User Created", True, False), ("live_paid", "User Created", True, False),
                ("clear", "User Created", True, False), ("ambiguous", "User Created", True, False)):
            self.assertEqual(dashboard.onboarded_unverified({"code": code}, stage, uuid_set), expected, (code, stage, uuid_set))
        self.assertFalse(dashboard.onboarded_unverified(None, "User Created", True))


class QueueRowTests(unittest.TestCase):
    ROW = {"Name": "CO-0816", "Account__r.Name": "Ministry Example", "Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT,
           "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE, "Submission_Date__c": "2026-10-20",
           "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "User Created"}

    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.tmp.cleanup)
        self.env.write_approvals(approval())

    def row(self, uuid_set, stage="User Created"):
        matches = {**MATCHES, "CO-0816": match("not_checked", reason="co_identity_unreadable")}
        starts = {**STARTS, "CO-0816": "2026-10-13"}  # in the past: was "overdue"
        with patch.dict(dashboard.QUEUE_UUID_SET, {"CO-0816": uuid_set} if uuid_set is not None else {}, clear=True):
            status, html = page_of("/", self.env, rows=ROWS + [dict(self.ROW, Onboarding_Stage__c=stage)], matches=matches,
                                   starts=starts)
        self.assertEqual(status, 200)
        start = html.index(">CO-0816<")
        return html[html.rindex("<tr", 0, start):html.index("</tr>", start)]

    def test_uuid_and_user_created_is_onboarded_not_verified_and_done(self):
        row = self.row(True)
        self.assertIn("Onboarded (UUID in Salesforce), not verified", row)
        self.assertIn("Created by hand", row)
        self.assertIn("<span class='muted'>done</span>", row)
        self.assertNotIn("overdue", row)
        self.assertNotIn("only prepared for CE-only", row)

    def test_without_a_uuid_it_stays_not_checked(self):
        for uuid_set in (False, None):
            with self.subTest(uuid_set=uuid_set):
                row = self.row(uuid_set)
                self.assertIn("Not checked", row)
                self.assertNotIn("not verified", row)
                self.assertIn("overdue", row)

    def test_approved_only_stays_not_checked(self):
        row = self.row(True, stage="Request Approved")
        self.assertNotIn("not verified", row)


if __name__ == "__main__":
    unittest.main()
