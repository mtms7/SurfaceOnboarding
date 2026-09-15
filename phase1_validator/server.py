"""Local-only, read-only comparison service for Phase 1 shadow validation.

The service deliberately accepts normalized DealHub and contract values only.
It does not fetch Salesforce data, read documents, write files, retain request
bodies, or call any Surface/BackOffice endpoint.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


HOST = "127.0.0.1"
PORT = int(os.environ.get("PHASE1_VALIDATOR_PORT", "8787"))
MAX_BODY_BYTES = 256 * 1024
POLICY_VERSION = "phase1-shadow-v1"
BOUND_VALIDATE_PATH = "/v1/validate-bound"
COMPARISON_FIELDS = (
    "product",
    "start_date",
    "end_date",
    "status",
    "subscription_name",
    "license_type",
    "quantity",
)
BOUND_TOP_LEVEL_FIELDS = {
    "co_number",
    "sf_record_id",
    "source_revision",
    "request_id",
    "policy_version",
    "dealhub",
    "contract",
}
CO_NUMBER_PATTERN = re.compile(r"^CO-[0-9]+$")
SALESFORCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
MAX_SCALAR_CHARS = 256


def _display(value: Any) -> str:
    """Return a bounded scalar for a field-level mismatch report."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()[:256]
    return ""


def _normalized(value: Any) -> str:
    """Normalize only for comparison; retain display values for the report."""
    return " ".join(_display(value).casefold().split())


def _validate_source(name: str, source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError(f"{name} must be an object containing normalized fields")
    return source


def _load_json_without_duplicate_keys(raw: bytes) -> Any:
    """Decode JSON and reject duplicate object keys at every nesting level."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)


def _strict_scalar(source_name: str, field: str, value: Any) -> str:
    """Validate one normalized scalar and return its canonical string."""
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{source_name}.{field} must be a scalar value")
    if field == "quantity" and isinstance(value, bool):
        raise ValueError(f"{source_name}.quantity must be a positive integer or empty")

    displayed = str(value).strip()
    if len(displayed) > MAX_SCALAR_CHARS:
        raise ValueError(f"{source_name}.{field} exceeds {MAX_SCALAR_CHARS} characters")
    if any(ord(character) < 32 for character in displayed):
        raise ValueError(f"{source_name}.{field} contains control characters")

    if field in {"start_date", "end_date"} and displayed:
        try:
            parsed = datetime.strptime(displayed, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(f"{source_name}.{field} must use YYYY-MM-DD") from error
        if parsed.strftime("%Y-%m-%d") != displayed:
            raise ValueError(f"{source_name}.{field} must use YYYY-MM-DD")

    if field == "quantity" and displayed:
        if not displayed.isdigit() or int(displayed) <= 0:
            raise ValueError(f"{source_name}.quantity must be a positive integer or empty")
        return str(int(displayed))

    return " ".join(displayed.casefold().split())


def _strict_source(name: str, source: Any) -> tuple[dict[str, Any], dict[str, str]]:
    source = _validate_source(name, source)
    source_keys = set(source)
    expected_keys = set(COMPARISON_FIELDS)
    unknown = sorted(source_keys - expected_keys)
    missing = sorted(expected_keys - source_keys)
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{name} is missing fields: {', '.join(missing)}")
    canonical = {
        field: _strict_scalar(name, field, source[field])
        for field in COMPARISON_FIELDS
    }
    return source, canonical


def _bounded_control(payload: dict[str, Any], field: str, limit: int = 128) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value or len(value) > limit or any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} is invalid")
    return value


def compare_bound(payload: dict[str, Any]) -> dict[str, Any]:
    """Strict Phase 2 wrapper that binds the shadow decision to source evidence."""
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    unknown = sorted(set(payload) - BOUND_TOP_LEVEL_FIELDS)
    missing = sorted(BOUND_TOP_LEVEL_FIELDS - set(payload))
    if unknown:
        raise ValueError(f"request contains unsupported fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"request is missing fields: {', '.join(missing)}")

    co_number = _bounded_control(payload, "co_number", 32)
    if not CO_NUMBER_PATTERN.fullmatch(co_number):
        raise ValueError("co_number must match CO-[0-9]+")

    sf_record_id = _bounded_control(payload, "sf_record_id", 18)
    if not SALESFORCE_ID_PATTERN.fullmatch(sf_record_id):
        raise ValueError("sf_record_id must be a 15- or 18-character Salesforce ID")

    source_revision = _bounded_control(payload, "source_revision", 64)
    try:
        parsed_revision = datetime.fromisoformat(source_revision.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("source_revision must be an ISO-8601 timestamp") from error
    if parsed_revision.tzinfo is None:
        raise ValueError("source_revision must include a timezone")

    request_id = _bounded_control(payload, "request_id", 36)
    try:
        if str(uuid.UUID(request_id)) != request_id.casefold():
            raise ValueError
    except ValueError as error:
        raise ValueError("request_id must be a canonical UUID") from error

    policy_version = _bounded_control(payload, "policy_version", 64)
    if policy_version != POLICY_VERSION:
        raise ValueError("policy_version does not match the active policy")

    dealhub, canonical_dealhub = _strict_source("dealhub", payload.get("dealhub"))
    contract, canonical_contract = _strict_source("contract", payload.get("contract"))

    evidence_material = {
        "co_number": co_number,
        "sf_record_id": sf_record_id,
        "source_revision": source_revision,
        "policy_version": policy_version,
        "dealhub": canonical_dealhub,
        "contract": canonical_contract,
    }
    evidence_hash = sha256(
        json.dumps(evidence_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    baseline_result = compare(
        {"co_number": co_number, "dealhub": dealhub, "contract": contract}
    )
    safe_mismatches = [
        {"field": mismatch["field"], "type": mismatch["type"]}
        for mismatch in baseline_result["mismatches"]
    ]
    result = {
        "decision": baseline_result["decision"],
        "proceed": False,
        "action_required": baseline_result["action_required"],
        "policy_version": policy_version,
        "co_number": co_number,
        "sf_record_id": sf_record_id,
        "source_revision": source_revision,
        "request_id": request_id,
        "evidence_hash": evidence_hash,
        "mismatches": safe_mismatches,
        "review_summary": baseline_result["review_summary"],
    }
    result["result_hash"] = sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return result


def compare(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare normalized evidence and always remain non-provisioning."""
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    co_number = _display(payload.get("co_number"))
    if not co_number:
        raise ValueError("co_number is required")

    dealhub = _validate_source("dealhub", payload.get("dealhub"))
    contract = _validate_source("contract", payload.get("contract"))

    mismatches: list[dict[str, str]] = []
    for field in COMPARISON_FIELDS:
        dealhub_value = _display(dealhub.get(field))
        contract_value = _display(contract.get(field))
        if not dealhub_value or not contract_value:
            mismatches.append(
                {
                    "field": field,
                    "type": "missing_required_field",
                    "dealhub_value": dealhub_value or "not provided",
                    "contract_value": contract_value or "not provided",
                }
            )
        elif _normalized(dealhub_value) != _normalized(contract_value):
            mismatches.append(
                {
                    "field": field,
                    "type": "source_data_mismatch",
                    "dealhub_value": dealhub_value,
                    "contract_value": contract_value,
                }
            )

    if mismatches:
        return {
            "decision": "manual_review_required",
            "proceed": False,
            "action_required": "contact_csm",
            "policy_version": POLICY_VERSION,
            "co_number": co_number,
            "mismatches": mismatches,
            "review_summary": (
                f"Phase 1 evidence mismatch for {co_number}: "
                f"{len(mismatches)} field(s) require CSM review."
            ),
        }

    return {
        "decision": "approved_for_review",
        "proceed": False,
        "action_required": "human_review_required",
        "policy_version": POLICY_VERSION,
        "co_number": co_number,
        "mismatches": [],
        "review_summary": (
            f"Phase 1 evidence matched for {co_number}. "
            "Shadow mode is active; no provisioning action is authorized."
        ),
    }


class ValidatorHandler(BaseHTTPRequestHandler):
    """A no-persistence HTTP handler used only by the local OPA host."""

    server_version = "Phase1Validator/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Never write request paths or values to stdout/stderr or system logs.
        return

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok", "policy_version": POLICY_VERSION})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path not in {"/v1/validate", BOUND_VALIDATE_PATH}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None or not content_length.isdigit():
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return

        length = int(content_length)
        if length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return

        try:
            payload = _load_json_without_duplicate_keys(self.rfile.read(length))
            result = compare_bound(payload) if self.path == BOUND_VALIDATE_PATH else compare(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": str(error)})
            return

        self._send_json(HTTPStatus.OK, result)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ValidatorHandler)
    print(f"Phase 1 validator listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
