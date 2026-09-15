import unittest

from phase1_validator.ce_entitlement import evaluate_credential_exposure_entitlement


class CredentialExposureEntitlementTests(unittest.TestCase):
    def test_explicit_ce_module_quantity_is_the_email_domain_limit(self):
        result = evaluate_credential_exposure_entitlement([
            {"product_full_name": "Credential Exposure Module", "quantity": "3"}
        ])
        self.assertTrue(result["credential_exposure_ready"])
        self.assertEqual(result["email_domain_limit"], 3)
        self.assertEqual(result["evidence_source"], "dealhub_ce_module")

    def test_core_contract_bundle_uses_one_domain_baseline(self):
        result = evaluate_credential_exposure_entitlement([], contract_evidence={
            "credential_exposure": "bundled_verified", "canonical_product": "core_plus_commercial"
        })
        self.assertTrue(result["credential_exposure_ready"])
        self.assertEqual(result["email_domain_limit"], 1)

    def test_missing_or_ambiguous_evidence_fails_closed(self):
        missing = evaluate_credential_exposure_entitlement([])
        ambiguous = evaluate_credential_exposure_entitlement([
            {"product_full_name": "Credential Exposure Module", "quantity": 1},
            {"product_full_name": "Credential Exposure Module", "quantity": 1},
        ])
        self.assertEqual(missing["reason"], "credential_exposure_evidence_missing")
        self.assertEqual(ambiguous["reason"], "credential_exposure_module_ambiguous")


if __name__ == "__main__":
    unittest.main()
