import unittest

from tools.production_renewal_playwright_preflight import ce_only_tenant_name, classify_rows, sf_command


class ProductionRenewalPreflightTests(unittest.TestCase):
    def test_one_exact_ce_only_candidate_is_found(self):
        self.assertEqual(ce_only_tenant_name("Acme"), "acme - ce only")
        self.assertEqual(
            classify_rows([("Acme - CE Only", "acme.test")], expected_company_name="acme - ce only"),
            "existing_account_found",
        )

    def test_base_name_or_domain_only_matches_fail_closed(self):
        self.assertEqual(
            classify_rows([("Acme", "acme.test")], expected_company_name="acme - ce only"),
            "no_exact_account_found",
        )
        self.assertEqual(
            classify_rows([("Different - CE Only", "acme.test")], expected_company_name="acme - ce only"),
            "no_exact_account_found",
        )

    def test_zero_or_multiple_exact_candidates_fail_closed(self):
        self.assertEqual(classify_rows([], expected_company_name="acme - ce only"), "no_exact_account_found")
        self.assertEqual(
            classify_rows([("Acme - CE Only", "one.test"), (" acme - ce only ", "two.test")], expected_company_name="acme - ce only"),
            "ambiguous_match",
        )
        self.assertEqual(
            classify_rows([("Acme - CE Only", "one.test"), ("Acme - CE Only", "one.test")], expected_company_name="acme - ce only"),
            "ambiguous_match",
        )

    def test_explicit_cli_override_is_portable(self):
        import os
        old = os.environ.get("SURFACE_SF_CLI")
        os.environ["SURFACE_SF_CLI"] = "/usr/local/bin/sf"
        try:
            self.assertEqual(sf_command(), "/usr/local/bin/sf")
        finally:
            if old is None: os.environ.pop("SURFACE_SF_CLI")
            else: os.environ["SURFACE_SF_CLI"] = old


if __name__ == "__main__":
    unittest.main()
