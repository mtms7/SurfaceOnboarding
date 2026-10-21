"""The Case 3 "Tenant creation summary" (owner request 2026-10-21): only the Salesforce facts the create needs and the values that
go into the tenant, in one short block above the full values card, so the owner sees it before onboarding. Same plan as the dry run
(one source of truth). Pure view model plus the dashboard card; hermetic, no Salesforce, no browser.
"""

from __future__ import annotations

import unittest
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_case3_summary as summary
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_dry_run_ui as base
import tools.attended_ce_only_playwright as runner

REV = base.REV


def plan(**kw):
    return runner.build_case3_fill(c3.make_case3_source(**kw), c3.RUN_DAY)


def flat(block):
    return {label: value for label, value in block}


class SummaryTests(unittest.TestCase):
    def test_salesforce_facts_are_only_what_the_create_needs(self):
        facts = flat(summary.build(plan())["salesforce"])
        self.assertEqual(set(facts), {"Account name", "Main domain", "Credential Exposure email domain", "Country",
                                      "Surface plan", "License end (Surface subscription end)", "Technical Advisor"})
        self.assertEqual((facts["Account name"], facts["Main domain"], facts["Country"]), (c3.NAME, c3.MAIN, "Chile"))
        self.assertEqual(facts["Credential Exposure email domain"], c3.EMAIL)
        self.assertEqual(facts["Surface plan"], "Go (Monthly)")
        self.assertEqual(facts["License end (Surface subscription end)"], "2029-09-30")

    def test_the_technical_advisor_is_not_guessed(self):
        facts = flat(summary.build(plan())["salesforce"])
        self.assertIn("Account", facts["Technical Advisor"])
        self.assertNotIn(c3.NAME, facts["Technical Advisor"])

    def test_the_tenant_block_shows_what_is_created_with_the_owner_rules(self):
        tenant = flat(summary.build(plan())["tenant"])
        self.assertEqual(tenant["Tenant name"], c3.NAME)
        self.assertEqual(tenant["Scanning interval"], "Monthly")
        self.assertIn("scan starts", tenant["Scan now"])
        self.assertEqual(tenant["Maximum scan duration"], "90 hours")
        self.assertEqual(tenant["Leaked Credentials"], "On, Weekly, domain " + c3.EMAIL)
        self.assertEqual(tenant["Licence"], "Prepaid annual subscription, assets 10000, domains 10000, subdomains 500")
        self.assertEqual(tenant["Licence dates"], c3.RUN_DAY.isoformat() + " to 2029-09-30")
        self.assertIn("Operator Account", tenant)
        self.assertIn("Technical Advisor", tenant["Operator Account"])
        self.assertEqual(tenant["MFA"], "On")
        self.assertTrue(tenant["Primary user email"].endswith("@pentera.io"))

    def test_the_switches_line_lists_on_and_off_from_the_plan(self):
        text = flat(summary.build(plan())["tenant"])["Advanced switches"]
        self.assertIn("On: ", text)
        for on in ("Nuclei", "Recon subdomains", "Web dictionary brute force"):
            self.assertIn(on, text.split("Off:")[0])
        for off in ("Automated discovery", "Static outbound IP", "Web dorking", "AI"):
            self.assertIn(off, text.split("Off:")[1])

    def test_the_plan_now_types_10000_domains_and_a_different_value_is_still_flagged(self):
        result = summary.build(plan())
        self.assertIn("domains 10000", flat(result["tenant"])["Licence"])
        self.assertFalse([a for a in result["attention"] if "Number of domains" in a])
        odd = plan()
        odd["texts"]["Number of domains"] = "500"
        flagged = summary.build(odd)
        self.assertIn("domains 500", flat(flagged["tenant"])["Licence"])  # shown as is, never silently changed
        self.assertTrue([a for a in flagged["attention"] if "Number of domains" in a])

    def test_a_long_name_gets_a_short_name_proposal_to_confirm(self):
        long_plan = plan()
        long_plan["texts"]["Company name"] = "Barmherzige Brüder gemeinnützige Träger GmbH"
        result = summary.build(long_plan)
        tenant = flat(result["tenant"])
        self.assertEqual(tenant["Tenant name"], "Barmherzige Brüder gemeinnützige Träger GmbH")  # the plan is never changed
        self.assertEqual(tenant["Short name (docs/60)"], "Proposed: Barmherzige Brüder gemeinnützige TG. Confirm or edit it before create.")
        self.assertTrue([a for a in result["attention"] if "short name" in a.lower()])

    def test_a_short_name_needs_no_proposal(self):
        result = summary.build(plan())
        self.assertEqual(flat(result["tenant"])["Short name (docs/60)"], "Not needed (40 characters or fewer).")
        self.assertFalse([a for a in result["attention"] if "short name" in a.lower()])

    def test_a_name_that_cannot_be_shortened_is_flagged_for_the_owner(self):
        odd = plan()
        odd["texts"]["Company name"] = "B" * 30 + " " + "C" * 30
        result = summary.build(odd)
        self.assertIn("No proposal", flat(result["tenant"])["Short name (docs/60)"])
        self.assertTrue([a for a in result["attention"] if "short name" in a.lower()])

    def test_the_operator_is_always_listed_for_attention(self):
        self.assertTrue([a for a in summary.build(plan())["attention"] if "Technical Advisor" in a])

    def test_an_incomplete_plan_fails_closed(self):
        broken = plan()
        del broken["checkboxes"]["fullNucleiScanEnabled"]
        with self.assertRaises(ValueError):
            summary.build(broken)
        with self.assertRaises(ValueError):
            summary.build({"texts": {}})


class CardTests(unittest.TestCase):
    def card(self, row=None, plan_result=None):
        with mock.patch.object(dashboard, "case3_values", mock.Mock(return_value=(REV, plan_result or plan()))):
            return dashboard._case3_values_card("CO-0800", row if row is not None else base.row())

    def test_the_summary_comes_before_the_full_values_and_has_no_control(self):
        html = self.card()
        self.assertIn("Tenant creation summary", html)
        self.assertLess(html.index("Tenant creation summary"), html.index("Add Account values"))
        for word in ("Technical Advisor", "90 hours", "Go (Monthly)"):
            self.assertIn(word, html)
        for tag in ("<form", "<button", "<input", "<script", "method='post'"):
            self.assertNotIn(tag, html)

    def test_values_are_escaped(self):
        evil = plan()
        evil["texts"]["Company name"] = "<script>alert(1)</script> & Co"
        html = self.card(plan_result=evil)
        self.assertNotIn("<script>alert", html)

    def test_a_blocked_co_shows_no_summary(self):
        html = self.card(row=base.row(Onboarding_Approval_Status__c="Pending"))
        self.assertIn("Do not onboard", html)
        self.assertNotIn("Tenant creation summary", html)


if __name__ == "__main__":
    unittest.main()
