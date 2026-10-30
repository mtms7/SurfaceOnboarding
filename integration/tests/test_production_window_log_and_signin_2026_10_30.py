"""Owner reports 2026-10-30 (CO-0831).

1. A window holding an old, expired BackOffice session showed the app shell, so the sign-in wait passed and the first read
   got 401/403 (``production_session_expired``). The production sign-in wait now also needs the page's OWN getAuthorities /
   accountInfo calls to have answered 200; until then it keeps waiting (reloading at most every 30 s to see them again).
2. The kept window from one click was gone at the next and nothing said why. Every launch, reuse, keep and close of the
   production window now appends one line of fixed words to ``<profile>.window-log`` (time, event, reason; no secret).
Hermetic: fake pages, temporary folders; nothing is launched, opened or sent.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

ORIGIN = runner.production_probe.PRODUCTION_ORIGIN
AUTH = runner.production_dry_run.AUTHORITIES_PATH
INFO = runner.production_dry_run.ACCOUNT_INFO_PATH
SIGNED_IN = ("/backoffice/tenantManagement", {"texts": ["Add Account"], "tables": 1, "navs": 1})
LOGIN = ("/login", {"texts": ["Log In"], "tables": 0, "navs": 0})
NOW = datetime(2026, 10, 30, 18, 45, tzinfo=timezone.utc)


def response(path, status, method="GET", origin=ORIGIN):
    return SimpleNamespace(url=origin + path, status=status, request=SimpleNamespace(method=method))


class FakePage:
    def __init__(self):
        self.handlers, self.reloads = [], 0

    def on(self, event, handler):
        self.handlers.append(handler)

    def send(self, *responses):
        for item in responses:
            for handler in self.handlers:
                handler(item)

    def reload(self, **_kw):
        self.reloads += 1


class AuthWatchTests(unittest.TestCase):
    def setUp(self):
        self.page, self.clock = FakePage(), [100.0]
        self.watch = runner._BackOfficeAuthWatch(self.page, clock=lambda: self.clock[0])

    def test_the_latest_status_of_the_own_auth_calls_decides(self):
        self.assertEqual(self.watch.verdict(), "none")
        self.page.send(response(AUTH, 401))
        self.assertEqual(self.watch.verdict(), "refused")
        self.page.send(response(AUTH, 200), response(INFO, 200))  # after the sign-in the same calls answer 200
        self.assertEqual(self.watch.verdict(), "ok")
        self.page.send(response(INFO, 500))
        self.assertEqual(self.watch.verdict(), "other")

    def test_other_methods_origins_and_paths_are_ignored(self):
        self.page.send(response(AUTH, 200, method="POST"), response(AUTH, 200, origin="https://evil.example"),
                       response("/api/v1/backoffice/getAllDetailedAccounts", 200))
        self.assertEqual(self.watch.verdict(), "none")

    def poll(self, snapshot):
        with mock.patch.object(runner, "_probe_snapshot", return_value=snapshot):
            return runner._backoffice_signed_in_poll(self.page, self.watch)()

    def test_the_shell_alone_never_counts_and_reloads_at_most_every_30_seconds(self):
        self.assertEqual(self.poll(SIGNED_IN), (SIGNED_IN[0], None))
        self.assertEqual(self.page.reloads, 1)
        self.clock[0] += 10
        self.poll(SIGNED_IN)
        self.assertEqual(self.page.reloads, 1)
        self.clock[0] += 25
        self.poll(SIGNED_IN)
        self.assertEqual(self.page.reloads, 2)

    def test_a_page_that_makes_no_auth_call_falls_back_after_two_reloads(self):
        self.assertEqual(self.poll(SIGNED_IN), (SIGNED_IN[0], None))
        self.clock[0] += 31
        self.assertEqual(self.poll(SIGNED_IN), (SIGNED_IN[0], None))  # the second reload
        self.assertEqual(self.poll(SIGNED_IN), SIGNED_IN)  # still no auth call: the page-only rule applies
        self.assertEqual(self.page.reloads, 2)

    def test_a_refusal_never_falls_back(self):
        self.page.send(response(AUTH, 401))
        for _ in range(4):
            self.assertEqual(self.poll(SIGNED_IN), (SIGNED_IN[0], None))
            self.clock[0] += 31

    def test_an_expired_session_never_counts(self):
        self.page.send(response(AUTH, 403))
        self.assertEqual(self.poll(SIGNED_IN), (SIGNED_IN[0], None))
        self.assertEqual(self.page.reloads, 1)

    def test_a_200_auth_call_with_the_signed_in_page_counts(self):
        self.page.send(response(AUTH, 200))
        self.assertEqual(self.poll(SIGNED_IN), SIGNED_IN)
        self.assertEqual(self.page.reloads, 0)

    def test_the_login_page_is_passed_through_without_a_reload(self):
        self.assertEqual(self.poll(LOGIN), LOGIN)
        self.assertEqual(self.page.reloads, 0)

    def test_the_final_check_needs_the_auth_verdict(self):
        with mock.patch.object(runner.production_probe, "wait_for_login", return_value=True), \
                mock.patch.object(runner, "_probe_snapshot", return_value=SIGNED_IN):
            self.page.send(response(AUTH, 401))
            self.assertFalse(runner._backoffice_signed_in(self.page, self.watch, 5, say=lambda *_: None))
            self.page.send(response(AUTH, 200))
            self.assertTrue(runner._backoffice_signed_in(self.page, self.watch, 5, say=lambda *_: None))


class WindowLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name) / "backoffice-production-automation"
        self.profile.mkdir()
        self.log = self.profile.with_name(self.profile.name + runner.PRODUCTION_WINDOW_LOG_SUFFIX)
        for patch in (mock.patch.object(runner, "leonardo_profile", return_value=(self.profile, True)),
                      mock.patch.object(runner, "_close_automation_browser_at", return_value="automation_browser_closed")):
            patch.start()
            self.addCleanup(patch.stop)

    def lines(self):
        return [line.split(" ", 1)[1] for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def tab(self, reused):
        return SimpleNamespace(persist=True, reused=reused, profile_dir=self.profile)

    def test_keep_and_close_reasons_are_logged(self):
        runner._finish_production_window(self.tab(False), NOW)
        runner._finish_production_window(self.tab(True), NOW + timedelta(hours=9))
        runner.close_production_window(reason="dashboard_start")
        runner.close_production_window(expire=True, reason="dashboard_sign_out")
        self.assertEqual(self.lines(), ["kept run_end", "close run_end_deadline_passed:automation_browser_closed",
                                        "close dashboard_start:automation_browser_closed", "expire dashboard_sign_out"])

    def test_a_reuse_inside_the_deadline_logs_nothing_at_its_end(self):
        runner._finish_production_window(self.tab(False), NOW)
        runner._finish_production_window(self.tab(True), NOW + timedelta(hours=1))
        self.assertEqual(self.lines(), ["kept run_end"])

    def test_the_idle_expiry_check_does_not_fill_the_log(self):
        with mock.patch.object(runner, "_close_automation_browser_at", return_value="automation_browser_not_running"):
            runner.close_production_window(only_if_expired=True, reason="dashboard_expiry")
            runner.close_production_window(reason="dashboard_start")
        self.assertEqual(self.lines(), ["close dashboard_start:automation_browser_not_running"])

    def test_lines_are_a_time_and_fixed_words_only(self):
        runner._log_production_window(self.profile, "launched", "browser_not_running")
        text = self.log.read_text(encoding="utf-8")
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z launched browser_not_running\n$")
        self.assertFalse((self.profile / self.log.name).exists())  # beside the profile, never inside it

    def test_the_log_rolls_over_and_never_raises(self):
        self.log.write_text("x" * (runner.PRODUCTION_WINDOW_LOG_MAX_BYTES + 1), encoding="utf-8")
        runner._log_production_window(self.profile, "reused")
        self.assertTrue(self.log.with_name(self.log.name + ".old").exists())
        self.assertEqual(self.lines(), ["reused"])
        runner._log_production_window(Path("Z:/no/such/dir/profile"), "reused")  # no exception

    def test_the_reuse_outlook_names_why_a_window_would_be_launched(self):
        target = runner.production_probe.probe_target()
        keep = self.profile.with_name(self.profile.name + runner.PRODUCTION_KEEP_SUFFIX)
        self.assertEqual(runner._production_reuse_outlook(target), "no_deadline")
        keep.write_text((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), encoding="utf-8")
        self.assertEqual(runner._production_reuse_outlook(target), "deadline_passed")
        keep.write_text((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), encoding="utf-8")
        with mock.patch.object(runner, "_verified_automation_port", return_value=None):
            self.assertEqual(runner._production_reuse_outlook(target), "browser_not_running")
        with mock.patch.object(runner, "_verified_automation_port", return_value=9222):
            self.assertEqual(runner._production_reuse_outlook(target), "")
        with mock.patch.object(runner, "leonardo_profile", return_value=(self.profile, False)):
            self.assertEqual(runner._production_reuse_outlook(target), "temporary_profile")


class NoRealWindowTests(unittest.TestCase):
    """2026-10-30: a test's /logout closed the owner's live window. Under tests every profile lives in the temp folder."""

    def test_profiles_resolve_inside_the_test_folder(self):
        import os
        temp = Path(os.environ["LOCALAPPDATA"]).resolve()
        self.assertIn("surface_test_state_", str(temp))
        for target in (runner.production_probe.probe_target(), None):
            profile = runner.leonardo_profile(target)[0] if target else runner.leonardo_profile()[0]
            self.assertTrue(str(Path(profile).resolve()).startswith(str(temp)), profile)


class WiringTests(unittest.TestCase):
    def test_the_production_pages_use_the_auth_aware_wait(self):
        source = Path(runner.__file__).read_text(encoding="utf-8")
        for name in ("_pdr_browser_page", "_kept_browser_page"):
            body = source[source.index("def " + name):]
            body = body[:body.index("\ndef ", 10)]
            self.assertIn("_BackOfficeAuthWatch(page)", body, name)
            self.assertIn("_backoffice_signed_in(page, watch", body, name)
            self.assertLess(body.index("_BackOfficeAuthWatch(page)"), body.index("page.goto("), name)  # watch the first load
            self.assertIn("_log_production_window(", body, name)

    def test_the_dashboard_names_its_reason(self):
        calls = []
        with mock.patch.object(dashboard, "vm_mode", return_value=False), \
                mock.patch.object(dashboard, "_production_run_active", return_value=False), \
                mock.patch.object(dashboard.attended_runner, "close_production_window",
                                  side_effect=lambda **kw: calls.append(kw["reason"]) or "ok"):
            for reason in ("start", "sign_out", "expiry"):
                dashboard.close_kept_production_window(reason)
        self.assertEqual(calls, ["dashboard_start", "dashboard_sign_out", "dashboard_expiry"])
        self.assertTrue(all(re.fullmatch(r"[a-z_]+", c) for c in calls))


if __name__ == "__main__":
    unittest.main()
