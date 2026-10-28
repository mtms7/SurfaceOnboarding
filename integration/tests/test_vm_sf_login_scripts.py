"""VM attended Salesforce sign-in and refresh timer (owner decision 2026-10-27): the operator script is a reviewed artifact.

Pinned by text; the script is never executed here (a POSIX ``sh -n`` syntax check runs only when ``sh`` is available)."""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

VM_PILOT = Path(__file__).resolve().parents[1] / "deployment" / "vm_pilot"
NAME = "_step28a_vm_install_sf_login_and_refresh.sh"


class StepScriptTests(unittest.TestCase):
    def setUp(self):
        data = (VM_PILOT / NAME).read_bytes()
        self.assertNotIn(b"\r", data, "must use LF line endings (it runs on Linux)")
        self.text = data.decode("utf-8")

    def test_it_is_a_root_only_posix_review_artifact_with_install_status_and_revert(self):
        self.assertTrue(self.text.startswith("#!/bin/sh\n# REVIEW ARTIFACT"))
        self.assertIn("set -eu", self.text)
        self.assertIn('[ "$(id -u)" -eq 0 ]', self.text)
        for mode in ("install)", "status)", "revert)"):
            self.assertIn(mode, self.text)

    def test_the_session_stays_in_memory_and_no_secret_is_handled(self):
        self.assertIn("/run/surface-onboarding-sf", self.text)
        self.assertIn("d /run/surface-onboarding-sf 0700 surface-onboarding surface-onboarding -", self.text)
        self.assertIn("SF_WEB_OAUTH_SERVER_TIMEOUT=300000", self.text)  # the CLI's own 2-minute wait (spike finding 2026-10-15)
        self.assertIn("-L 1717:127.0.0.1:1717", self.text)
        self.assertIsNone(re.search(r"(?i)(password\s*=|bearer |token\s*=|--password|--jwt-key-file)", self.text))
        self.assertIn("login-url.txt", self.text)  # the one-time URL is shown to the operator, never opened by the VM

    def test_nothing_is_started_without_the_owners_marker_and_the_unit_is_gated_by_it(self):
        self.assertIn("approved-sf-reader", self.text)
        unit = (VM_PILOT / "surface-onboarding-sf-refresh.service.template").read_text(encoding="utf-8")
        self.assertIn("ConditionPathExists=/etc/surface-onboarding/approved-sf-reader", unit)
        self.assertIn("--transport cli", unit)

    def test_the_login_url_is_pinned_to_salesforce_and_checked_twice(self):
        self.assertGreaterEqual(self.text.count(r"^https://[a-z0-9.-]+\.salesforce\.com$"), 2)

    def test_it_is_read_only_and_touches_nothing_outside_its_scope(self):
        for forbidden in ("authorized_keys", "sshd_config", "ufw ", "iptables", "nft ", "snapshots/", "clone-push", "nginx",
                          "surface-onboarding-dashboard", "data update", "data create", "data delete", "data upsert", "apex"):
            self.assertNotIn(forbidden, self.text, forbidden)
        self.assertIsNone(re.search(r"(?m)^\s*(eval|sh -c|bash -c)\b", self.text))
        self.assertIn("SELECT Id FROM Organization LIMIT 1", self.text)

    def test_the_embedded_programs_have_valid_shell_syntax(self):
        sh = shutil.which("sh")
        if sh is None:
            self.skipTest("no POSIX sh on this machine")
        completed = subprocess.run([sh, "-n", str(VM_PILOT / NAME)], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)


class CadenceScriptTests(unittest.TestCase):
    def setUp(self):
        data = (VM_PILOT / "_step28b_vm_sf_refresh_cadence.sh").read_bytes()
        self.assertNotIn(b"\r", data)
        self.text = data.decode("utf-8")

    def test_it_only_writes_a_timer_drop_in_within_10_to_240_minutes(self):
        self.assertTrue(self.text.startswith("#!/bin/sh\n# REVIEW ARTIFACT"))
        self.assertIn('[ "$(id -u)" -eq 0 ]', self.text)
        self.assertIn("OnUnitActiveSec=", self.text)
        self.assertIn('-ge 10', self.text)
        self.assertIn('-le 240', self.text)
        self.assertIn("surface-onboarding-sf-refresh.timer.d/interval.conf", self.text.replace("$TIMER", "surface-onboarding-sf-refresh.timer"))
        for forbidden in ("approved-sf-reader", "authorized_keys", "ufw ", "iptables", "snapshots/", "nginx", "data update", "eval "):
            self.assertNotIn(forbidden, self.text, forbidden)

    def test_the_embedded_syntax_is_valid_shell(self):
        sh = shutil.which("sh")
        if sh is None:
            self.skipTest("no POSIX sh on this machine")
        completed = subprocess.run([sh, "-n", str(VM_PILOT / "_step28b_vm_sf_refresh_cadence.sh")], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
