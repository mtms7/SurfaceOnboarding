import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from phase2_leonardo.intake import (
    ENGINE_VALUE,
    INTAKE_CONTRACT_VERSION,
    MAPPING_POLICY_VERSION,
    PREFLIGHT_CONTRACT_VERSION,
    PublicSuffixList,
    IntakeError,
    build_primary_user_email,
    normalize_company_name,
    normalize_domain_candidate,
    prepare_case3,
)
from phase2_leonardo.server_intake import PREPARE_PATH, Phase2IntakeHandler


def valid_payload():
    return {
        "contract_version": INTAKE_CONTRACT_VERSION,
        "target_environment": "leonardo-development",
        "source": {
            "co_number": "CO-9999",
            "sf_record_id": "a0C000000000001AAA",
            "source_revision": "2026-08-27T20:00:00Z",
        },
        "routing": {"engine_value": ENGINE_VALUE, "special_requirements": False},
        "account": {
            "company_name": "Éxample Labs - CE Only",
            "primary_domain": "example.com",
            "domain_candidates": "example.org, example.net, portal.example.org",
            "ce_email_domains": ["example.com"],
            "country": "United Kingdom",
        },
        "primary_user": {
            "first_name": "Synthetic",
            "last_name": "User",
            "email": "synthetic@pentera.io",
            "phone": "",
            "job_title": "",
            "operator_account": None,
        },
        "entitlements": {
            "surface": {
                "record_id": "a0B000000000001AAA",
                "system_modstamp": "2026-08-27T19:00:00Z",
                "opportunity_id": "006000000000001AAA",
                "product_full_name": "Pentera Surface Go - 500 Subdomains",
                "status": "Active",
                "quantity": 500,
                "quantity_source": "owner_approved_product_tier",
                "unit": "Subdomains",
                "start_date": "2026-07-01",
                "end_date": "2029-06-30",
            },
            "core": {
                "record_id": "a0B000000000002AAA",
                "system_modstamp": "2026-08-27T19:00:01Z",
                "opportunity_id": "006000000000001AAA",
                "product_full_name": "Pentera Core Plus Commercial - 500 End Points",
                "status": "Active",
                "quantity": 500,
                "quantity_source": "owner_approved_product_tier",
                "unit": "End Points",
                "start_date": "2026-07-01",
                "end_date": "2029-06-30",
            },
            "add_ons": [],
        },
    }


class NormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.psl = PublicSuffixList()

    def test_company_name_is_ascii_and_combined_case_has_no_ce_suffix(self):
        self.assertEqual(normalize_company_name("Éxample Labs - CE Only"), "Example Labs")

    def test_internal_primary_user_email_uses_owner_approved_company_rule(self):
        self.assertEqual(
            build_primary_user_email("owner.user@pentera.io", "Example Labs"),
            "owner.user+examplelabs@pentera.io",
        )
        self.assertEqual(
            build_primary_user_email("owner.user@pentera.io", "Example Labs Tests"),
            "owner.user+elt@pentera.io",
        )

    def test_primary_user_alias_uses_normalized_combined_account_name(self):
        self.assertEqual(
            build_primary_user_email("owner.user@pentera.io", "\u00c9xample-Labs - CE Only"),
            "owner.user+examplelabs@pentera.io",
        )
        self.assertEqual(
            build_primary_user_email("owner.user@pentera.io", "\u00c9xample Labs Tests - CE Only"),
            "owner.user+elt@pentera.io",
        )

    def test_external_primary_user_email_is_not_rewritten(self):
        self.assertEqual(
            build_primary_user_email("customer.user@example.com", "Example Labs"),
            "customer.user@example.com",
        )

    def test_internal_primary_user_email_rejects_existing_plus_alias(self):
        with self.assertRaisesRegex(IntakeError, "organization_email_required"):
            build_primary_user_email("owner.user+old@pentera.io", "Example Labs")

    def test_root_subdomain_url_network_and_idn_classification(self):
        self.assertEqual(normalize_domain_candidate("example.org", self.psl).kind, "root_domain")
        portal = normalize_domain_candidate("https://portal.example.org:443/path?q=1", self.psl)
        self.assertEqual((portal.kind, portal.value, portal.registrable), ("subdomain", "portal.example.org", "example.org"))
        self.assertEqual(normalize_domain_candidate("192.0.2.10/24", self.psl).value, "192.0.2.0/24")
        self.assertEqual(normalize_domain_candidate("bücher.de", self.psl).value, "xn--bcher-kva.de")

    def test_email_wildcard_and_public_suffix_fail_closed(self):
        for value in ("user@example.com", "*.example.com", "co.uk"):
            with self.subTest(value=value), self.assertRaises(IntakeError):
                normalize_domain_candidate(value, self.psl)

    def test_multiple_credential_exposure_root_domains_require_addon(self):
        from phase2_leonardo.intake import normalize_domains

        with self.assertRaisesRegex(IntakeError, "exactly_one_entitled_without_addon"):
            normalize_domains(
                "example.com",
                "example.org, example.net",
                "example.org, example.net",
                self.psl,
            )

    def test_credential_exposure_requires_at_least_one_root_domain(self):
        from phase2_leonardo.intake import normalize_domains

        with self.assertRaisesRegex(IntakeError, "exactly_one_entitled_without_addon"):
            normalize_domains("example.com", [], [], self.psl)


class Case3PreparationTests(unittest.TestCase):
    def test_prepares_partial_case3_candidate_without_authorizing_execution(self):
        result = prepare_case3(valid_payload())
        schema = json.loads(
            (Path(__file__).parent / "contracts" / "case3-preflight-response.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(result), set(schema["required"]))
        self.assertEqual(set(result), set(schema["properties"]))
        for key in (
            "contract_version", "target_environment", "decision", "proceed",
            "leonardo_request_allowed", "action_required", "mapping_authority",
            "execution_mapping_status", "mapping_policy_version",
        ):
            self.assertEqual(result[key], schema["properties"][key]["const"])
        self.assertEqual(result["contract_version"], PREFLIGHT_CONTRACT_VERSION)
        self.assertEqual(result["decision"], "candidate_mapping_requires_owner_review")
        self.assertFalse(result["proceed"])
        self.assertFalse(result["leonardo_request_allowed"])
        self.assertEqual(result["mapping_authority"], "partial_owner_recorded_normalization_only")
        self.assertEqual(result["execution_mapping_status"], "unapproved")
        self.assertEqual(
            result["unapproved_field_groups"],
            [
                "account_enums_and_collision_rules",
                "settings",
                "attack_modules",
                "license_semantics",
                "addon_taxonomy",
                "duplicate_and_readback_rules",
            ],
        )
        self.assertEqual(result["mapping_policy_version"], MAPPING_POLICY_VERSION)
        self.assertEqual(result["onboarding_comments"], "2026-07-01 - 2029-06-30")
        self.assertEqual(result["alternate_domains_csv"], "example.net, example.org")
        self.assertEqual(result["subdomains_csv"], "portal.example.org")
        draft = result["draft_manifest_sections"]
        self.assertEqual(draft["account"]["company_name"], "Example Labs")
        self.assertEqual(draft["routing"]["engine_value"], "case_3_combined_baseline")
        self.assertEqual(draft["settings"]["scanning_interval"], "Monthly")
        self.assertTrue(draft["settings"]["scan_now"])
        self.assertNotIn("spycloud", draft["attack_modules"])
        self.assertEqual(draft["license"]["number_of_assets"], 10_000)
        self.assertEqual(draft["license"]["number_of_domains"], 3)
        self.assertEqual(draft["license"]["number_of_subdomains"], 500)
        self.assertEqual(draft["primary_user"]["first_name"], "Synthetic")
        self.assertEqual(draft["primary_user"]["last_name"], "User")
        self.assertEqual(draft["primary_user"]["email"], "synthetic+examplelabs@pentera.io")
        self.assertTrue(draft["primary_user"]["mfa_enabled"])
        self.assertIsNone(draft["primary_user"]["operator_account"])
        self.assertEqual(len(result["evidence_hash"]), 64)
        self.assertEqual(result, prepare_case3(valid_payload()))

    def test_source_identity_revision_and_term_are_bound(self):
        result = prepare_case3(valid_payload())
        bindings = result["source_bindings"]
        self.assertEqual(bindings["surface_record_id"], "a0B000000000001AAA")
        self.assertEqual(bindings["core_system_modstamp"], "2026-08-27T19:00:01Z")

    def test_number_of_domains_is_distinct_root_sum_without_double_counting_ce(self):
        payload = valid_payload()
        payload["account"]["ce_email_domains"] = ["example.org"]
        result = prepare_case3(payload)
        self.assertEqual(result["draft_manifest_sections"]["license"]["number_of_domains"], 3)

    def test_quantity_term_opportunity_and_special_route_fail_closed(self):
        mutations = []
        payload = valid_payload()
        payload["entitlements"]["surface"]["quantity"] = 499
        mutations.append(payload)
        payload = valid_payload()
        payload["entitlements"]["core"]["end_date"] = "2028-06-30"
        mutations.append(payload)
        payload = valid_payload()
        payload["entitlements"]["core"]["opportunity_id"] = "006000000000002AAA"
        mutations.append(payload)
        payload = valid_payload()
        payload["routing"]["special_requirements"] = True
        mutations.append(payload)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(IntakeError):
                prepare_case3(payload)

    def test_unknown_addon_and_schema_drift_fail_closed_without_source_echo(self):
        payload = valid_payload()
        payload["entitlements"]["add_ons"] = [{
            "record_id": "a0B000000000003AAA",
            "system_modstamp": "2026-08-27T19:00:02Z",
            "product_full_name": "Unapproved Restricted Add-on",
            "status": "Active",
        }]
        with self.assertRaises(IntakeError) as raised:
            prepare_case3(payload)
        self.assertNotIn("Restricted", str(raised.exception))
        drifted = valid_payload()
        drifted["browser_cookie"] = "must-never-be-accepted"
        with self.assertRaisesRegex(IntakeError, "schema_mismatch"):
            prepare_case3(drifted)


class IntakeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Phase2IntakeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_and_prepare(self):
        with urllib.request.urlopen(self.base + "/healthz", timeout=2) as response:
            self.assertEqual(json.load(response)["policy_version"], MAPPING_POLICY_VERSION)
        request = urllib.request.Request(
            self.base + PREPARE_PATH,
            data=json.dumps(valid_payload()).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            result = json.load(response)
        self.assertFalse(result["proceed"])

    def test_duplicate_json_keys_and_sensitive_error_values_are_not_echoed(self):
        raw = b'{"contract_version":"a","contract_version":"b"}'
        request = urllib.request.Request(self.base + PREPARE_PATH, data=raw, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=2)
        body = raised.exception.read().decode()
        self.assertEqual(raised.exception.code, 400)
        self.assertNotIn("contract_version", body)


if __name__ == "__main__":
    unittest.main()
