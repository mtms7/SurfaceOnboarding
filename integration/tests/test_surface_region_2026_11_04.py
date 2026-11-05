"""Owner decision 2026-11-04: Salesforce Customer_Onboarding__c.Surface_Tenant_Region__c (picklist US / EU / IL) decides
where a Surface tenant lives: EU = https://app.pentera.io (the default; blank means EU), US = https://us.app.pentera.io,
IL = https://il.app.pentera.io. The clone (Redash "Prod (Cloned) - Mgmt") and the production create cover EU only, so:

* a US / IL CO is never "Clear" (nor "found") by the EU clone: it shows "Region US: not checked" (blocking);
* a US / IL CO holding a UUID past approval shows "Onboarded (UUID in Salesforce), not verified" on the queue;
* the production create and the Surface renewal refuse US / IL (``surface_region_not_supported``) before any browser.

Hermetic: fake reads and a fake page; nothing reads Salesforce, Redash or BackOffice.
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import production_match as match_module
from integration.onboarding import production_surface_renewal as psr
from integration.onboarding import production_views as views
from integration.tests import test_production_create_runner as create_tests

CO, PRODUCT, KIND = "CO-0835", "Surface", "New Product Onboarding"
SNAPSHOT = {"tenants": [{"account_name": "Other", "account_uuid": "b" * 32, "alternate_domains": []}], "captured_at": None}


class RegionRuleTests(unittest.TestCase):
    def test_blank_means_eu_and_unknown_is_never_eu(self):
        for value, expected in ((None, "EU"), ("", "EU"), ("  ", "EU"), ("EU", "EU"), ("eu", "EU"), ("US", "US"),
                                ("IL", "IL"), ("APAC", None), (5, None)):
            self.assertEqual(match_module.region_of(value), expected, value)
        self.assertIsNone(match_module.region_problem(None))
        self.assertIsNone(match_module.region_problem("EU"))
        for value in ("US", "IL", "APAC"):
            self.assertEqual(match_module.region_problem(value), "surface_region_not_supported", value)

    def test_origins(self):
        self.assertEqual(match_module.REGION_ORIGINS, {"EU": "https://app.pentera.io", "US": "https://us.app.pentera.io",
                                                      "IL": "https://il.app.pentera.io"})

    def test_apply_region_never_clears_a_us_co(self):
        for status in ("not_in_production", "uuid_not_in_clone", "tenant_missing", "tenant_found"):
            result = match_module.apply_region({"status": status, "reason": "x"}, "US")
            self.assertEqual((result["status"], result["region"], result["region_was"]), ("region_not_checked", "US", status))
            self.assertIn("https://us.app.pentera.io", result["reason"])

    def test_apply_region_keeps_eu_and_blocking_results(self):
        clear = {"status": "not_in_production", "reason": ""}
        self.assertIs(match_module.apply_region(clear, None), clear)
        self.assertIs(match_module.apply_region(clear, "EU"), clear)
        for status in ("onboarded", "exists_matches", "exists_differs", "exists_other", "ambiguous", "not_checked",
                       "clone_unavailable"):
            result = {"status": status, "reason": "r"}
            self.assertIs(match_module.apply_region(result, "IL"), result, status)

    def test_the_chip_is_blocked_and_names_the_region(self):
        chip = views.production_check({"status": "region_not_checked", "reason": "why", "region": "US", "candidates": []})
        self.assertEqual((chip["code"], chip["label"], chip["blocked"]), ("region_not_checked", "Region US: not checked", True))
        self.assertEqual(chip["reason"], "why")


class RunnerRegionTests(unittest.TestCase):
    def run_for(self, region, full_status="not_in_production", vm=False):
        identity = {"names": ("Ion Example",), "domains": ("ion.example",), "uuid": None, "region": region}
        full = mock.Mock(return_value={"status": full_status, "reason": ""})
        with patch.object(runner, "_production_expectation", return_value=("surface", {"s": 1}, {"p": 1})), \
                patch.object(runner, "_identity_for", return_value=identity), \
                patch.object(runner, "clone_root", return_value=Path("unused")), \
                patch.object(match_module, "load_snapshot", return_value=SNAPSHOT), \
                patch.object(match_module, "production_match", full), \
                patch.object(runner, "_snapshot_account_uuid", return_value=None), \
                patch.object(runner, "_snapshot_identity", return_value=None), \
                patch.object(runner, "_apply_verified_tenant", side_effect=lambda result, *_: result), \
                patch("integration.onboarding.state_paths.vm_mode", return_value=vm):
            return runner.production_match_for(CO, PRODUCT, KIND)

    def test_a_us_co_is_not_checked_instead_of_clear(self):
        found = self.run_for("US")
        self.assertEqual((found["status"], found["region"]), ("region_not_checked", "US"))
        self.assertEqual(views.production_check(found)["label"], "Region US: not checked")

    def test_blank_and_eu_stay_clear(self):
        for region in (None, "", "EU"):
            self.assertEqual(self.run_for(region)["status"], "not_in_production", region)

    def test_a_us_co_whose_uuid_is_found_stays_onboarded(self):
        self.assertEqual(self.run_for("US", full_status="onboarded")["status"], "onboarded")

    def test_identity_read_selects_the_region(self):
        rows = [{"Name": CO, "Account_Name__c": "Ion Example", "Main_Domain__c": "ion.example", "Alternate_Domains__c": None,
                 "Email_Domains__c": None, "Account_UUID__c": None, "Surface_Tenant_Region__c": "US"}]
        with patch.object(runner, "_sf_records", return_value=rows) as read:
            identity = runner._identity_for(CO)
        self.assertIn("Surface_Tenant_Region__c", read.call_args[0][0])
        self.assertEqual(identity["region"], "US")


class QueueRuleTests(unittest.TestCase):
    def test_a_us_co_with_uuid_past_approval_is_onboarded_not_verified(self):
        check = views.production_check({"status": "region_not_checked", "reason": "r", "region": "US", "candidates": []})
        self.assertTrue(dashboard.onboarded_unverified(check, "User Created", True))
        self.assertFalse(dashboard.onboarded_unverified(check, "New", True))
        self.assertFalse(dashboard.onboarded_unverified(check, "User Created", False))


class CreateRefusalTests(create_tests.Base):
    def test_us_and_il_are_refused_before_any_browser(self):
        for region in ("US", "IL", "APAC"):
            with self.subTest(region=region):
                h = self.harness()
                result = h.go(source=dataclasses.replace(create_tests.SOURCE, tenant_region=region))
                self.assertEqual(result["result"], "surface_region_not_supported")
                self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0))
                self.assertIsNone(h.approval_unused())  # nothing consumed
                self.assertEqual(h.outcomes()[-1], "refused")

    def test_eu_and_blank_pass_the_region_gate(self):
        for region in ("EU", ""):
            with self.subTest(region=region):
                h = self.harness()
                result = h.go(source=dataclasses.replace(create_tests.SOURCE, tenant_region=region))
                self.assertNotEqual(result["result"], "surface_region_not_supported")

    def test_the_code_is_a_known_reason(self):
        self.assertEqual(pdr.known_reason("surface_region_not_supported"), "surface_region_not_supported")

    def test_the_region_is_not_part_of_source_equality(self):
        self.assertEqual(create_tests.SOURCE, dataclasses.replace(create_tests.SOURCE, tenant_region="US"))


class RenewalRefusalTests(unittest.TestCase):
    def test_a_us_renewal_is_refused_before_the_dealhub_read(self):
        row = {"Id": "a0X000000000001", "Name": "CO-0829", "LastModifiedDate": "2026-11-03T19:00:00.000+0000",
               "Account__c": "001000000000001AAA", "Surface_Tenant_Region__c": "US"}
        with patch.object(runner, "_sf_records", return_value=[row]) as read:
            with self.assertRaises(psr.RenewalRefused) as caught:
                runner.surface_renewal_intended("CO-0829", date(2026, 11, 3))
        self.assertEqual(str(caught.exception), "surface_region_not_supported")
        self.assertEqual(read.call_count, 1)  # the DealHub read never ran
        self.assertIn("Surface_Tenant_Region__c", read.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
