import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from phase2_leonardo.policy import (
    assess_execution_readiness,
    build_idempotency_key,
    canonical_manifest_sha256,
    is_allowed_leonardo_url,
    safe_evidence,
    validate_manifest,
    validate_result,
    validate_result_for_manifest,
)


FIXED_NOW = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)


def valid_manifest():
    manifest = {
        "contract_version": "leonardo-dev-create-v2",
        "target_environment": "leonardo-development",
        "correlation_id": "synthetic-job-001",
        "idempotency_key": "",
        "source": {
            "sf_record_id": "SYNTHETIC-NO-SF-WRITE",
            "co_number": "CO-9999",
            "source_revision": "synthetic-revision-1",
        },
        "routing": {"engine_value": "case_2_new_ce_only", "policy_version": "phase2-local-v1"},
        "approval": {
            "request_key": "synthetic-approval-1",
            "requested_by": "requester-group:test-user-a",
            "approved_by": "approver-group:test-user-b",
            "approver_group": "cse-approvers:test",
            "attestation_id": "workato-approval-row:test-1",
            "decision": "approved",
            "approval_revision": 1,
            "approved_at": "2026-08-24T20:00:00Z",
            "expires_at": "2026-08-24T21:00:00Z",
            "operation": "fill_and_pause",
            "manifest_sha256": "",
        },
        "account": {
            "company_name": "Synthetic CE Test",
            "account_type": "Demo",
            "primary_domain": "surface-phase2.invalid",
            "alternate_domains": [],
            "subdomains": [],
            "email_domains": ["surface-phase2.invalid"],
            "networks": [],
            "country": "US",
        },
        "primary_user": {
            "first_name": "Synthetic",
            "last_name": "Operator",
            "email": "synthetic+phase2@pentera.invalid",
            "phone": "",
            "job_title": "",
            "mfa_enabled": True,
            "operator_account": None,
        },
        "settings": {
            "scanning_interval": "None",
            "scan_now": False,
            "maximum_scan_duration_hours": 90,
            "automated_discovery": False,
            "recon_subdomains": True,
            "multiple_attack_stacks": False,
            "mas_for_subdomains": False,
            "web_dictionary_brute_force": True,
            "web_dorking": False,
            "nuclei": True,
            "authenticated_testing": False,
            "static_outbound_ip": False,
            "ai": False,
            "notifications": True,
            "multiple_users": True,
            "api_access": True,
        },
        "attack_modules": {
            "phishing": False,
            "leaked_credentials": True,
            "leaked_credentials_interval": "Weekly",
            "leaked_credentials_domains": ["surface-phase2.invalid"],
        },
        "license": {
            "include_provisioning": True,
            "include_subdomains": True,
            "type": "Prepaid annual subscription",
            "number_of_assets": 0,
            "number_of_domains": 1,
            "number_of_subdomains": 0,
            "start_date": "2026-08-24",
            "expiration_date": "2027-08-23",
        },
    }
    manifest["approval"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
    manifest["idempotency_key"] = build_idempotency_key(manifest)
    return manifest


class ManifestPolicyTests(unittest.TestCase):
    def test_valid_synthetic_manifest_is_ready_only_for_attended_execution(self):
        manifest = valid_manifest()
        self.assertEqual(validate_manifest(manifest, now=FIXED_NOW), [])
        intake = {
            "co_number": manifest["source"]["co_number"],
            "sf_record_id": manifest["source"]["sf_record_id"],
            "source_revision": manifest["source"]["source_revision"],
            "proceed": True,
            "leonardo_request_allowed": True,
        }
        result = assess_execution_readiness(
            manifest, intake_evidence=intake, duplicate_count=0, prior_state=None, now=FIXED_NOW
        )
        self.assertFalse(result["proceed"])
        self.assertTrue(result["local_contract_valid"])
        self.assertIn("routing:mapping_not_approved", result["reasons"])
        self.assertIn("authorization:trusted_workato_approval_not_verified", result["reasons"])

    def test_partially_reviewed_case3_mapping_does_not_clear_execution_gate(self):
        manifest = valid_manifest()
        manifest["routing"] = {
            "engine_value": "case_3_combined_baseline",
            "policy_version": "surface-case3-partial-owner-mapping-2026-08-31-v5",
        }
        manifest["approval"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
        manifest["idempotency_key"] = build_idempotency_key(manifest)
        intake = {
            "co_number": manifest["source"]["co_number"],
            "sf_record_id": manifest["source"]["sf_record_id"],
            "source_revision": manifest["source"]["source_revision"],
            "proceed": True,
            "leonardo_request_allowed": True,
        }
        result = assess_execution_readiness(
            manifest, intake_evidence=intake, duplicate_count=0, prior_state=None, now=FIXED_NOW
        )
        self.assertFalse(result["proceed"])
        self.assertIn("routing:mapping_not_approved", result["reasons"])
        self.assertIn("authorization:trusted_workato_approval_not_verified", result["reasons"])

        manifest["approval"]["operation"] = "validate_only"
        manifest["approval"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
        manifest["idempotency_key"] = build_idempotency_key(manifest)
        result = assess_execution_readiness(
            manifest, intake_evidence=intake, duplicate_count=0, prior_state=None, now=FIXED_NOW
        )
        self.assertFalse(result["proceed"])
        self.assertNotIn("routing:mapping_not_approved", result["reasons"])

    def test_production_target_and_unknown_field_fail_closed(self):
        manifest = valid_manifest()
        manifest["target_environment"] = "production"
        manifest["browser_cookie"] = "forbidden"
        errors = validate_manifest(manifest, now=FIXED_NOW)
        self.assertIn("target_environment:blocked", errors)
        self.assertIn("manifest.browser_cookie:unexpected", errors)

    def test_mfa_self_approval_and_expiry_fail_closed(self):
        manifest = valid_manifest()
        manifest["primary_user"]["mfa_enabled"] = False
        manifest["approval"]["approved_by"] = manifest["approval"]["requested_by"]
        manifest["approval"]["expires_at"] = "2026-08-24T19:00:00Z"
        errors = validate_manifest(manifest, now=FIXED_NOW)
        self.assertIn("primary_user.mfa_enabled:must_be_true", errors)
        self.assertIn("approval:self_approval_blocked", errors)
        self.assertIn("approval:expired", errors)

    def test_manifest_tampering_breaks_hash_and_idempotency(self):
        manifest = valid_manifest()
        original_key = manifest["idempotency_key"]
        manifest["settings"]["api_access"] = False
        errors = validate_manifest(manifest, now=FIXED_NOW)
        self.assertIn("approval.manifest_sha256:mismatch", errors)
        manifest["approval"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
        self.assertNotEqual(build_idempotency_key(manifest), original_key)

    def test_transport_correlation_does_not_change_intent_or_idempotency(self):
        first = valid_manifest()
        second = copy.deepcopy(first)
        second["correlation_id"] = "synthetic-job-002"
        self.assertEqual(canonical_manifest_sha256(first), canonical_manifest_sha256(second))
        self.assertEqual(build_idempotency_key(first), build_idempotency_key(second))

    def test_future_or_long_lived_approval_is_blocked(self):
        manifest = valid_manifest()
        manifest["approval"]["approved_at"] = "2026-08-24T20:30:00Z"
        manifest["approval"]["expires_at"] = "2026-08-24T22:00:00Z"
        errors = validate_manifest(manifest, now=FIXED_NOW)
        self.assertIn("approval:future_timestamp", errors)
        self.assertIn("approval:ttl_too_long", errors)

    def test_duplicate_and_uncertain_prior_attempt_block(self):
        result = assess_execution_readiness(
            valid_manifest(), intake_evidence=None, duplicate_count=1, prior_state="uncertain", now=FIXED_NOW
        )
        self.assertFalse(result["proceed"])
        self.assertIn("duplicate_check:zero_required", result["reasons"])
        self.assertIn("idempotency:prior_uncertain", result["reasons"])

    def test_boolean_duplicate_count_and_unknown_or_complete_state_fail_closed(self):
        manifest = valid_manifest()
        for prior_state in ("complete", "made-up-state"):
            result = assess_execution_readiness(
                manifest,
                intake_evidence=None,
                duplicate_count=False,
                prior_state=prior_state,
                now=FIXED_NOW,
            )
            self.assertFalse(result["proceed"])
            self.assertIn("duplicate_check:zero_required", result["reasons"])
        unknown = assess_execution_readiness(
            manifest, intake_evidence=None, duplicate_count=0, prior_state="made-up-state", now=FIXED_NOW
        )
        self.assertIn("idempotency:unknown_prior_state", unknown["reasons"])

    def test_leaked_credentials_dependency_is_enforced(self):
        manifest = valid_manifest()
        manifest["attack_modules"]["leaked_credentials"] = False
        errors = validate_manifest(manifest, now=FIXED_NOW)
        self.assertIn("attack_modules.leaked_credentials:inconsistent_disabled_state", errors)

    def test_origin_allowlist_rejects_production_userinfo_and_alt_port(self):
        self.assertTrue(is_allowed_leonardo_url("https://leonardo.dev.app.pentera.io/tenant-management"))
        self.assertFalse(is_allowed_leonardo_url("https://app.pentera.io/"))
        self.assertFalse(is_allowed_leonardo_url("https://user@leonardo.dev.app.pentera.io/"))
        self.assertFalse(is_allowed_leonardo_url("https://leonardo.dev.app.pentera.io:8443/"))
        self.assertFalse(is_allowed_leonardo_url("https://leonardo.dev.app.pentera.io:bad/"))
        self.assertFalse(is_allowed_leonardo_url(" https://leonardo.dev.app.pentera.io/"))

    def test_safe_evidence_excludes_customer_and_authentication_fields(self):
        evidence = safe_evidence(valid_manifest(), now=FIXED_NOW)
        serialized = json.dumps(evidence)
        self.assertNotIn("surface-phase2.invalid", serialized)
        self.assertNotIn("Synthetic CE Test", serialized)
        self.assertNotIn("primary_user", serialized)
        self.assertNotIn("account", serialized)

    def test_request_and_result_schemas_are_strict_json(self):
        root = Path(__file__).parent / "contracts"
        for name in (
            "create-request.schema.json",
            "create-result.schema.json",
            "case3-intake-request.schema.json",
            "case3-preflight-response.schema.json",
        ):
            schema = json.loads((root / name).read_text(encoding="utf-8"))
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["type"], "object")

    def test_synthetic_blocked_intake_fixture_is_fail_closed_and_non_derived(self):
        path = Path(__file__).parent / "fixtures" / "synthetic-blocked-intake-status.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(fixture),
            {
                "fixture_version",
                "recorded_at",
                "scope",
                "fixture_classification",
                "data_origin",
                "data_sensitivity",
                "co_number",
                "sf_record_id",
                "source_revision",
                "record_match_count",
                "onboarding_product",
                "onboarding_type",
                "onboarding_stage",
                "approval_status",
                "submission_date",
                "surface_account_id_present",
                "account_uuid_present",
                "decision",
                "proceed",
                "eligible_for_automation",
                "leonardo_request_allowed",
                "reasons",
                "contains_customer_contact_or_domain_data",
                "contains_internal_or_customer_derived_identifiers",
            },
        )
        self.assertEqual(fixture["fixture_version"], "synthetic-blocked-intake-v1")
        self.assertEqual(fixture["co_number"], "CO-000000")
        self.assertEqual(fixture["sf_record_id"], "a00000000000000AAA")
        self.assertEqual(fixture["source_revision"], "2000-01-01T00:00:00Z")
        self.assertEqual(fixture["record_match_count"], 1)
        self.assertEqual(
            fixture["fixture_classification"],
            "fully synthetic blocked intake regression",
        )
        self.assertEqual(fixture["data_origin"], "fully_synthetic")
        self.assertTrue(fixture["data_sensitivity"].startswith("synthetic;"))
        self.assertNotIn("restricted", fixture["data_sensitivity"])
        self.assertIsNone(fixture["approval_status"])
        self.assertIsNone(fixture["submission_date"])
        self.assertFalse(fixture["surface_account_id_present"])
        self.assertFalse(fixture["account_uuid_present"])
        self.assertFalse(fixture["proceed"])
        self.assertFalse(fixture["eligible_for_automation"])
        self.assertFalse(fixture["leonardo_request_allowed"])
        self.assertEqual(fixture["decision"], "blocked")
        self.assertEqual(
            fixture["reasons"],
            [
                "approval_missing",
                "submission_date_missing",
                "dealhub_and_contract_evidence_not_verified",
                "case_mapping_not_approved",
                "leonardo_duplicate_check_not_performed",
                "leonardo_execution_not_authorized",
            ],
        )
        self.assertFalse(fixture["contains_customer_contact_or_domain_data"])
        self.assertFalse(fixture["contains_internal_or_customer_derived_identifiers"])

    def test_synthetic_blocked_intake_cannot_release_a_resealed_manifest(self):
        path = Path(__file__).parent / "fixtures" / "synthetic-blocked-intake-status.json"
        intake = json.loads(path.read_text(encoding="utf-8"))
        manifest = valid_manifest()
        manifest["source"] = {
            "sf_record_id": intake["sf_record_id"],
            "co_number": intake["co_number"],
            "source_revision": intake["source_revision"],
        }
        manifest["approval"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
        manifest["idempotency_key"] = build_idempotency_key(manifest)
        result = assess_execution_readiness(
            manifest, intake_evidence=intake, duplicate_count=0, prior_state=None, now=FIXED_NOW
        )
        self.assertFalse(result["proceed"])
        self.assertIn("intake_evidence:blocked", result["reasons"])
        self.assertIn("intake_evidence:leonardo_not_allowed", result["reasons"])


class ResultPolicyTests(unittest.TestCase):
    def test_verified_result_requires_uuid_and_complete_readback(self):
        manifest = valid_manifest()
        result = {
            "contract_version": "leonardo-dev-result-v1",
            "correlation_id": manifest["correlation_id"],
            "idempotency_key": manifest["idempotency_key"],
            "manifest_sha256": manifest["approval"]["manifest_sha256"],
            "runner_job_id": "synthetic-runner-job-1",
            "target_environment": "leonardo-development",
            "outcome": "verified",
            "leonardo_account_uuid": None,
            "verification": {
                "method": "owner-approved-read-only-ui",
                "all_fields_match": True,
                "all_settings_match": False,
                "verified_at": "2026-08-24T20:30:00Z",
            },
            "error": {"category": None, "retry_allowed": False},
        }
        errors = validate_result(result)
        self.assertIn("leonardo_account_uuid:required_text", errors)
        self.assertIn("verification.all_settings_match:must_be_true", errors)

    def test_uncertain_result_forbids_uuid_and_retry(self):
        manifest = valid_manifest()
        result = {
            "contract_version": "leonardo-dev-result-v1",
            "correlation_id": manifest["correlation_id"],
            "idempotency_key": manifest["idempotency_key"],
            "manifest_sha256": manifest["approval"]["manifest_sha256"],
            "runner_job_id": "synthetic-runner-job-1",
            "target_environment": "leonardo-development",
            "outcome": "uncertain",
            "leonardo_account_uuid": "must-not-be-trusted",
            "verification": {
                "method": None,
                "all_fields_match": False,
                "all_settings_match": False,
                "verified_at": None,
            },
            "error": {"category": "ambiguous_submit", "retry_allowed": True},
        }
        errors = validate_result(result)
        self.assertIn("leonardo_account_uuid:forbidden_unless_verified", errors)
        self.assertIn("error.retry_allowed:must_be_false", errors)

    def test_result_must_bind_to_exact_manifest(self):
        manifest = valid_manifest()
        result = {
            "contract_version": "leonardo-dev-result-v1",
            "correlation_id": manifest["correlation_id"],
            "idempotency_key": manifest["idempotency_key"],
            "manifest_sha256": "0" * 64,
            "runner_job_id": None,
            "target_environment": "leonardo-development",
            "outcome": "blocked",
            "leonardo_account_uuid": None,
            "verification": {
                "method": None,
                "all_fields_match": False,
                "all_settings_match": False,
                "verified_at": None,
            },
            "error": {"category": "preflight_blocked", "retry_allowed": False},
        }
        errors = validate_result_for_manifest(result, manifest)
        self.assertIn("result_binding.manifest_sha256:mismatch", errors)
        self.assertIn("result_attestation:not_verified", errors)

    def test_verified_result_is_bound_and_requires_trusted_attestation(self):
        manifest = valid_manifest()
        result = {
            "contract_version": "leonardo-dev-result-v1",
            "correlation_id": manifest["correlation_id"],
            "idempotency_key": manifest["idempotency_key"],
            "manifest_sha256": manifest["approval"]["manifest_sha256"],
            "runner_job_id": "synthetic-runner-job-verified",
            "target_environment": "leonardo-development",
            "outcome": "verified",
            "leonardo_account_uuid": "a385d820-333a-48a0-bb00-34cc0e8f0d1d",
            "verification": {
                "method": "owner-approved-read-only-ui",
                "all_fields_match": True,
                "all_settings_match": True,
                "verified_at": "2026-08-24T20:30:00Z",
            },
            "error": {"category": None, "retry_allowed": False},
        }
        self.assertEqual(
            validate_result_for_manifest(result, manifest, attestation_verified=True), []
        )


if __name__ == "__main__":
    unittest.main()
