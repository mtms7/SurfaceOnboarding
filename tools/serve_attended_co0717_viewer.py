"""One-user, loopback-only, read-only viewer for the fixed CO-0717 pilot.

Run manually on the operator's desktop after they authenticate with their own
Salesforce CLI browser login. This tool must never run on workato-opa-01,
receive a copied token, be proxied, or be used for another record.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding.salesforce_detail import (DETAIL_QUERY_FIELDS,
                                                       parse_co_0717_detail_document,
                                                       render_attended_co_0717_detail)


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8011
MAX_CLI_OUTPUT_BYTES = 256 * 1024
SOQL = (
    "SELECT Name, LastModifiedDate, Account_Name__c, Onboarding_Product__c, Onboarding_Type__c, "
    "Primary_User_Name__c, Main_Domain__c, Alternate_Domains__c, Email_Domains__c, "
    "Onboarding_Comments__c, Onboarding_Stage__c, Surface_Account_ID__c, Account_UUID__c "
    "FROM Customer_Onboarding__c WHERE Name = 'CO-0717' LIMIT 2"
)


class AttendedReadError(RuntimeError):
    """A deliberately generic error with no source, CLI, or authentication details."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AttendedReadError("invalid_salesforce_response")
        result[key] = value
    return result


def read_co_0717_once(run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str:
    """Return rendered HTML after one fixed query; never log or retain raw output."""
    try:
        completed = run(
            ["sf.cmd", "data", "query", "--query", SOQL, "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AttendedReadError("salesforce_read_unavailable") from error
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
        raise AttendedReadError("salesforce_read_unavailable")
    try:
        response = json.loads(completed.stdout, object_pairs_hook=_reject_duplicate_keys)
        result = response["result"]
        records = result["records"]
        if response.get("status") != 0 or not isinstance(records, list) or len(records) != 1:
            raise AttendedReadError("salesforce_detail_not_available")
        record = records[0]
        if not isinstance(record, dict) or set(record) != set(DETAIL_QUERY_FIELDS) | {"attributes"}:
            raise AttendedReadError("invalid_salesforce_response")
        normalized = [{field: record[field] for field in DETAIL_QUERY_FIELDS}]
        detail = parse_co_0717_detail_document(json.dumps(normalized, separators=(",", ":")))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, AttendedReadError) as error:
        raise AttendedReadError("salesforce_detail_not_available") from error
    return render_attended_co_0717_detail(detail)


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "AttendedCO0717Viewer"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        """Do not create request logs that could correlate customer data access."""

    def _headers(self, status: HTTPStatus, content_length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path != "/":
            self._headers(HTTPStatus.NOT_FOUND, 0)
            return
        try:
            body = read_co_0717_once().encode("utf-8")
        except AttendedReadError:
            body = b"<!doctype html><title>Unavailable</title><p>CO-0717 is unavailable for this attended read.</p>"
            self._headers(HTTPStatus.SERVICE_UNAVAILABLE, len(body))
            self.wfile.write(body)
            return
        self._headers(HTTPStatus.OK, len(body))
        self.wfile.write(body)


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Attend a CO-0717 Salesforce read on desktop loopback only.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(arguments)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    server = ThreadingHTTPServer((LOOPBACK_HOST, args.port), ViewerHandler)
    print(f"attended_co_0717_viewer_listening_on_{LOOPBACK_HOST}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
