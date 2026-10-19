"""The production runner child's redacted log (2026-10-19): the dashboard started it with stdout/stderr discarded, so the
00:11Z production_session_not_ready attempt left no trace. The runner now writes a fixed-enum, redacted log itself when the
dashboard names a log file: result codes, record names and a fixed set of operator lines only; a traceback becomes its
exception class name; anything else (and anything UUID-shaped) is withheld. Hermetic: temporary files only.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.onboarding import runner_child_log as rcl

UUID = "0123456789abcdef0123456789abcdef"


class SanitizeTests(unittest.TestCase):
    def test_result_lines_keep_allow_listed_codes_only(self):
        line = json.dumps({"result": "production_session_not_ready", "outcome": "refused",
                           "record": "CO-0798-20261019T001143Z.json", "record_error": None, "account_uuid": UUID,
                           "note": "free text Bearer abc", "salesforce_writeback": "not_performed"})
        out = json.loads(rcl.sanitize("stdout", line))
        self.assertEqual(out, {"result": "production_session_not_ready", "outcome": "refused",
                               "record": "CO-0798-20261019T001143Z.json", "record_error": None,
                               "salesforce_writeback": "not_performed"})

    def test_a_uuid_shaped_or_free_text_value_is_dropped(self):
        for value in (UUID, "0123abcd-0123-abcd-0123-0123456789ab", "Signed in as Bob", "x" * 200):
            out = json.loads(rcl.sanitize("stdout", json.dumps({"result": value, "outcome": "refused"})))
            self.assertNotIn("result", out, value)

    def test_known_operator_lines_pass_and_others_are_withheld(self):
        self.assertEqual(rcl.sanitize("stdout", "Sign in with SSO and MFA in the window titled app.pentera.io; waiting up to 15 minutes"),
                         "Sign in with SSO and MFA in the window titled app.pentera.io; waiting up to 15 minutes")
        self.assertEqual(rcl.sanitize("stdout", "token=abc cookie=def"), rcl.WITHHELD)
        self.assertIsNone(rcl.sanitize("stdout", "   "))

    def test_a_traceback_keeps_only_the_exception_class(self):
        self.assertEqual(rcl.sanitize("stderr", "Traceback (most recent call last):"), "stderr: traceback")
        self.assertEqual(rcl.sanitize("stderr", "playwright._impl._errors.TimeoutError: Timeout 30000ms exceeded https://x/?t=1"),
                         "stderr: playwright._impl._errors.TimeoutError")
        self.assertIsNone(rcl.sanitize("stderr", '  File "C:\\x.py", line 3, in f'))
        self.assertIsNone(rcl.sanitize("stderr", "    page.goto(url)"))


class StreamTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "runner.log"

    def test_partial_writes_are_joined_and_every_line_is_stamped(self):
        stream = rcl.SanitizingStream(self.path, "stdout")
        stream.write('{"result": "created_verified", ')
        stream.write('"outcome": "readback_verified"}\nnot allowed\n')
        stream.flush()
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z stdout ")
        self.assertIn('"created_verified"', lines[0])
        self.assertTrue(lines[1].endswith(rcl.WITHHELD))
        self.assertNotIn("not allowed", self.path.read_text(encoding="utf-8"))

    def test_an_unwritable_log_never_breaks_the_run(self):
        stream = rcl.SanitizingStream(Path(self.path.parent / "missing" / "deeper" / "x.log"), "stdout")
        with patch.object(rcl.Path, "open", side_effect=OSError):
            stream.write("Sign in with SSO and MFA in the window titled app.pentera.io; waiting up to 15 minutes\n")
            stream.flush()

    def test_install_only_for_production_runs_with_a_named_absolute_log(self):
        saved = sys.stdout, sys.stderr
        self.addCleanup(lambda: (setattr(sys, "stdout", saved[0]), setattr(sys, "stderr", saved[1])))
        self.assertFalse(rcl.install_from_env({}, ["--production-create"]))
        self.assertFalse(rcl.install_from_env({rcl.LOG_ENV: "relative.log"}, ["--production-create"]))
        self.assertFalse(rcl.install_from_env({rcl.LOG_ENV: str(self.path)}, ["--co", "CO-0798"]))
        self.assertTrue(rcl.install_from_env({rcl.LOG_ENV: str(self.path)}, ["--production-create", "--co", "CO-0798"]))
        self.assertIsInstance(sys.stdout, rcl.SanitizingStream)
        print(json.dumps({"result": "refused", "account_uuid": UUID}), flush=True)
        sys.stdout, sys.stderr = saved
        self.assertNotIn(UUID, self.path.read_text(encoding="utf-8"))


class DashboardWiringTests(unittest.TestCase):
    def test_the_dashboard_names_a_log_file_and_still_discards_the_pipes(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                patch.object(dashboard, "runner_log_dir", return_value=Path(tmp.name)), \
                patch.object(dashboard.subprocess, "Popen") as popen:
            dashboard._launch_production_create("CO-0798", "2026-10-16T18:25:50.000+0000", {})
        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs["stdout"], dashboard.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], dashboard.subprocess.DEVNULL)
        log = Path(kwargs["env"][rcl.LOG_ENV])
        self.assertEqual(log.parent, Path(tmp.name))
        self.assertRegex(log.name, r"^CO-0798-\d{8}T\d{6}Z\.log$")

    def test_no_log_folder_still_launches_without_a_log(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard
        with patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                patch.object(dashboard, "runner_log_dir", return_value=None), \
                patch.object(dashboard.subprocess, "Popen") as popen, patch.dict(os.environ, {}, clear=False):
            os.environ.pop(rcl.LOG_ENV, None)
            dashboard._launch_production_create("CO-0798", "2026-10-16T18:25:50.000+0000", {})
        self.assertNotIn(rcl.LOG_ENV, popen.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
