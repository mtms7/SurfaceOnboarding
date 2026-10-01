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

    def test_preview_renders_the_current_design_without_any_backend(self):
        import re
        import subprocess
        from unittest.mock import patch
        import tools.serve_attended_open_onboardings_dashboard as dashboard
        import tools.attended_ce_only_playwright as runner
        originals = (dashboard.sf_json, dashboard.load_runner_state, runner._sf_records)
        paths = ["/", "/?queue=ready", "/?queue=scanning", "/history", "/connection"] + [
            f"/co/CO-DEMO-000{i}" for i in range(1, 7)]
        with patch.object(subprocess, "run", side_effect=AssertionError("no subprocess")),                 patch.object(subprocess, "Popen", side_effect=AssertionError("no process launch")):
            pages = {path: preview_response(path) for path in paths}
        for path, (status, body, _type) in pages.items():
            with self.subTest(path=path):
                self.assertEqual(status, 200)
                text = body.decode("utf-8")
                self.assertIn("Read-only design preview · synthetic data · Not deployed.", text)
                self.assertIn("PENTERA.", text)  # the current Pentera shell
                self.assertEqual(re.findall(r"<button(?![^>]*\bdisabled\b)", text), [])
                self.assertNotIn("Read from Salesforce at", text)
        self.assertEqual((dashboard.sf_json, dashboard.load_runner_state, runner._sf_records), originals)
        self.assertIn(b"case3_term_mismatch", pages["/co/CO-DEMO-0003"][1])
        self.assertIn("Salesforce IDs · Ready to write", pages["/co/CO-DEMO-0002"][1].decode("utf-8"))
        self.assertIn(b"<code>COMPLETED</code>", pages["/co/CO-DEMO-0004"][1])
        self.assertEqual(preview_response("/?queue=nope")[0], 404)
        self.assertEqual(preview_response("/co/CO-DEMO-0007")[0], 404)
        self.assertEqual(preview_response("/history?x=1")[0], 404)

