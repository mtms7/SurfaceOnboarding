"""Owner decisions 2026-10-20 for CO-0800 (Surface & Credential Exposure, New Product Onboarding = Case 3) in production:

* production approvals may be issued for the Case 3 route, one CO, 7 days, revision-bound, consumed once (scope
  ``case3_single_co``); a CE-only approval can never authorize Case 3 and the reverse;
* the production CREATE stays CE-only: a valid Case 3 approval does not open any create gate (the dry run and the create for
  Case 3 are separate, later, reviewed steps);
* Case 3 needs a main domain and exactly one email domain; when the main domain differs from the email domain the review
  page shows an ORANGE warning (not a blocker) so the owner sees it before approving;
* the licence expiry is the subscription end for every onboarding (one rule).

Hermetic: temporary folders, fake rows; no Salesforce, no Redash, no network, no browser.
"""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_approval as pa
from integration.onboarding import production_create
from integration.onboarding import production_views as views
from integration.onboarding.models import EngineValue
from tools import issue_production_approval as tool

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
OWNER = "owner@example.test"
ISSUERS = frozenset({OWNER})
CO = "CO-0800"
REV = "2026-10-20T00:35:37.000+0000"
REF = "PROD-C3-0800-20261020"
CASE3 = EngineValue.CASE_3.value
CE = EngineValue.CASE_2.value
ROW = {"Name": CO, "Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
       "Onboarding_Approval_Status__c": "Approved", "Main_Domain__c": "customer.example",
       "Email_Domains__c": "@customer-mail.example", "Account_UUID__c": None, "Surface_Account_ID__c": None,
       "LastModifiedDate": REV}


class ApprovalPairTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / pa.STATE_FILE_NAME

    def issue(self, route=CASE3, ref=REF, **over):
        args = dict(approval_ref=ref, co_reference=CO, route=route, source_revision=REV, issued_by=OWNER,
                    allowed_issuers=ISSUERS, now=NOW)
        args.update(over)
        return pa.issue(self.path, **args)

    def check(self, route=CASE3, ref=REF, **over):
        args = dict(approval_ref=ref, co_reference=CO, route=route, source_revision=REV, now=NOW)
        args.update(over)
        return pa.check(self.path, **args)

    def test_a_case3_approval_is_one_co_seven_days_revision_bound_with_its_own_scope(self):
        record = self.issue()
        self.assertEqual((record["route"], record["scope"]), (CASE3, "case3_single_co"))
        self.assertEqual(record["expires_at"], "2026-10-27T12:00:00Z")
        self.assertIsNone(self.check())
        self.assertEqual(self.check(source_revision="other"), "approval_revision_drift")
        self.assertEqual(self.check(co_reference="CO-0801"), "approval_co_mismatch")
        self.assertIsNone(pa.consume(self.path, REF, CO, CASE3, REV, NOW))
        self.assertEqual(self.check(), "approval_consumed")

    def test_a_ce_only_approval_never_authorizes_case3_and_the_reverse(self):
        self.issue(route=CE, ref="PROD-CE-0800-20261020")
        self.assertEqual(self.check(route=CASE3, ref="PROD-CE-0800-20261020"), "approval_route_mismatch")
        self.assertIsNone(self.check(route=CE, ref="PROD-CE-0800-20261020"))
        with tempfile.TemporaryDirectory() as other:
            self.path = Path(other) / pa.STATE_FILE_NAME
            self.issue()
            self.assertEqual(self.check(route=CE), "approval_route_mismatch")

    def test_other_routes_are_still_refused(self):
        for route in (EngineValue.CASE_1.value, "case_2_ce_only", "", None, 5):
            with self.assertRaises(pa.ApprovalRefused) as raised:
                self.issue(route=route)
            self.assertEqual(raised.exception.reason, "route_not_ce_only", route)
        self.assertFalse(self.path.exists())

    def test_a_record_with_the_wrong_scope_for_its_route_makes_the_file_unreadable(self):
        self.issue()
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        stored[REF]["scope"] = "ce_only_single_co"
        self.path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(self.check(), "approval_unreadable")

    def test_the_approvals_list_shows_a_case3_approval_as_valid(self):
        self.issue()
        listing = views.list_approvals(self.path, NOW)
        self.assertEqual(listing["state"], "ok")
        self.assertEqual([item["state"] for item in listing["items"]], ["valid"])

    def test_a_case3_approval_opens_no_create_gate(self):
        """The create stays CE-only: even a valid Case 3 approval is refused by the create gate's route rule."""
        self.issue()
        self.assertEqual(production_create.production_approval.ALLOWED_ROUTE, CE)
        gate_source = Path(production_create.__file__).read_text(encoding="utf-8")
        self.assertIn("if inputs.route != production_approval.ALLOWED_ROUTE:", gate_source)
        self.assertIn('return "route_not_ce_only_new"', gate_source)


class IssueToolCase3Tests(unittest.TestCase):
    def test_a_case3_row_is_eligible_with_a_main_domain_and_one_email_domain(self):
        self.assertIsNone(tool.eligibility_problem(dict(ROW), CO))
        self.assertEqual(tool.route_for_row(dict(ROW)), CASE3)
        self.assertEqual(tool.route_for_row({**ROW, "Onboarding_Product__c": "Credential Exposure"}), CE)
        self.assertIsNone(tool.route_for_row({**ROW, "Onboarding_Product__c": "Surface"}))

    def test_case3_refusals(self):
        cases = (("main_domain_missing", {"Main_Domain__c": None}), ("main_domain_missing", {"Main_Domain__c": "not a domain"}),
                 ("email_domain_not_single", {"Email_Domains__c": "a.example, b.example"}),
                 ("email_domain_not_single", {"Email_Domains__c": None}),
                 ("approval_status_not_approved", {"Onboarding_Approval_Status__c": "Pending"}),
                 ("account_uuid_present", {"Account_UUID__c": "a" * 32}),
                 ("surface_account_id_present", {"Surface_Account_ID__c": "b" * 24}),
                 ("source_revision_missing", {"LastModifiedDate": ""}))
        for reason, over in cases:
            with self.subTest(reason=reason, over=list(over)):
                self.assertEqual(tool.eligibility_problem({**ROW, **over}, CO), reason)

    def test_the_default_approval_ref_names_the_route(self):
        self.assertEqual(tool.default_approval_ref(CO, NOW, CASE3), "PROD-C3-0800-20261020")
        self.assertEqual(tool.default_approval_ref(CO, NOW), "PROD-CE-0800-20261020")


class ExpiryRuleTests(unittest.TestCase):
    def test_every_route_expires_on_the_subscription_end(self):
        import tools.attended_ce_only_playwright as runner
        start, end = date(2026, 10, 1), date(2029, 9, 30)
        self.assertEqual(runner.ce_license_dates(start, end), (start, end))
        self.assertEqual(runner.annual_license_dates(start, end), (start, end))  # Surface-only and Case 3: no one-year cap
        run_day = date(2026, 10, 20)
        self.assertEqual(runner.annual_run_license_dates(start, end, run_day), (run_day, end))
        with self.assertRaises(ValueError):
            runner.annual_run_license_dates(start, end, end)  # an expiry that is not after the run day still stops


class Case3ApprovalStatusTests(unittest.TestCase):
    """The summary of a Surface-route CO cannot be approved while Salesforce does not say Approved (CO-0800 was Pending)."""

    def summary(self, status, product="Surface & Credential Exposure", kind="New Product Onboarding"):
        from integration.tests import test_onboard_review as review

        case = review.Case()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.row = {"Name": "CO-0800", "Account_Name__c": "Customer Co", "Onboarding_Product__c": product,
                    "Onboarding_Type__c": kind, "Onboarding_Approval_Status__c": status}
        with case.patched():
            return dashboard.build_review_summary("CO-0800")[0]

    def test_pending_blocks_and_the_text_says_why(self):
        for status in ("Pending", None, "Rejected", ""):
            summary = self.summary(status)
            self.assertIn("salesforce_not_approved", summary["blockers"], status)
        self.assertIn("salesforce_not_approved", dashboard.REVIEW_BLOCKER_TEXT)
        self.assertIn("Approved", dashboard.REVIEW_BLOCKER_TEXT["salesforce_not_approved"])

    def test_approved_does_not_block(self):
        self.assertNotIn("salesforce_not_approved", self.summary("Approved")["blockers"])

    def test_the_ce_only_route_is_unchanged(self):
        summary = self.summary("Pending", product="Credential Exposure")
        self.assertNotIn("salesforce_not_approved", summary["blockers"])


class DomainWarningTests(unittest.TestCase):
    """Case 3: the main domain and the CE email domain; orange (not a blocker) when they differ."""

    def checks(self, domains, *, route=None, plan_primary="customer.example", alternates=()):
        summary = {"route": route or dashboard.CASE3_ENGINE,
                   "values": {"domains": list(domains),
                              "plan": {"license_start": "2026-10-19", "primary_domain": plan_primary,
                                       "alternate_domains": tuple(alternates)}}}
        with mock.patch.object(dashboard, "production_now", return_value=NOW):
            return dashboard._other_checks(summary, {"Account_UUID__c": None})

    def test_same_domain_is_ok_and_not_a_warning(self):
        html, failed = self.checks(["customer.example", "customer.example"])
        self.assertIn("Email domain", html)
        self.assertNotIn("differs", html)
        self.assertEqual(failed, 0)
        html, failed = self.checks(["customer.example"])
        self.assertNotIn("differs", html)
        self.assertEqual(failed, 0)

    def test_a_different_email_domain_is_an_orange_warning_that_does_not_block(self):
        html, failed = self.checks(["customer.example", "customer-mail.example"])
        block = html[html.index("Email domain"):]
        self.assertIn("chip-warn", block)
        self.assertIn("differs", block)
        self.assertIn("customer.example", block)
        self.assertIn("customer-mail.example", block)
        self.assertEqual(failed, 0)  # a warning to see before approving, never a blocker

    def test_more_than_one_email_domain_is_a_blocker(self):
        html, failed = self.checks(["customer.example", "a.example", "b.example"])
        self.assertIn("chip-bad", html[html.index("Email domain"):])
        self.assertEqual(failed, 1)

    def test_other_routes_are_unchanged(self):
        html, _ = self.checks(["x.example"], route=dashboard.SURFACE_ENGINE)
        self.assertIn("Not required for this route", html)
        html, failed = self.checks(["x.example"], route=dashboard.CE_ENGINE)
        self.assertIn("chip-ok", html[html.index("Email domain"):])


if __name__ == "__main__":
    unittest.main()
