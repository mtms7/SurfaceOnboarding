"""VM Redash collector (docs/44 addendum 2026-10-16): runs as the service user with its key from a root-owned file, writes
<state>/clone-inventory/prod-clone/latest.json, treats "only GET works" as an honest cached read (exit 0, age recorded), and the
operator scripts and unit templates stay consistent. Hermetic: fake HTTP layer, temporary folders, no network, no secrets."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from integration.onboarding import leonardo_inventory as inventory
from integration.tests.test_redash_inventory import (FAKE_KEY, GET, NOW, POST, RETRIEVED_FRESH, RETRIEVED_STALE,
                                                      FakeOpener, http_error, result, row)
import tools.redash_inventory_collector as collector

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "integration" / "deployment" / "vm_pilot"
SERVICE = (PILOT / "surface-onboarding-redash-collect.service.template").read_text(encoding="utf-8")
TIMER = (PILOT / "surface-onboarding-redash-collect.timer.template").read_text(encoding="utf-8")
SCRIPTS = {name: PILOT / name for name in ("_step18a_vm_redash_key.sh", "_step18b_vm_redash_enable.sh",
                                           "_step18c_vm_redash_status.sh")}


class VmRunTests(unittest.TestCase):
    """main() the way the systemd unit runs it: state dir + key file from the environment, no DPAPI, no real network."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("SURFACE_")}
        self.env.update(SURFACE_ONBOARDING_STATE_DIR=str(self.state),
                        SURFACE_REDASH_KEY_FILE=str(self.tmp / "redash-key"))

    def run_main(self, argv, opener):
        out = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(collector, "_dpapi", side_effect=AssertionError("DPAPI must not be used on the VM")), \
                patch.object(collector, "load_key_file", return_value=FAKE_KEY) as key_file, \
                patch.object(collector, "build_opener", return_value=opener), \
                patch.object(collector, "datetime", wraps=datetime) as clock, redirect_stdout(out):
            clock.now.return_value = NOW
            code = collector.main(argv)
        key_file.assert_called_once_with(str(self.tmp / "redash-key"))
        return code, out.getvalue()

    def latest(self):
        return self.state / "clone-inventory" / "prod-clone" / "latest.json"

    def test_key_from_the_file_path_no_dpapi_and_the_clone_lands_under_the_state_dir(self):
        code, output = self.run_main(["--collect", "--cached-ok"], FakeOpener({GET: result([row(1), row(2)])}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["result"], "inventory_collected")
        latest = json.loads(self.latest().read_text(encoding="utf-8"))
        self.assertEqual((latest["environment"], latest["row_count"]), ("prod-clone", 2))
        self.assertTrue((self.state / "redash" / "collector.log").is_file())
        for text in (output, (self.state / "redash" / "collector.log").read_text(encoding="utf-8")):
            self.assertNotIn(FAKE_KEY, text)
            self.assertNotIn("example.com", text)

    def test_only_get_works_is_an_honest_cached_read_exit_0_with_reason_and_age(self):
        opener = FakeOpener({GET: result([row(1)], RETRIEVED_STALE), POST: http_error(403)})
        code, output = self.run_main(["--collect", "--cached-ok"], opener)
        report = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual((report["result"], report["refreshed"], report["refresh_failed"], report["refresh_reason"],
                          report["data_age_minutes"]),
                         ("inventory_collected", False, True, "redash_refresh_not_permitted", 240))
        self.assertEqual([request[0] for request in opener.requests], ["GET", "POST"])
        self.assertTrue(self.latest().is_file())  # the genuine data is written
        trusted = inventory.load_latest(self.state / "clone-inventory", "prod-clone", max_age=timedelta(hours=6), now=NOW)
        self.assertEqual(trusted["row_count"], 1)
        # the age gate downstream still decides: the same data is not trusted 3 hours later
        with self.assertRaises(inventory.InventoryError):
            inventory.load_latest(self.state / "clone-inventory", "prod-clone", max_age=timedelta(hours=6),
                                  now=NOW + timedelta(hours=3))

    def test_the_desktop_exit_code_contract_is_unchanged_without_the_flag(self):
        opener = FakeOpener({GET: result([row(1)], RETRIEVED_STALE), POST: http_error(403)})
        code, output = self.run_main(["--collect"], opener)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["refresh_reason"], "redash_refresh_not_permitted")
        self.assertIn("exit codes: 0 = ok, 1 = failed", (ROOT / "tools" / "register_redash_inventory_task.ps1")
                      .read_text(encoding="utf-8").casefold())

    def test_a_fresh_cache_needs_no_refresh_and_records_no_reason(self):
        opener = FakeOpener({GET: result([row(1)], RETRIEVED_FRESH)})
        code, output = self.run_main(["--collect", "--cached-ok"], opener)
        report = json.loads(output)
        self.assertEqual((code, report["refreshed"], report["refresh_failed"], report["refresh_reason"]),
                         (0, False, False, None))
        self.assertEqual([request[0] for request in opener.requests], ["GET"])

    def test_cached_ok_never_hides_real_failures(self):
        for replies, reason in (({GET: http_error(403)}, "redash_auth_failed"),
                                ({GET: result([row(1)], "2026-10-03T08:00:00"), POST: http_error(403)}, "redash_results_stale"),
                                ({GET: http_error(500)}, "redash_http_error"),
                                ({GET: OSError("down")}, "redash_unreachable")):
            with self.subTest(reason=reason):
                code, output = self.run_main(["--collect", "--cached-ok"], FakeOpener(replies))
                self.assertEqual((code, json.loads(output)["result"]), (1, reason))
        self.assertFalse(self.latest().exists())

    def test_other_refresh_problems_keep_their_own_reason_code(self):
        stale = result([row(1)], RETRIEVED_STALE)
        for error, reason in ((http_error(429), "redash_rate_limited"), (OSError("down"), "redash_unreachable"),
                              (http_error(401), "redash_refresh_not_permitted"),
                              (http_error(500), "redash_refresh_not_permitted")):
            with self.subTest(reason=reason):
                report = collector.collect(FakeOpener({GET: stale, POST: error}), FAKE_KEY, root=self.tmp / "inv",
                                           now=NOW, sleep=lambda seconds: None)
                self.assertEqual((report["refresh_failed"], report["refresh_reason"]), (True, reason))
        failed_job = FakeOpener({GET: stale, POST: {"job": {"id": "abc12345-def"}},
                                 ("GET", "/api/jobs/abc12345-def"): {"job": {"status": 4}}})
        report = collector.collect(failed_job, FAKE_KEY, root=self.tmp / "inv2", now=NOW, sleep=lambda seconds: None)
        self.assertEqual(report["refresh_reason"], "redash_refresh_not_permitted")

    def test_a_successful_refresh_has_no_reason(self):
        opener = FakeOpener({GET: result([row(1)], RETRIEVED_STALE), POST: result([row(1)], RETRIEVED_FRESH)})
        report = collector.collect(opener, FAKE_KEY, root=self.tmp / "inv", now=NOW, sleep=lambda seconds: None)
        self.assertEqual((report["refreshed"], report["refresh_failed"], report["refresh_reason"]), (True, False, None))

    def test_there_is_no_dpapi_call_on_the_key_file_path(self):
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(collector, "_dpapi", side_effect=AssertionError("DPAPI")), \
                patch.object(collector, "load_key_file", return_value=FAKE_KEY):
            self.assertEqual(collector.load_key(), FAKE_KEY)


class TemplateTests(unittest.TestCase):
    def test_service_runs_the_cached_ok_collector_from_a_key_file_with_the_gates(self):
        self.assertIn("redash_inventory_collector.py --collect --cached-ok", SERVICE)
        self.assertIn("SuccessExitStatus=2", SERVICE)
        self.assertIn("Environment=SURFACE_REDASH_KEY_FILE=/etc/surface-onboarding/redash-key", SERVICE)
        self.assertIn("ConditionPathExists=/etc/surface-onboarding/approved-redash-collector", SERVICE)
        self.assertIn("ConditionPathExists=/etc/surface-onboarding/redash-key", SERVICE)
        self.assertIn("SupplementaryGroups=surface-redash-reader", SERVICE)
        self.assertIn("ReadWritePaths=/var/lib/surface-onboarding", SERVICE)
        self.assertNotRegex(SERVICE, r"[A-Za-z0-9]{40}")  # no key-shaped value anywhere
        self.assertIn("OnUnitActiveSec=1h", TIMER)
        self.assertIn("Unit=surface-onboarding-redash-collect.service", TIMER)

    def test_the_collector_accepts_the_flag_the_unit_passes(self):
        with patch.object(collector, "load_key", side_effect=collector.CollectorError("redash_key_unavailable")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(collector.main(["--collect", "--cached-ok"]), 1)


class ScriptTests(unittest.TestCase):
    def text(self, name):
        return SCRIPTS[name].read_bytes().decode("utf-8")

    def test_scripts_are_lf_strict_and_syntax_clean(self):
        sh = shutil.which("sh")
        for name, path in SCRIPTS.items():
            with self.subTest(script=name):
                raw = path.read_bytes()
                self.assertNotIn(b"\r", raw)
                self.assertTrue(raw.startswith(b"#!/bin/sh\n"))
                self.assertIn(b"\nset -", raw)
                if sh:
                    self.assertEqual(subprocess.run([sh, "-n", str(path)], capture_output=True).returncode, 0)

    def test_key_script_prompts_hidden_validates_shape_and_never_prints_the_key(self):
        text = self.text("_step18a_vm_redash_key.sh")
        for needle in ("set -eu", "stty -echo", "[ -t 0 ]", "root:\"$GROUP\"", "chmod 0640", "GROUP=surface-redash-reader",
                       "/etc/surface-onboarding/redash-key", "revert", "[!A-Za-z0-9]", "umask 0137"):
            self.assertIn(needle, text)
        for forbidden in ('echo "$VALUE"', "echo $VALUE", '"$VALUE" |', "set -x", "$1\"", "read -s"):
            self.assertNotIn(forbidden, text.replace("printf '%s' \"$VALUE\" | tr", "").replace(
                "printf '%s' \"$VALUE\" | wc", ""))
        self.assertNotRegex(text, r"[A-Za-z0-9]{40}")

    def test_enable_script_checks_creates_the_marker_the_unit_needs_and_reverts(self):
        text = self.text("_step18b_vm_redash_enable.sh")
        for needle in ("set -eu", "revert", "root:surface-redash-reader 640", "%{http_code}", "https://redash.pentera.io",
                       "MARKER=$DIR/approved-redash-collector", "systemctl enable --now", "systemctl disable --now",
                       "rm -f \"$MARKER\"", "daemon-reload", "surface-onboarding-redash-collect"):
            self.assertIn(needle, text)
        self.assertIn("approved-redash-collector", SERVICE)  # the marker the unit conditions on is the one created
        self.assertNotIn("Authorization", text)  # reachability check sends no key
        self.assertNotIn("$(cat", text)

    def test_status_script_is_read_only_and_secret_free(self):
        text = self.text("_step18c_vm_redash_status.sh")
        self.assertIn("latest.json", text)
        self.assertIn("clone-inventory/prod-clone", text)
        for forbidden in ("systemctl start", "systemctl enable", "systemctl stop", "systemctl disable", "rm ", "chmod",
                          "chown", "curl", "cat /etc/surface-onboarding/redash-key", "set -x"):
            self.assertNotIn(forbidden, text)
        self.assertIn("360", text)  # the 6 h trust line


if __name__ == "__main__":
    unittest.main()
