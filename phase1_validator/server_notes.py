"""Isolated Phase 1 notes-enabled validator for shadow-mode testing.

This temporary service wraps the proven port-8787 comparison behaviour without
changing it. Run it on port 8788 and point only a separate Workato test
connection at it. It has no persistence, Salesforce calls, document reads, or
provisioning capability.
"""

from __future__ import annotations

import json
import os
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from phase1_validator import server as baseline
from phase1_validator.rejection_notes import render_rejection_note


HOST = "127.0.0.1"
PORT = int(os.environ.get("PHASE1_NOTES_VALIDATOR_PORT", "8788"))
POLICY_VERSION = baseline.POLICY_VERSION
BLOCKING_GATE_LABELS = {
    "approval_pending": "approval_status: Pending",
    "commercial_evidence_unverified": "commercial_evidence: requires visual confirmation",
    "domain_validation_failed": "domain_validation: failed",
    "duplicate_account_check_required": "duplicate_account_check: required",
}


def _blocking_gates(payload: dict[str, Any]) -> list[str]:
    """Accept only predefined codes; never permit arbitrary note content."""
    raw_gates = payload.get("blocking_gates", [])
    if raw_gates is None:
        return []
    if not isinstance(raw_gates, list) or not all(isinstance(gate, str) for gate in raw_gates):
        raise ValueError("blocking_gates must be a list of supported gate codes")
    if any(gate not in BLOCKING_GATE_LABELS for gate in raw_gates):
        raise ValueError("unsupported blocking gate code")
    return [BLOCKING_GATE_LABELS[gate] for gate in raw_gates]


def _result_hash(
    co_number: str,
    decision: str,
    mismatches: list[dict[str, str]],
    blocking_gates: list[str],
) -> str:
    material = {
        "policy_version": POLICY_VERSION,
        "co_number": co_number,
        "decision": decision,
        "mismatches": mismatches,
        "blocking_gates": blocking_gates,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def compare(payload: dict[str, Any]) -> dict[str, Any]:
    """Return baseline decision plus safe note metadata for Workato testing."""
    blocking_gates = _blocking_gates(payload)
    result = baseline.compare(payload)
    co_number = result["co_number"]
    decision = result["decision"]
    mismatches = result["mismatches"]

    result["blocking_gates"] = blocking_gates
    result["result_hash"] = _result_hash(
        co_number, decision, mismatches, blocking_gates
    )

    if decision == "manual_review_required":
        result["onboarding_rejection_note"] = render_rejection_note(
            co_number=co_number,
            mismatches=mismatches,
            blocking_gates=blocking_gates,
        )
        result["should_append_rejection_note"] = True
        result["review_summary"] = (
            f"Phase 1 evidence mismatch for {co_number}: "
            f"{len(mismatches)} field(s) require CSM review."
        )
    else:
        result["onboarding_rejection_note"] = ""
        result["should_append_rejection_note"] = False

    return result


class NotesValidatorHandler(BaseHTTPRequestHandler):
    """No-persistence HTTP handler for the isolated notes test service."""

    server_version = "Phase1ValidatorNotes/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "policy_version": POLICY_VERSION,
                    "capability": "rejection_note",
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/validate":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None or not content_length.isdigit():
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return
        length = int(content_length)
        if length > baseline.MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            result = compare(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": str(error)},
            )
            return

        self._send_json(HTTPStatus.OK, result)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), NotesValidatorHandler)
    print(f"Phase 1 notes validator listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
