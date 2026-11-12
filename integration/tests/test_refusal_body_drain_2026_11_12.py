"""2026-11-12: desktop refusals read the unread POST body first (the flaky guards test, WinError 10053).

A 403 sent while the request body is still unread lets Windows reset the connection on close, and the client may see
the reset instead of the refusal. These tests drive the handler on in-memory streams, so they are deterministic.
"""
from __future__ import annotations

import io
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard

BODY = b"reference=CO-0767"


def handler(raw: bytes, port: int = 8765) -> dashboard.Handler:
    instance = dashboard.Handler.__new__(dashboard.Handler)
    instance.rfile = io.BytesIO(raw)
    instance.wfile = io.BytesIO()
    instance.client_address = ("127.0.0.1", 50000)
    instance.server = SimpleNamespace(server_address=("127.0.0.1", port))
    instance.close_connection = True
    return instance


class DiscardUnreadBodyTests(unittest.TestCase):
    def with_length(self, value: str | None, body: bytes = BODY) -> dashboard.Handler:
        instance = handler(body)
        headers = Message()
        if value is not None:
            headers["Content-Length"] = value
        instance.headers = headers
        instance.close_connection = False
        instance._discard_unread_body()
        return instance

    def test_small_body_is_read_and_never_parsed(self):
        instance = self.with_length(str(len(BODY)))
        self.assertEqual(instance.rfile.tell(), len(BODY))
        self.assertFalse(instance.close_connection)

    def test_missing_bad_or_oversize_length_closes_instead_of_reading(self):
        for value in (None, "0", "abc", "-5", "65537"):
            instance = self.with_length(value)
            self.assertEqual(instance.rfile.tell(), 0, value)
            self.assertTrue(instance.close_connection, value)


class RefusalPathTests(unittest.TestCase):
    def request(self, origin: str | None) -> bytes:
        lines = [b"POST /attended/confirm-user-created HTTP/1.1", b"Host: 127.0.0.1:8765",
                 b"Content-Type: application/x-www-form-urlencoded", b"Content-Length: " + str(len(BODY)).encode()]
        if origin is not None:
            lines.append(b"Origin: " + origin.encode())
        return b"\r\n".join(lines) + b"\r\n\r\n" + BODY

    def test_foreign_origin_refusal_reads_the_body_first(self):
        raw = self.request("http://evil.example")
        instance = handler(raw)
        instance.raw_requestline = instance.rfile.readline()
        self.assertFalse(instance.parse_request())
        self.assertEqual(instance.rfile.tell(), len(raw))
        self.assertRegex(instance.wfile.getvalue(), rb"^HTTP/1\.[01] 403 ")

    def test_signed_out_refusal_reads_the_body_first_and_records_nothing(self):
        raw = self.request(None)
        instance = handler(raw)
        instance.raw_requestline = instance.rfile.readline()
        self.assertTrue(instance.parse_request())
        with patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "login_required", return_value=True), \
                patch.object(dashboard, "dashboard_operator", return_value=None), \
                patch.object(dashboard, "set_user_created_confirmation") as confirm:
            instance.do_POST()
        confirm.assert_not_called()
        self.assertEqual(instance.rfile.tell(), len(raw))
        response = instance.wfile.getvalue()
        self.assertRegex(response, rb"^HTTP/1\.[01] 403 ")
        self.assertIn(b"Sign in required", response)


if __name__ == "__main__":
    unittest.main()
