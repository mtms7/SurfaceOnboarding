"""A VM-started run needs a FRESH BackOffice SSO and MFA sign-in (owner decision 2026-10-27, docs/62 G9).

The runner normally reuses the persisted automation profile, so an already-signed-in BackOffice asks for nothing. A remote start sets
``SURFACE_ONBOARDING_PRODUCTION_FRESH_SIGNIN=1`` for the runner child; the runner then uses a brand-new temporary profile (never the
persisted one, never a reused Chrome), torn down at the end, so BackOffice must ask for SSO and MFA. Hermetic: no Chrome, no network."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import remote_start as rs
from integration.onboarding import session_readiness as readiness
from integration.onboarding.run_target import RunTarget

FRESH = rs.FRESH_SIGNIN_ENV


def production_target() -> RunTarget:
    environment = readiness.ENVIRONMENTS[readiness.BACKOFFICE_PRODUCTION]
    return RunTarget(environment.key, environment.origin, environment.profile_dir_name)


class FreshProfileTests(unittest.TestCase):
    def setUp(self):
        self.created = []

    def tearDown(self):
        for path in self.created:
            shutil.rmtree(path, ignore_errors=True)

    def test_the_switch_name_is_one_the_runner_child_actually_receives(self):
        self.assertTrue(FRESH.startswith("SURFACE_ONBOARDING_PRODUCTION_"))  # the launcher forwards only this prefix to the child

    def test_with_the_switch_every_run_gets_a_new_temporary_profile_that_is_not_persisted(self):
        with patch.dict(os.environ, {FRESH: "1"}):
            first, persist_first = runner.leonardo_profile(production_target())
            second, persist_second = runner.leonardo_profile(production_target())
        self.created += [first, second]
        self.assertFalse(persist_first or persist_second)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())
        self.assertNotIn("SurfaceOnboarding", str(first))  # never the persisted BackOffice profile folder

    def test_without_the_switch_the_production_profile_is_the_persisted_one_as_before(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(FRESH, None)
            profile, persist = runner.leonardo_profile(production_target())
        if os.name == "nt":
            self.assertTrue(persist)
            self.assertIn("SurfaceOnboarding", str(profile))
        else:
            self.created.append(profile)
            self.assertFalse(persist)

    def test_only_the_exact_value_one_counts(self):
        for value in ("0", "true", "yes", ""):
            with patch.dict(os.environ, {FRESH: value}):
                profile, persist = runner.leonardo_profile(production_target())
            if os.name == "nt":
                self.assertTrue(persist, value)
            else:
                self.created.append(profile)

    def test_the_development_profile_is_not_affected(self):
        with patch.dict(os.environ, {FRESH: "1"}):
            profile, persist = runner.leonardo_profile()
        if os.name == "nt":
            self.assertTrue(persist)
        else:
            self.created.append(profile)

    def test_a_fresh_profile_never_reuses_a_running_automation_chrome(self):
        profile = Path(tempfile.mkdtemp(prefix="fresh_signin_test_"))
        self.created.append(profile)
        proc = object()
        with patch.object(runner, "leonardo_profile", return_value=(profile, False)), \
                patch.object(runner, "_verified_automation_port", side_effect=AssertionError("must not reuse")), \
                patch.object(runner, "_launch_automation_chrome", return_value=(proc, 4242)), \
                patch.object(runner, "_cdp_page_targets", return_value=[]), \
                patch.object(runner, "_cdp_open_tab", return_value="TAB1"), \
                patch.object(runner, "_chrome_executable", return_value="chrome.exe"):
            tab = runner._open_automation_tab(object(), "https://app.pentera.io/", production_target())
        self.assertFalse(tab.reused)
        self.assertFalse(tab.persist)
        with patch.object(runner, "_stop_chrome_process") as stop:
            runner._release_automation_tab(tab)
        stop.assert_called_once_with(proc)
        self.assertFalse(profile.exists())  # the temporary profile, and with it every cookie, is deleted


class RemoteLaunchCarriesTheSwitchTests(unittest.TestCase):
    def test_a_remote_start_launches_the_runner_with_the_fresh_sign_in_switch_and_the_button_does_not(self):
        seen = {}

        def fake_post(values, inputs, launch, issue=None):
            seen["launch"] = launch
            return "launched"

        row = {"LastModifiedDate": "2026-10-27T10:00:00.000+0000"}
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "_owner_session_live", return_value=True), \
                patch.object(dashboard, "detail_row", return_value=row), patch.object(dashboard, "_create_ui_inputs", return_value="I"), \
                patch.object(create_ui, "remote_start_values", return_value=({"reference": "CO-0812", "nonce": "t"}, "")), \
                patch.object(create_ui, "handle_create_post", side_effect=fake_post):
            dashboard._start_remote_create("CO-0812", "owner@example.test", row["LastModifiedDate"])
        calls = []
        with patch.object(dashboard, "_launch_production_create_followed",
                          side_effect=lambda ref, rev, env, route=None: calls.append((ref, rev, dict(env), route)) or "PROC"):
            self.assertEqual(seen["launch"]("CO-0812", "rev", {"SURFACE_ONBOARDING_PRODUCTION_X": "1"}), "PROC")
            self.assertEqual(seen["launch"]("CO-0802", "rev", {}, route="case_3_combined_baseline"), "PROC")
        self.assertEqual(calls[0][2], {"SURFACE_ONBOARDING_PRODUCTION_X": "1", FRESH: "1"})
        self.assertEqual((calls[1][2], calls[1][3]), ({FRESH: "1"}, "case_3_combined_baseline"))

    def test_the_launcher_forwards_the_switch_to_the_runner_child(self):
        captured = {}

        class FakeProcess:
            pass

        def fake_popen(argv, env=None, **kwargs):
            captured["env"] = env
            return FakeProcess()

        with patch.object(dashboard, "local_browser_launch_allowed", return_value=True), patch.object(dashboard.subprocess, "Popen", fake_popen), \
                patch.object(dashboard, "runner_log_dir", return_value=None):
            dashboard._launch_production_create("CO-0812", "rev", {FRESH: "1", "UNRELATED": "x"})
            self.assertEqual(captured["env"].get(FRESH), "1")
            self.assertNotEqual(captured["env"].get("UNRELATED"), "x")
            os.environ.pop(FRESH, None)
            dashboard._launch_production_create("CO-0812", "rev", {})
            self.assertNotEqual(captured["env"].get(FRESH), "1")  # the normal button never sets it


if __name__ == "__main__":
    unittest.main()
