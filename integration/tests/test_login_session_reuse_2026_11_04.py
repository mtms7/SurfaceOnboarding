"""Owner request 2026-11-04: reuse the active Salesforce session instead of signing in again.

* The login page offers "Continue as <operator>" when the dashboard's Salesforce CLI session is still valid for an allowed
  operator in the pinned org (one read-only userinfo call; the same identity rules as a new sign-in).
* Continue checks the session again (never from the page cache), opens no browser, starts no SSO and signs nothing out.
* A new sign-in no longer signs the CLI out first; a foreign or wrong-org session is still ended after a sign-in, and
  Sign out still ends the CLI session.
* BackOffice is unchanged: the automation profile keeps its own saved session (the launcher only reports it).

Hermetic: the same fake server, CLI and userinfo as the existing login tests; nothing reads Salesforce.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.tests.test_session_readiness import LoginFlowTests

LAUNCHER = Path(__file__).resolve().parents[2] / "tools" / "start_attended_dashboard.ps1"


class ContinueWithActiveSessionTests(LoginFlowTests):
    # Run only the tests of this class, not the inherited ones (they already run in test_session_readiness).
    def _origin(self):
        return {"Origin": "http://127.0.0.1:" + str(self.server.server_address[1]), "Sec-Fetch-Site": "same-origin"}

    def test_continue_signs_in_without_a_browser_or_sign_out(self):
        status, location, cookies, _ = self._request("POST", "/login/continue", extra=self._origin())
        self.assertEqual((status, location), (303, "/connection"))
        session = self._session(cookies)
        self.assertEqual(self.launched, [])  # no `sf org login web`
        self.assertEqual(self.quiet, [])  # no `sf org logout`
        self.assertEqual(self.workers, [])
        self.assertEqual(dashboard.session_statuses(include_runs=False)["salesforce"].state, dashboard.SessionState.READY)
        status, _, _, body = self._request("GET", "/connection", [session])
        self.assertEqual(status, 200)
        self.assertIn("milton.stevenson@pentera.io", body)

    def test_an_expired_session_offers_only_the_normal_sign_in(self):
        with patch.object(dashboard, "salesforce_userinfo", return_value=None):
            _status, _, _, body = self._request("GET", "/login")
            self.assertNotIn("Continue as", body)
            self.assertIn("Sign in with Salesforce", body)
            status, _, cookies, body = self._request("POST", "/login/continue")
        self.assertEqual(status, 409)
        self.assertIn("salesforce_session_inactive", body)
        self.assertFalse(any(c.startswith(dashboard.SESSION_COOKIE) for c in cookies))

    def test_a_foreign_or_wrong_org_session_is_never_continued(self):
        cases = ({**self.USERINFO, "email": "x@pentera.io", "preferred_username": "x@pentera.io"},
                 {**self.USERINFO, "organization_id": "00D000000000002AAA"})
        for userinfo in cases:
            with self.subTest(userinfo=userinfo), patch.object(dashboard, "salesforce_userinfo", return_value=userinfo):
                dashboard._active_session_cache.clear()
                _status, _, _, body = self._request("GET", "/login")
                self.assertNotIn("Continue as", body)
                status, _, cookies, _ = self._request("POST", "/login/continue")
                self.assertEqual(status, 409)
                self.assertFalse(any(c.startswith(dashboard.SESSION_COOKIE) for c in cookies))

    def test_continue_checks_again_and_ignores_the_page_cache(self):
        self.assertIn("Continue as", self._request("GET", "/login")[3])  # the page caches the active operator
        with patch.object(dashboard, "salesforce_userinfo", return_value=None):
            status, _, _, body = self._request("POST", "/login/continue")
        self.assertEqual(status, 409)
        self.assertIn("salesforce_session_inactive", body)

    def test_continue_is_refused_while_a_browser_sign_in_is_pending(self):
        self._request("POST", "/login/start")
        status, _, _, body = self._request("POST", "/login/continue")
        self.assertEqual(status, 409)
        self.assertIn("login_in_progress", body)

    def test_cross_site_continue_is_refused(self):
        self.assertEqual(self._request("POST", "/login/continue", extra={"Origin": "https://evil.example"})[0], 403)

    def test_sign_out_still_ends_the_cli_session_and_forgets_the_offer(self):
        _status, _, cookies, _ = self._request("POST", "/login/continue")
        session = self._session(cookies)
        self._request("POST", "/logout", [session])
        self.assertEqual(self.quiet[-1][:2], ("org", "logout"))
        self.assertEqual(dashboard._active_session_cache, {})

    def test_the_vm_never_offers_continue(self):
        with patch.object(dashboard, "vm_mode", return_value=True):
            self.assertIsNone(dashboard.active_salesforce_operator(fresh=True))

    def test_the_launcher_reports_both_sessions(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        # 2026-11-12 local accounts: the dashboard sign-in is the username form; the launcher still reports the Salesforce session.
        self.assertIn("session is still active (checked again after sign-in)", text)
        self.assertIn("backoffice-production-automation", text)
        self.assertNotIn("Copy-Item", text)  # nothing is copied between profiles


# The inherited LoginFlowTests methods already run in their own module; keep only this class's own tests here.
for _name in [name for name in vars(LoginFlowTests) if name.startswith("test_")]:
    if _name not in vars(ContinueWithActiveSessionTests):
        setattr(ContinueWithActiveSessionTests, _name, None)
del LoginFlowTests  # not collected twice from this module
