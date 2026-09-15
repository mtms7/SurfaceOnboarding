"""Fail-closed, dependency-free Phase 2 request and result policy.

This module performs no network, browser, filesystem-write, or external-system
operations. It is intentionally small enough to reuse in local tests and to
translate into Workato formulas or a future approved runner.
"""

from __future__ import annotations

import copy
import hashlib
import re
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID


ALLOWED_LEONARDO_ORIGIN = "https://leonardo.dev.app.pentera.io"
CONTRACT_VERSION = "leonardo-dev-create-v2"
RESULT_CONTRACT_VERSION = "leonardo-dev-result-v1"
TARGET_ENVIRONMENT = "leonardo-development"

ENGINE_VALUES = {
    "case_1_new_surface_only",
    "case_2_new_ce_only",
    "case_3_combined_baseline",
    "case_4_renew_surface_new_ce",
    "case_5_renew_ce_new_surface",
    "case_6_renew_both",
}
OPERATIONS = {
    "validate_only",
    "read_only_preflight",
    "fill_and_pause",
    "read_only_verify",
}
INTERVALS = {"None", "Daily", "Weekly", "Monthly"}
LICENSE_TYPES = {
    "Evaluation",
    "Trial",
    "Prepaid monthly subscription",
    "Prepaid annual subscription",
    "PAYG monthly subscription",
}
PRIOR_STATES_THAT_BLOCK = {"pending", "submitted", "verification_pending", "verified", "uncertain"}
MAX_APPROVAL_TTL_SECONDS = 60 * 60
KNOWN_PRIOR_STATES = {
    None,
    "new",
    "approved_for_preflight",
    "ready_for_attended_execution",
    "pending",
    "submitted",
    "verification_pending",
    "verified",
    "writeback_pending",
    "complete",
    "uncertain",
    "blocked",
}
# v4 records a narrow set of owner-provided normalization rules for validate-only
# preparation. The remaining Leonardo settings, module, license, duplicate, and
# execution semantics are still unsigned. Keep the revision visible for audit,
# but do not let it clear the execution mapping gate.
PARTIALLY_REVIEWED_MAPPING_POLICIES: frozenset[tuple[str, str]] = frozenset(
    {("case_3_combined_baseline", "surface-case3-partial-owner-mapping-2026-08-31-v5")}
)
EXECUTION_APPROVED_MAPPING_POLICIES: frozenset[tuple[str, str]] = frozenset()
ERROR_CATEGORIES = {
    "approval_blocked",
    "authentication_failed",
    "authorization_failed",
    "ambiguous_submit",
    "contract_invalid",
    "duplicate_blocked",
    "environment_blocked",
    "preflight_blocked",
    "readback_failed",
    "schema_drift",
    "timeout",
    "unexpected_response",
}
VERIFICATION_METHODS = {"owner-approved-read-only-ui", "supported-service-readback"}

TOP_LEVEL_KEYS = {
    "contract_version",
    "target_environment",
    "correlation_id",
    "idempotency_key",
    "source",
    "routing",
    "approval",
    "account",
    "primary_user",
    "settings",
    "attack_modules",
    "license",
}
SECTION_KEYS = {
    "source": {"sf_record_id", "co_number", "source_revision"},
    "routing": {"engine_value", "policy_version"},
    "approval": {
        "request_key",
        "requested_by",
        "approved_by",
        "approver_group",
        "attestation_id",
        "decision",
        "approval_revision",
        "approved_at",
        "expires_at",
        "operation",
        "manifest_sha256",
    },
    "account": {
        "company_name",
        "account_type",
        "primary_domain",
        "alternate_domains",
        "subdomains",
        "email_domains",
        "networks",
        "country",
    },
    "primary_user": {
        "first_name",
        "last_name",
        "email",
        "phone",
        "job_title",
        "mfa_enabled",
        "operator_account",
    },
    "settings": {
        "scanning_interval",
        "scan_now",
        "maximum_scan_duration_hours",
        "automated_discovery",
        "recon_subdomains",
        "multiple_attack_stacks",
        "mas_for_subdomains",
        "web_dictionary_brute_force",
        "web_dorking",
        "nuclei",
        "authenticated_testing",
        "static_outbound_ip",
        "ai",
        "notifications",
        "multiple_users",
        "api_access",
    },
    "attack_modules": {
        "phishing",
        "leaked_credentials",
        "leaked_credentials_interval",
        "leaked_credentials_domains",
    },
    "license": {
        "include_provisioning",
        "include_subdomains",
        "type",
        "number_of_assets",
        "number_of_domains",
        "number_of_subdomains",
        "start_date",
        "expiration_date",
    },
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_manifest_sha256(manifest: dict[str, Any]) -> str:
    """Hash immutable execution intent, excluding transport and approval data."""
    review_body = {
        key: copy.deepcopy(manifest.get(key))
        for key in (
            "contract_version",
            "target_environment",
            "source",
            "routing",
            "account",
            "primary_user",
            "settings",
            "attack_modules",
            "license",
        )
    }
    return hashlib.sha256(_canonical_json(review_body).encode("utf-8")).hexdigest()


def build_idempotency_key(manifest: dict[str, Any]) -> str:
    """Bind one execution to target, source revision, route, and approval."""
    source = manifest.get("source", {})
    routing = manifest.get("routing", {})
    approval = manifest.get("approval", {})
    values = (
        manifest.get("contract_version", ""),
        manifest.get("target_environment", ""),
        source.get("sf_record_id", ""),
        source.get("source_revision", ""),
        routing.get("engine_value", ""),
        routing.get("policy_version", ""),
        approval.get("approval_revision", ""),
        approval.get("operation", ""),
        approval.get("manifest_sha256", ""),
    )
    return hashlib.sha256("|".join(str(value) for value in values).encode("utf-8")).hexdigest()


def is_allowed_leonardo_url(url: str) -> bool:
    """Allow HTTPS URLs only on the exact Leonardo Development origin."""
    if (
        not isinstance(url, str)
        or url != url.strip()
        or any(ord(character) < 32 for character in url)
    ):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "leonardo.dev.app.pentera.io"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _require_exact_keys(value: Any, expected: set[str], path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path}:must_be_object")
        return {}
    missing = expected - set(value)
    extra = set(value) - expected
    errors.extend(f"{path}.{key}:missing" for key in sorted(missing))
    errors.extend(f"{path}.{key}:unexpected" for key in sorted(extra))
    return value


def _require_text(value: Any, path: str, errors: list[str]) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        errors.append(f"{path}:required_text")


def _require_string_list(value: Any, path: str, errors: list[str]) -> None:
    if (
        not isinstance(value, list)
        or len(value) > 1000
        or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in value
        )
    ):
        errors.append(f"{path}:must_be_string_array")


def _parse_utc(value: Any, path: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{path}:invalid_timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path}:invalid_timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{path}:timezone_required")
        return None
    return parsed.astimezone(timezone.utc)


def validate_manifest(manifest: Any, *, now: datetime | None = None) -> list[str]:
    """Return stable error codes; an empty list means locally valid, not approved."""
    errors: list[str] = []
    root = _require_exact_keys(manifest, TOP_LEVEL_KEYS, "manifest", errors)
    if not root:
        return errors

    sections = {
        name: _require_exact_keys(root.get(name), keys, name, errors)
        for name, keys in SECTION_KEYS.items()
    }

    if root.get("contract_version") != CONTRACT_VERSION:
        errors.append("contract_version:unsupported")
    if root.get("target_environment") != TARGET_ENVIRONMENT:
        errors.append("target_environment:blocked")
    _require_text(root.get("correlation_id"), "correlation_id", errors)

    source = sections["source"]
    for key in SECTION_KEYS["source"]:
        _require_text(source.get(key), f"source.{key}", errors)
    if not re.fullmatch(r"CO-[0-9]+", str(source.get("co_number", ""))):
        errors.append("source.co_number:invalid")

    routing = sections["routing"]
    if routing.get("engine_value") not in ENGINE_VALUES:
        errors.append("routing.engine_value:unsupported")
    _require_text(routing.get("policy_version"), "routing.policy_version", errors)

    approval = sections["approval"]
    for key in (
        "request_key",
        "requested_by",
        "approved_by",
        "approver_group",
        "attestation_id",
        "approved_at",
        "expires_at",
        "manifest_sha256",
    ):
        _require_text(approval.get(key), f"approval.{key}", errors)
    if approval.get("decision") != "approved":
        errors.append("approval.decision:must_be_approved")
    approval_revision = approval.get("approval_revision")
    if not isinstance(approval_revision, int) or isinstance(approval_revision, bool) or approval_revision < 1:
        errors.append("approval.approval_revision:must_be_positive_integer")
    if approval.get("requested_by") == approval.get("approved_by"):
        errors.append("approval:self_approval_blocked")
    if approval.get("operation") not in OPERATIONS:
        errors.append("approval.operation:unsupported")
    approved_at = _parse_utc(approval.get("approved_at"), "approval.approved_at", errors)
    expires_at = _parse_utc(approval.get("expires_at"), "approval.expires_at", errors)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if approved_at and approved_at > current_time:
        errors.append("approval:future_timestamp")
    if approved_at and expires_at and expires_at <= approved_at:
        errors.append("approval:invalid_window")
    if approved_at and expires_at and (expires_at - approved_at).total_seconds() > MAX_APPROVAL_TTL_SECONDS:
        errors.append("approval:ttl_too_long")
    if expires_at and expires_at <= current_time:
        errors.append("approval:expired")

    account = sections["account"]
    for key in ("company_name", "primary_domain", "country"):
        _require_text(account.get(key), f"account.{key}", errors)
    if account.get("account_type") not in {"Customer", "Demo"}:
        errors.append("account.account_type:unsupported")
    for key in ("alternate_domains", "subdomains", "email_domains", "networks"):
        _require_string_list(account.get(key), f"account.{key}", errors)

    primary_user = sections["primary_user"]
    for key in ("first_name", "last_name", "email"):
        _require_text(primary_user.get(key), f"primary_user.{key}", errors)
    operator_account = primary_user.get("operator_account")
    if operator_account is not None:
        _require_text(operator_account, "primary_user.operator_account", errors)
    for key in ("phone", "job_title"):
        if not isinstance(primary_user.get(key), str):
            errors.append(f"primary_user.{key}:must_be_string")
    if primary_user.get("mfa_enabled") is not True:
        errors.append("primary_user.mfa_enabled:must_be_true")

    settings = sections["settings"]
    if settings.get("scanning_interval") not in INTERVALS:
        errors.append("settings.scanning_interval:unsupported")
    for key in SECTION_KEYS["settings"] - {"scanning_interval", "maximum_scan_duration_hours"}:
        if not isinstance(settings.get(key), bool):
            errors.append(f"settings.{key}:must_be_boolean")
    duration = settings.get("maximum_scan_duration_hours")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        errors.append("settings.maximum_scan_duration_hours:must_be_positive_integer")
    if settings.get("mas_for_subdomains") is True and settings.get("multiple_attack_stacks") is not True:
        errors.append("settings.mas_for_subdomains:requires_multiple_attack_stacks")

    modules = sections["attack_modules"]
    for key in ("phishing", "leaked_credentials"):
        if not isinstance(modules.get(key), bool):
            errors.append(f"attack_modules.{key}:must_be_boolean")
    if modules.get("leaked_credentials_interval") not in INTERVALS:
        errors.append("attack_modules.leaked_credentials_interval:unsupported")
    _require_string_list(modules.get("leaked_credentials_domains"), "attack_modules.leaked_credentials_domains", errors)
    if modules.get("leaked_credentials") is False and (
        modules.get("leaked_credentials_interval") != "None" or modules.get("leaked_credentials_domains")
    ):
        errors.append("attack_modules.leaked_credentials:inconsistent_disabled_state")
    if modules.get("leaked_credentials") is True and (
        modules.get("leaked_credentials_interval") == "None" or not modules.get("leaked_credentials_domains")
    ):
        errors.append("attack_modules.leaked_credentials:interval_and_domains_required")

    license_data = sections["license"]
    for key in ("include_provisioning", "include_subdomains"):
        if not isinstance(license_data.get(key), bool):
            errors.append(f"license.{key}:must_be_boolean")
    if license_data.get("type") not in LICENSE_TYPES:
        errors.append("license.type:unsupported")
    for key in ("number_of_assets", "number_of_domains", "number_of_subdomains"):
        value = license_data.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"license.{key}:must_be_nonnegative_integer")
    try:
        start = datetime.strptime(str(license_data.get("start_date")), "%Y-%m-%d").date()
        end = datetime.strptime(str(license_data.get("expiration_date")), "%Y-%m-%d").date()
        if end < start:
            errors.append("license:invalid_date_range")
    except ValueError:
        errors.append("license:invalid_date")

    expected_hash = canonical_manifest_sha256(root)
    if approval.get("manifest_sha256") != expected_hash:
        errors.append("approval.manifest_sha256:mismatch")
    if not re.fullmatch(r"[a-f0-9]{64}", str(root.get("idempotency_key", ""))):
        errors.append("idempotency_key:invalid_format")
    elif root.get("idempotency_key") != build_idempotency_key(root):
        errors.append("idempotency_key:mismatch")

    return sorted(set(errors))


def assess_execution_readiness(
    manifest: Any,
    *,
    intake_evidence: dict[str, Any] | None = None,
    duplicate_count: int | None,
    prior_state: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Combine contract, duplicate, and single-flight gates without executing."""
    reasons = validate_manifest(manifest, now=now)
    operation = manifest.get("approval", {}).get("operation") if isinstance(manifest, dict) else None
    routing = manifest.get("routing", {}) if isinstance(manifest, dict) else {}
    mapping_key = (routing.get("engine_value"), routing.get("policy_version"))
    if operation != "validate_only" and mapping_key not in EXECUTION_APPROVED_MAPPING_POLICIES:
        reasons.append("routing:mapping_not_approved")

    source = manifest.get("source", {}) if isinstance(manifest, dict) else {}
    if not isinstance(intake_evidence, dict):
        reasons.append("intake_evidence:missing")
    else:
        bindings = {
            "co_number": source.get("co_number"),
            "sf_record_id": source.get("sf_record_id"),
            "source_revision": source.get("source_revision"),
        }
        for key, expected in bindings.items():
            if intake_evidence.get(key) != expected:
                reasons.append(f"intake_evidence.{key}:mismatch")
        if intake_evidence.get("proceed") is not True:
            reasons.append("intake_evidence:blocked")
        if intake_evidence.get("leonardo_request_allowed") is not True:
            reasons.append("intake_evidence:leonardo_not_allowed")

    if prior_state not in KNOWN_PRIOR_STATES:
        reasons.append("idempotency:unknown_prior_state")
    if operation == "read_only_verify":
        if prior_state not in {"submitted", "verification_pending", "uncertain"}:
            reasons.append("idempotency:verification_state_required")
        if type(duplicate_count) is not int or duplicate_count != 1:
            reasons.append("readback:exactly_one_required")
    elif operation == "fill_and_pause":
        if prior_state not in {None, "new", "approved_for_preflight"}:
            reasons.append(f"idempotency:prior_{prior_state}")
        if type(duplicate_count) is not int or duplicate_count != 0:
            reasons.append("duplicate_check:zero_required")
        reasons.extend(
            [
                "authorization:trusted_workato_approval_not_verified",
                "preflight:attestation_not_verified",
                "idempotency_lock:not_acquired",
                "runner_transport:not_authenticated",
            ]
        )
    elif operation == "read_only_preflight":
        reasons.append("authorization:read_only_preflight_not_verified")

    # A local contract package can identify blockers but cannot authorize an
    # external action. Trusted adapters must verify attestations and acquire the
    # atomic lock in a future separately approved integration.
    return {
        "proceed": False,
        "state": "blocked" if reasons else "locally_valid_external_authorization_required",
        "local_contract_valid": not validate_manifest(manifest, now=now),
        "approved_operation": operation,
        "reasons": sorted(set(reasons)),
    }


def validate_result(result: Any) -> list[str]:
    errors: list[str] = []
    expected = {
        "contract_version",
        "correlation_id",
        "idempotency_key",
        "manifest_sha256",
        "runner_job_id",
        "target_environment",
        "outcome",
        "leonardo_account_uuid",
        "verification",
        "error",
    }
    root = _require_exact_keys(result, expected, "result", errors)
    if not root:
        return errors
    if root.get("contract_version") != RESULT_CONTRACT_VERSION:
        errors.append("contract_version:unsupported")
    if root.get("target_environment") != TARGET_ENVIRONMENT:
        errors.append("target_environment:blocked")
    for key in ("correlation_id", "idempotency_key", "manifest_sha256"):
        _require_text(root.get(key), key, errors)
    for key in ("idempotency_key", "manifest_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", str(root.get(key, ""))):
            errors.append(f"{key}:invalid_format")
    if not isinstance(root.get("runner_job_id"), str) and root.get("runner_job_id") is not None:
        errors.append("runner_job_id:must_be_string_or_null")
    if root.get("outcome") not in {"verified", "blocked", "failed", "uncertain"}:
        errors.append("outcome:unsupported")

    verification = _require_exact_keys(
        root.get("verification"),
        {"method", "all_fields_match", "all_settings_match", "verified_at"},
        "verification",
        errors,
    )
    error = _require_exact_keys(root.get("error"), {"category", "retry_allowed"}, "error", errors)
    if error and error.get("retry_allowed") is not False:
        errors.append("error.retry_allowed:must_be_false")

    if root.get("outcome") == "verified":
        _require_text(root.get("runner_job_id"), "runner_job_id", errors)
        _require_text(root.get("leonardo_account_uuid"), "leonardo_account_uuid", errors)
        if verification.get("method") not in VERIFICATION_METHODS:
            errors.append("verification.method:unsupported")
        _require_text(verification.get("verified_at"), "verification.verified_at", errors)
        _parse_utc(verification.get("verified_at"), "verification.verified_at", errors)
        try:
            UUID(str(root.get("leonardo_account_uuid")))
        except (ValueError, AttributeError):
            errors.append("leonardo_account_uuid:invalid_format")
        if verification.get("all_fields_match") is not True:
            errors.append("verification.all_fields_match:must_be_true")
        if verification.get("all_settings_match") is not True:
            errors.append("verification.all_settings_match:must_be_true")
    elif root.get("leonardo_account_uuid") not in (None, ""):
        errors.append("leonardo_account_uuid:forbidden_unless_verified")

    if root.get("outcome") == "verified" and error.get("category") not in (None, ""):
        errors.append("error.category:forbidden_when_verified")
    if root.get("outcome") in {"blocked", "failed", "uncertain"}:
        _require_text(error.get("category"), "error.category", errors)
        if error.get("category") not in ERROR_CATEGORIES:
            errors.append("error.category:unsupported")

    return sorted(set(errors))


def validate_result_for_manifest(
    result: Any,
    manifest: Any,
    *,
    attestation_verified: bool = False,
) -> list[str]:
    """Validate a result and bind it to the exact approved manifest."""
    errors = validate_result(result)
    if not isinstance(result, dict) or not isinstance(manifest, dict):
        return sorted(set(errors + ["result_binding:invalid_input"]))
    expected = {
        "correlation_id": manifest.get("correlation_id"),
        "idempotency_key": manifest.get("idempotency_key"),
        "manifest_sha256": manifest.get("approval", {}).get("manifest_sha256"),
        "target_environment": manifest.get("target_environment"),
    }
    for key, value in expected.items():
        if result.get(key) != value:
            errors.append(f"result_binding.{key}:mismatch")
    if attestation_verified is not True:
        errors.append("result_attestation:not_verified")
    return sorted(set(errors))


def safe_evidence(
    manifest: dict[str, Any],
    result: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project only explicitly permitted operational evidence fields."""
    manifest_errors = validate_manifest(manifest, now=now)
    if manifest_errors:
        raise ValueError("manifest must pass local validation before evidence projection")
    if result is not None and validate_result_for_manifest(result, manifest, attestation_verified=True):
        raise ValueError("result must pass validation and manifest binding before evidence projection")
    evidence = {
        "co_number": manifest.get("source", {}).get("co_number"),
        "sf_record_id": manifest.get("source", {}).get("sf_record_id"),
        "source_revision": manifest.get("source", {}).get("source_revision"),
        "correlation_id": manifest.get("correlation_id"),
        "idempotency_key": manifest.get("idempotency_key"),
        "engine_value": manifest.get("routing", {}).get("engine_value"),
        "policy_version": manifest.get("routing", {}).get("policy_version"),
        "contract_version": manifest.get("contract_version"),
        "manifest_sha256": manifest.get("approval", {}).get("manifest_sha256"),
        "target_environment": manifest.get("target_environment"),
        "approval_request_key": manifest.get("approval", {}).get("request_key"),
        "approved_by": manifest.get("approval", {}).get("approved_by"),
        "approved_at": manifest.get("approval", {}).get("approved_at"),
        "expires_at": manifest.get("approval", {}).get("expires_at"),
        "operation": manifest.get("approval", {}).get("operation"),
    }
    if result is not None:
        evidence.update(
            {
                "outcome": result.get("outcome"),
                "runner_job_id": result.get("runner_job_id"),
                "error_category": result.get("error", {}).get("category"),
                "leonardo_account_uuid": (
                    result.get("leonardo_account_uuid") if result.get("outcome") == "verified" else None
                ),
            }
        )
    return evidence
