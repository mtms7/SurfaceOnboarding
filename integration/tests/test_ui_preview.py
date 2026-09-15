from __future__ import annotations

import unittest

from integration.onboarding.ui_preview import render_dashboard_detail, render_dashboard_preview


class UiPreviewTests(unittest.TestCase):
    def test_preview_is_a_redacted_non_executable_security_design(self):
        preview = render_dashboard_preview()
        self.assertIn("Surface onboarding queue", preview)
        self.assertIn("Not deployed.", preview)
        self.assertIn("Salesforce-style operational view", preview)
        self.assertIn("CO-DEMO-0001", preview)
        self.assertIn("no scripts", preview)
        for forbidden in ("<script", "password", "token", "cookie", "salesforce.com", "leonardo.dev"):
            self.assertNotIn(forbidden, preview.lower())

    def test_preview_does_not_offer_state_changing_controls(self):
        preview = render_dashboard_preview().lower()
        for forbidden in ("<form", "<button", "<input", "onclick=", "salesforce.com"):
            self.assertNotIn(forbidden, preview)

    def test_preview_detail_exists_only_for_synthetic_safe_reference(self):
        self.assertIn("CO-DEMO-0001", render_dashboard_detail("CO-DEMO-0001") or "")
        self.assertIsNone(render_dashboard_detail("CO-0717"))
