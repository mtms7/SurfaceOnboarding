"""Owner decision 2026-10-19: production is live, so nothing from Leonardo (Dev) is shown or started any more.

The Dev menu, the Leonardo session rows, the preflight row, the Leonardo troubleshooting buttons and the Leonardo wording on the
sign-in and Sessions pages are gone; signing in, "Prepare sessions" and "Re-check" no longer start a Leonardo check or sign-in
(nothing opens a Leonardo tab). Code, Dev routes (by direct URL) and records are untouched. Hermetic: no browser, no network.
"""

import re
import unittest
from pathlib import Path
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding.session_readiness import SessionState, SessionStatus

NOW = dashboard.readiness_now


def statuses(leonardo=SessionState.BLOCKED):
    now = dashboard.readiness_now()
    return {"salesforce": SessionStatus(SessionState.READY, "salesforce_ready", now),
            "leonardo": SessionStatus(leonardo, "leonardo_check_failed", now)}


def visible_text(html):
    return re.sub(r"\s+", " ", re.sub(r"<style.*?</style>", " ", re.sub(r"<[^>]+>", " ", html), flags=re.S)).strip()


class HiddenTests(unittest.TestCase):
    def shell(self, active="prod_onboarding"):
        with mock.patch.object(dashboard, "session_statuses", return_value=statuses()), \
                mock.patch.object(dashboard, "vm_mode", return_value=False):
            return dashboard._app_shell("T", "<p>body</p>", active=active)

    def test_the_menu_has_no_dev_leonardo_group_and_no_dev_links(self):
        page = self.shell()
        self.assertNotIn("LEONARDO", page)
        self.assertNotIn("Leonardo", visible_text(page))
        for href in ("/dev", "/dev/onboarded", "/inventory"):
            self.assertNotIn("href='" + href + "'", page)
        for production in ("/prod/tenants", "/history", "/prod/approvals", "/connection"):
            self.assertIn("href='" + production + "'", page)

    def test_the_session_chip_ignores_leonardo(self):
        with mock.patch.object(dashboard, "session_statuses", return_value=statuses(SessionState.BLOCKED)):
            self.assertIn("Sessions: Ready", dashboard._session_chip())
        blocked_sf = statuses()
        blocked_sf["salesforce"] = SessionStatus(SessionState.EXPIRED, "salesforce_sign_in_required", dashboard.readiness_now())
        with mock.patch.object(dashboard, "session_statuses", return_value=blocked_sf):
            self.assertIn("Sessions: Sign-in needed", dashboard._session_chip())

    def test_the_sessions_card_shows_salesforce_and_backoffice_only(self):
        with mock.patch.object(dashboard, "_backoffice_session_row", return_value="<tr><th>BackOffice production</th></tr>"):
            card = dashboard._sessions_card(statuses(), False)
        text = visible_text(card)
        self.assertNotIn("Leonardo", text)
        self.assertNotIn("Preflight", text)
        self.assertNotIn("Browser automation", text)
        self.assertNotIn("leonardo-dev", card)
        self.assertIn("Salesforce", text)
        self.assertIn("BackOffice production", text)
        self.assertIn("Re-check", text)

    def test_the_environment_chip_and_login_page_have_no_leonardo(self):
        with mock.patch.object(dashboard, "_production_lock", return_value=(True, "production_runner_not_supported")):
            self.assertNotIn("Leonardo", dashboard._environment_chip())
        self.assertNotIn("Leonardo", visible_text(dashboard.page_login()))

    def test_signing_in_and_preparing_sessions_start_no_leonardo_work(self):
        with mock.patch.object(dashboard, "start_leonardo_session_worker") as worker, \
                mock.patch.object(dashboard, "probe_salesforce_readiness",
                                  return_value=SessionStatus(SessionState.READY, "salesforce_ready", dashboard.readiness_now())):
            dashboard.prepare_sessions()
        worker.assert_not_called()
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertNotIn("# Leonardo sign-in (with the preflight first) starts right away", source)
        self.assertNotIn("start_leonardo_session_worker(sign_in=True)", source)
        self.assertNotIn("start_leonardo_session_worker(sign_in=False)", source)

    def test_the_launcher_messages_do_not_mention_leonardo(self):
        text = Path(dashboard.__file__).with_name("start_attended_dashboard.ps1").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith(("Write-Host", "also starts")):
                self.assertNotIn("Leonardo", line)


if __name__ == "__main__":
    unittest.main()
