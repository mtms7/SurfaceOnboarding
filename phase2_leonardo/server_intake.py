"""Loopback-only HTTP adapter for Phase 2 intake and manifest validation."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from phase1_validator.server import MAX_BODY_BYTES, _load_json_without_duplicate_keys
from phase2_leonardo.intake import MAPPING_POLICY_VERSION, IntakeError, prepare_case3
from phase2_leonardo.policy import assess_execution_readiness


HOST = "127.0.0.1"
PORT = int(os.environ.get("PHASE2_INTAKE_PORT", "8789"))
PREPARE_PATH = "/v1/phase2/prepare-case3"
VALIDATE_PATH = "/v1/phase2/validate-manifest"


class Phase2IntakeHandler(BaseHTTPRequestHandler):
    server_version = "SurfacePhase2Intake/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(HTTPStatus.OK, {"status": "ok", "capability": "phase2_case3_preflight", "policy_version": MAPPING_POLICY_VERSION})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {PREPARE_PATH, VALIDATE_PATH}:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        length_header = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdigit():
            self._send(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return
        length = int(length_header)
        if length > MAX_BODY_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return
        try:
            payload = _load_json_without_duplicate_keys(self.rfile.read(length))
            if self.path == PREPARE_PATH:
                result = prepare_case3(payload)
            else:
                envelope = payload if isinstance(payload, dict) else {}
                if set(envelope) != {"manifest", "intake_evidence", "duplicate_count", "prior_state"}:
                    raise IntakeError("manifest_validation:schema_mismatch")
                result = assess_execution_readiness(
                    envelope["manifest"],
                    intake_evidence=envelope["intake_evidence"],
                    duplicate_count=envelope["duplicate_count"],
                    prior_state=envelope["prior_state"],
                )
        except (UnicodeDecodeError, json.JSONDecodeError, IntakeError, ValueError) as error:
            code = error.code if isinstance(error, IntakeError) else "invalid_json_or_duplicate_key"
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "code": code})
            return
        self._send(HTTPStatus.OK, result)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Phase2IntakeHandler)
    print(f"Surface Phase 2 intake listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

