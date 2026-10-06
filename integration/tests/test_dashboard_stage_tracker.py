from __future__ import annotations

import unittest
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from tools.serve_attended_open_onboardings_dashboard import (
    ONBOARDING_STAGES, derive_onboarding_stage, user_created_evidence,
)


class DeriveStageTests(unittest.TestCase):
    def stage(self, sf=None, approval=None, **evidence):
        return derive_onboarding_stage(sf, approval, **evidence)

    def test_every_stage_is_reached_by_its_proof(self):
        self.assertEqual(self.stage("New", "Pending")["index"], 0)
        self.assertEqual(self.stage("New", "Approved")["index"], 1)
        self.assertEqual(self.stage("New", "Approved", tenant_created=True)["index"], 2)
        self.assertEqual(self.stage("New", "Approved", tenant_created=True, scan_done=True)["index"], 3)
        self.assertEqual(self.stage("New", "Approved", tenant_created=True, scan_done=True, user_created=True)["index"], 4)
        self.assertEqual(self.stage("Onboarding Completed", "Approved", tenant_created=True, scan_done=True,
                                    user_created=True)["index"], 5)

    def test_completed_comes_only_from_salesforce(self):
        full = self.stage("User Created", "Approved", tenant_created=True, scan_done=True, user_created=True)
        self.assertEqual(full["stage"], "User Created")

    def test_missing_data_never_jumps_ahead(self):
        # A finished scan without a verified create stays at the last provable stage.
        self.assertEqual(self.stage("New", "Approved", scan_done=True)["index"], 1)
        # Users present but no finished scan: stays at Account Scanning.
        self.assertEqual(self.stage("New", "Approved", tenant_created=True, user_created=True)["index"], 2)
        # Evidence without approval never reaches Request Approved.
        self.assertEqual(self.stage("New", "Pending", tenant_created=True, scan_done=True)["index"], 0)
        # Unknown (None) or False user evidence stops the chain.
        for value in (None, False):
            self.assertEqual(self.stage("New", "Approved", tenant_created=True, scan_done=True,
                                        user_created=value)["index"], 3)
        # Nothing is known at all.
        self.assertEqual(self.stage(None, None)["index"], 0)
        self.assertEqual(self.stage("garbage", "Approved")["index"], 1)

    def test_salesforce_stage_counts_for_itself_and_marks_ahead_or_behind(self):
        ahead = self.stage("Request Approved", "Approved", tenant_created=True)
        self.assertEqual((ahead["index"], ahead["salesforce_index"], ahead["ahead"], ahead["behind"]), (2, 1, True, False))
        same = self.stage("Request Approved", "Approved")
        self.assertFalse(same["ahead"] or same["behind"])
        behind = self.stage("Scan Completed Successfully", "Approved")
        self.assertEqual(behind["index"], 3)  # Salesforce is the authority for its own stage
        self.assertFalse(behind["ahead"])
        unknown = self.stage(None, "Approved", tenant_created=True)
        self.assertFalse(unknown["ahead"])

    def test_stage_names_match_salesforce_path(self):
        self.assertEqual(ONBOARDING_STAGES, ("New", "Request Approved", "Account Scanning",
                                             "Scan Completed Successfully", "User Created", "Onboarding Completed"))


class UserCreatedEvidenceTests(unittest.TestCase):
    def test_dev_needs_operator_and_customer_user(self):
        both = user_created_evidence({"operator_assigned": True, "primary_user": {"present": True}}, "dev")
        self.assertIs(both["created"], True)
        no_user = user_created_evidence({"operator_assigned": True, "primary_user": {"present": False}}, "dev")
        self.assertIs(no_user["created"], False)
        self.assertEqual(no_user["missing"], ["customer user"])
        no_operator = user_created_evidence({"operator_assigned": False, "primary_user": {"present": True}}, "dev")
        self.assertEqual((no_operator["created"], no_operator["missing"]), (False, ["operator account"]))

    def test_unknown_parts_are_never_guessed(self):
        self.assertIsNone(user_created_evidence({"operator_assigned": None, "primary_user": {"present": True}}, "dev")["created"])
        self.assertIsNone(user_created_evidence({}, "dev")["created"])
        self.assertIsNone(user_created_evidence(None, "dev")["created"])
        # The production clone has no primary-user data: the customer part stays unknown.
        clone = user_created_evidence({"operator_assigned": True, "primary_user": {"present": False}}, "prod-clone")
        self.assertIsNone(clone["created"])
        self.assertEqual(clone["unknown"], ["customer user"])
        # A missing operator is still provable on the clone.
        self.assertIs(user_created_evidence({"operator_assigned": False}, "prod-clone")["created"], False)


class StepperRenderTests(unittest.TestCase):
    def state(self, index, **extra):
        return {"index": index, "salesforce_index": extra.pop("sf", index), "ahead": extra.pop("ahead", False),
                "evidence": extra.pop("evidence", None), "environment": "dev", "captured_at": "", **extra}

    def test_done_current_upcoming_with_accessible_text(self):
        html = dashboard._stage_tracker_html(self.state(2))
        self.assertIn("aria-label='Onboarding stage'", html)
        self.assertEqual(html.count("class='st done'"), 2)
        self.assertEqual(html.count("class='st current' aria-current='step'"), 1)
        self.assertEqual(html.count("class='st upcoming'"), 3)
        self.assertIn("Account Scanning<span class='sr'> — current stage</span>", html)
        self.assertIn("New<span class='sr'> — done</span>", html)
        self.assertIn("Onboarding Completed<span class='sr'> — upcoming</span>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("http", html)

    def test_completed_is_all_done(self):
        html = dashboard._stage_tracker_html(self.state(5))
        self.assertEqual(html.count("class='st done'"), 6)
        self.assertNotIn("aria-current", html)

    def test_ahead_of_salesforce_is_highlighted(self):
        html = dashboard._stage_tracker_html(self.state(2, sf=1, ahead=True))
        self.assertIn("Salesforce still shows: Request Approved", html)
        self.assertIn("nothing is written to Salesforce", html)
        self.assertNotIn("still shows", dashboard._stage_tracker_html(self.state(1, sf=1)))

    def test_missing_part_is_named(self):
        evidence = user_created_evidence({"operator_assigned": True, "primary_user": {"present": False}}, "dev")
        html = dashboard._stage_tracker_html(self.state(3, evidence=evidence))
        self.assertIn("operator account: present", html)
        self.assertIn("customer user: missing", html)
        self.assertIn("not checked", dashboard._stage_tracker_html(self.state(3)))


class DetailPageTrackerTests(unittest.TestCase):
    ROW = {"Name": "CO-0702", "Account_Name__c": "Acme", "Onboarding_Product__c": "Credential Exposure",
           "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Approval_Status__c": "Approved",
           "Onboarding_Stage__c": "Request Approved", "Main_Domain__c": "acme.example"}
    READBACK = {"surface_account_id": "A" * 20, "account_uuid": "a" * 32, "leonardo_state": "Account Scanning",
                "observed_on": "2026-10-06", "source": "Leonardo Development Details readback"}

    def render(self, readback, scan=None, tenant=None):
        with patch.object(dashboard, "attended_leonardo_readbacks", return_value=readback), \
                patch.object(dashboard, "attended_scan_statuses", return_value=scan or {}), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "_stage_tenant", return_value=(tenant, "dev" if tenant else "", "2026-10-06T08:00")), \
                patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce")):
            return dashboard.page_detail("CO-0702", dict(self.ROW))

    def test_tracker_is_first_and_forms_are_unchanged(self):
        page = self.render({})
        self.assertLess(page.index("class='tracker'"), page.index("<h1>CO-0702</h1>"))
        self.assertEqual(page.count("class='st current' aria-current='step'"), 1)
        self.assertIn("Request Approved<span class='sr'> — current stage</span>", page)
        self.assertIn("action='/attended/", page)

    def test_scan_done_and_users_advance_the_tracker_and_checks_are_folded(self):
        tenant = {"operator_assigned": True, "primary_user": {"present": True},
                  "license": {"start_date": "2026-10-01", "expiration_date": "2027-09-30"}}
        page = self.render({"CO-0702": dict(self.READBACK)}, {"CO-0702": {"execution_state": "done", "state": "scan_completed"}}, tenant)
        self.assertIn("User Created<span class='sr'> — current stage</span>", page)
        self.assertIn("Salesforce still shows: Request Approved", page)
        self.assertIn("2026-10-01 → 2027-09-30", page)
        self.assertIn("AAAAAAAA…", page)
        self.assertLess(page.index("class='tracker'"), page.index("Tenant checks"))
        self.assertIn("<details class='more'><summary><h2 class='sum-h'>Tenant checks</h2>", page)

    def test_readback_without_scan_stays_at_account_scanning(self):
        page = self.render({"CO-0702": dict(self.READBACK)})
        self.assertIn("Account Scanning<span class='sr'> — current stage</span>", page)


if __name__ == "__main__":
    unittest.main()
