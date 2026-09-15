import unittest

from tools.production_renewal_playwright_preflight import classify_rows, sf_command


class ProductionRenewalPreflightTests(unittest.TestCase):
    def test_one_exact_candidate_is_found(self):
        self.assertEqual(classify_rows([("Acme", "acme.test")], account_name="acme", email_domain="acme.test"), "existing_account_found")

    def test_zero_or_multiple_candidates_fail_closed(self):
        self.assertEqual(classify_rows([], account_name="acme", email_domain="acme.test"), "no_exact_account_found")
        self.assertEqual(classify_rows([("Acme A", "acme.test"), ("Acme B", "acme.test")], account_name="acme", email_domain="acme.test"), "ambiguous_match")

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
