from __future__ import annotations

import unittest

from tools.serve_loopback_preview import (DEFAULT_PORT, LOOPBACK_HOST, SECURITY_HEADERS, parse_port,
                                          preview_response)


class LoopbackPreviewTests(unittest.TestCase):
    def test_preview_is_fixed_to_loopback_and_has_safe_routes_only(self):
        self.assertEqual(LOOPBACK_HOST, "127.0.0.1")
        page_status, page, page_type = preview_response("/")
        self.assertEqual((page_status, page_type), (200, "text/html; charset=utf-8"))
        self.assertIn(b"Not deployed.", page)
        self.assertIn(b"/co/CO-DEMO-0001", page)
        detail_status, detail, detail_type = preview_response("/co/CO-DEMO-0001")
        self.assertEqual((detail_status, detail_type), (200, "text/html; charset=utf-8"))
        self.assertIn(b"CO-DEMO-0001", detail)
        self.assertEqual(preview_response("/co/CO-0717")[0], 404)
        self.assertEqual(preview_response("/anything")[0], 404)
        self.assertEqual(preview_response("/?anything")[0], 404)
        self.assertEqual(preview_response("/health"),
                         (200, b'{"status":"static_preview_only"}', "application/json"))

    def test_preview_security_headers_and_port_policy_are_constrained(self):
        self.assertEqual(SECURITY_HEADERS["Cache-Control"], "no-store")
        self.assertIn("default-src 'none'", SECURITY_HEADERS["Content-Security-Policy"])
        self.assertEqual(parse_port([]), DEFAULT_PORT)
        self.assertEqual(parse_port(["8002"]), 8002)
        for value in (["80"], ["70000"], ["not-a-port"], ["8001", "extra"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_port(value)
