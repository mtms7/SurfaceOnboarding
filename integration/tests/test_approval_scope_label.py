"""The create-approval card names the real scope: a Case 3 approval must not say "CE-only" (owner decision 2026-10-21).

Cosmetic only: the approval itself, its route and its scope binding are unchanged. Hermetic: the approval view is faked.
"""

from __future__ import annotations

import unittest
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_approval as approval


def item(scope):
    return {"approval_ref": "AP-1", "scope": scope, "issued_by": "owner", "expires_at": "2026-10-28T00:00:00Z", "consumed_at": ""}


class ScopeLabelTests(unittest.TestCase):
    def render(self, scope):
        view = {"state": "valid", "item": item(scope)}
        with mock.patch.object(dashboard.production_views, "approval_for_co", return_value=view):
            return dashboard._create_approval_html("CO-0800")

    def test_a_case3_approval_does_not_say_ce_only(self):
        html = self.render(approval.CASE3_SCOPE)
        self.assertIn("One CO, Surface &amp; Credential Exposure", html)
        self.assertNotIn("CE-only", html)

    def test_a_ce_only_approval_keeps_its_wording(self):
        self.assertIn("One CO, CE-only", self.render(approval.SCOPE))

    def test_an_unknown_scope_is_shown_neutrally(self):
        html = self.render("something_else")
        self.assertIn("One CO", html)
        self.assertNotIn("CE-only", html)


if __name__ == "__main__":
    unittest.main()
