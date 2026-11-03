"""CE-only with several email domains (owner go 2026-11-03, CO-0814 Seidor "seidor.com seidor.es").

The Email Domains value is normalised to "seidor.com, seidor.es"; the first is the primary domain; every domain is typed into
Leaked Credentials scanned domains, expected in the create body and duplicate-checked; Case 3 keeps exactly one. Local only.
"""
from __future__ import annotations

import unittest
from datetime import date

import tools.attended_ce_only_playwright as runner
import tools.issue_production_approval as issuer
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_contract as contract
from integration.onboarding import production_create
from integration.onboarding import production_views as views

LC = "Leaked Credentials scanned domains (Comma Separated Values)"


def source(domains=("seidor.com", "seidor.es")):
    return runner.CeFillSource("CO-0814", "2026-11-03T19:32:06.000+0000", "001000000000001AAA", "Seidor", domains[0], "Spain",
                               date(2026, 11, 2), date(2029, 11, 1), "Seidor - CE Only", "seidor",
                               email_domains=tuple(domains))


class NormaliseTests(unittest.TestCase):
    def test_values(self):
        cases = {
            "seidor.com seidor.es": ("seidor.com", "seidor.es"),
            "seidor.com,seidor.es": ("seidor.com", "seidor.es"),
            " @Seidor.com ;  seidor.ES\n": ("seidor.com", "seidor.es"),
            "seidor.com, seidor.com": ("seidor.com",),
            "a.example b.example c.example": ("a.example", "b.example", "c.example"),
            "one.test": ("one.test",),
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(runner.ce_email_domains(raw), want)
        for raw in ("", "   ", None, "seidor.com not_a_domain", "seidor", 7):
            with self.subTest(raw=raw):
                self.assertIsNone(runner.ce_email_domains(raw))

    def test_one_email_domain_is_unchanged(self):
        self.assertIsNone(runner.one_email_domain("seidor.com seidor.es"))
        self.assertEqual(runner.one_email_domain("@Seidor.com"), "seidor.com")


class PlanAndContractTests(unittest.TestCase):
    def test_fill_plan_types_every_domain(self):
        plan = runner.build_ce_only_fill(source(), date(2026, 11, 3))
        self.assertEqual(plan["texts"]["Company primary domain"], "seidor.com")
        self.assertEqual(plan["texts"][LC], "seidor.com, seidor.es")

    def test_one_domain_plan_is_unchanged(self):
        plan = runner.build_ce_only_fill(source(("one.test",)), date(2026, 11, 3))
        self.assertEqual(plan["texts"][LC], "one.test")
        legacy = runner.CeFillSource("CO-1", "r", "001000000000001AAA", "One", "one.test", "Spain", date(2026, 11, 2),
                                     date(2029, 11, 1), "One - CE Only", "one")
        self.assertEqual(runner.build_ce_only_fill(legacy, date(2026, 11, 3))["texts"][LC], "one.test")

    def test_create_body_expects_the_list(self):
        intended = runner.ce_production_intended(source(), date(2026, 11, 3))
        self.assertEqual(intended["leaked_domains"], ["seidor.com", "seidor.es"])
        payload = contract.expected_create_payload(intended)
        self.assertEqual(payload["leakedCredentialsScannedDomains"], ["seidor.com", "seidor.es"])
        self.assertEqual(payload["accountDomain"], "seidor.com")
        self.assertEqual(payload["alternateDomains"], [])
        one = contract.expected_create_payload(runner.ce_production_intended(source(("one.test",)), date(2026, 11, 3)))
        self.assertEqual(one["leakedCredentialsScannedDomains"], ["one.test"])

    def test_bad_lists_are_refused(self):
        intended = runner.ce_production_intended(source(), date(2026, 11, 3))
        for bad in (["seidor.es", "seidor.com"], ["seidor.com", "seidor.com"], [], "seidor.com", ["seidor.com", "bad domain"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                contract.expected_create_payload(dict(intended, leaked_domains=bad))

    def test_the_create_gate_takes_several_domains(self):
        self.assertFalse(production_create._domain_problem(("seidor.com", "seidor.es"), many=True))  # CE-only
        self.assertTrue(production_create._domain_problem(("seidor.com", "seidor.es")))  # Case 3: exactly one
        self.assertFalse(production_create._domain_problem(("one.test",)))
        for bad in ((), ("seidor.com", "seidor.com"), ("seidor.com", "bad domain"), "seidor.com", None):
            with self.subTest(bad=bad):
                self.assertTrue(production_create._domain_problem(bad, many=True))


class ApprovalTests(unittest.TestCase):
    ROW = {"Name": "CO-0814", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
           "Onboarding_Approval_Status__c": "Approved", "Email_Domains__c": "seidor.com seidor.es", "Account_UUID__c": None,
           "LastModifiedDate": "2026-11-03T19:32:06.000+0000"}

    def test_ce_only_accepts_several(self):
        self.assertIsNone(issuer.eligibility_problem(self.ROW, "CO-0814"))
        self.assertEqual(issuer.eligibility_problem(dict(self.ROW, Email_Domains__c="seidor.com nope"), "CO-0814"),
                         "email_domain_invalid")

    def test_case3_still_needs_exactly_one(self):
        row = dict(self.ROW, Onboarding_Product__c="Surface & Credential Exposure", Main_Domain__c="seidor.com",
                   Surface_Account_ID__c=None)
        self.assertEqual(issuer.eligibility_problem(row, "CO-0814"), "email_domain_not_single")


class DisplayTests(unittest.TestCase):
    def test_note(self):
        self.assertEqual(dashboard.ce_domains_note(("seidor.com", "seidor.es")),
                         "Credential Exposure with 2 email domains: seidor.com, seidor.es")
        self.assertEqual(dashboard.ce_domains_note(("one.test",)), "")
        self.assertEqual(dashboard.ce_domains_note(None), "")

    def test_co_page_head(self):
        row = dict(ApprovalTests.ROW)
        self.assertIn("2 email domains: seidor.com, seidor.es", dashboard._ce_domains_head(row))
        self.assertEqual(dashboard._ce_domains_head(dict(row, Email_Domains__c="one.test")), "")

    def test_checklist_data_row(self):
        common = dict(vm=False, locked=False, lock_reason="", clone={"state": "fresh", "age_seconds": 60}, match=None,
                      uuid_state="empty", ce_route=True, start_iso="2026-11-02", today=date(2026, 11, 3), days_before=2,
                      approval=None, create=None, gate=None, session=None, approved_in_salesforce=True)
        items = {item["key"] if "key" in item else item.get("id"): item
                 for item in views.onboard_checklist(domain_count=2, **common)}
        data = [item for item in views.onboard_checklist(domain_count=2, **common) if "email domain" in str(item)]
        self.assertTrue(data and "2 email domains" in str(data[0]))
        none = [item for item in views.onboard_checklist(domain_count=0, **common) if "email domain" in str(item)]
        self.assertTrue(none and "At least one" in str(none[0]))
        self.assertTrue(items)

    def test_sheet_rows(self):
        rules = dashboard._sheet_rules() if hasattr(dashboard, "_sheet_rules") else None
        if rules is None:
            self.skipTest("sheet rules helper not exposed")
        self.assertEqual(rules["domains_of"]("seidor.com seidor.es"), ("seidor.com", "seidor.es"))


if __name__ == "__main__":
    unittest.main()
