"""PC to VM automatic snapshot push (owner request 2026-10-27): the full signed snapshot travels over the same restricted
clone-push channel, and the VM page no longer waits for a manual ``_step12a`` ingest.

Hermetic: temporary folders, a fake HMAC key, no network, no ssh, no Salesforce. The PowerShell script is checked as text."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import tools.ingest_clone_push as ingest
from integration.onboarding import vm_snapshot as vs

REPO = Path(__file__).resolve().parents[2]
KEY = b"unit-test-snapshot-push-key-0123456789-abcdef-NOT-REAL"
OTHER_KEY = b"another-unit-test-key-0123456789-abcdefgh-NOT-REAL"
NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 27, 13, 0, 0, tzinfo=timezone.utc)
FILES = {vs.QUEUE_FILE: json.dumps([{"Name": "CO-0812"}]).encode(), vs.CO_DETAILS_FILE: json.dumps({}).encode()}


class SnapshotPushTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.key_file = self.tmp / "snapshot.key"
        self.key_file.write_bytes(KEY)

    def push(self, raw: bytes):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ingest.main(["--key-file", str(self.key_file), "--state-dir", str(self.state)], stdin=io.BytesIO(raw))
        return code, buffer.getvalue().strip()

    def bundle(self, producer=vs.SNAPSHOT_PUSH_PRODUCER, key=KEY, created=NOW, files=None):
        return vs.build_bundle(files or FILES, key, producer, created)

    def test_a_snapshot_push_bundle_becomes_the_current_snapshot(self):
        code, line = self.push(self.bundle())
        self.assertEqual(code, 0)
        self.assertRegex(line, r"^ingested-snapshot snapshot-20261027T120000Z$")
        root = vs.snapshots_root(self.state)
        self.assertEqual(vs.current_name(root), "snapshot-20261027T120000Z")
        self.assertEqual(vs.load_current_json(root, vs.QUEUE_FILE), [{"Name": "CO-0812"}])

    def test_a_newer_push_replaces_it_and_an_older_one_is_refused(self):
        self.assertEqual(self.push(self.bundle(created=LATER))[0], 0)
        code, line = self.push(self.bundle(created=NOW))
        self.assertEqual((code, line), (2, "refused:older_than_current"))
        self.assertEqual(vs.current_name(vs.snapshots_root(self.state)), "snapshot-20261027T130000Z")

    def test_only_the_snapshot_push_producer_is_accepted_as_a_snapshot_here(self):
        # The manual producer labels never take the snapshot path on this channel; they fall to the clone-only rules and fail.
        for producer in ("desktop", vs.PRODUCER_VM_READER, "anything-else"):
            code, line = self.push(self.bundle(producer=producer))
            self.assertEqual(code, 2, producer)
            self.assertTrue(line.startswith("refused:"), producer)
        self.assertIsNone(vs.current_name(vs.snapshots_root(self.state)))

    def test_a_bad_signature_changes_nothing(self):
        code, line = self.push(self.bundle(key=OTHER_KEY))
        self.assertEqual(code, 2)
        self.assertTrue(line.startswith("refused:"))
        self.assertIsNone(vs.current_name(vs.snapshots_root(self.state)))

    def test_a_name_outside_the_allow_list_is_refused(self):
        with self.assertRaises(vs.SnapshotError):
            self.bundle(files={**FILES, "evil.sh": b"echo no"})

    def test_output_is_one_line_with_no_content_or_key(self):
        _code, line = self.push(self.bundle())
        for secret in (KEY.decode(), "CO-0812"):
            self.assertNotIn(secret, line)

    def test_garbage_input_is_still_a_short_refusal(self):
        self.assertEqual(self.push(b"garbage"), (2, "refused:bad_zip"))


class PushScriptTextTests(unittest.TestCase):
    def test_the_hourly_script_pushes_the_snapshot_after_the_clone_without_new_secrets(self):
        run = (REPO / "tools" / "run_clone_push.ps1").read_text(encoding="utf-8")
        self.assertIn("publish_vm_snapshot.py", run)
        self.assertIn("desktop-snapshot-push", run)
        self.assertIn("--autopublish", run)
        self.assertLess(run.index(r"tools\build_clone_push_bundle.py"), run.index(r"tools\publish_vm_snapshot.py"))
        self.assertRegex(run, r"ingested-snapshot")
        self.assertIn("$SnapshotEveryMinutes = 180", run)  # about 6.5 minutes of Salesforce reads: not every hour
        self.assertIn("snapshot-last-success.txt", run)
        for text in (run,):
            self.assertIn("IdentitiesOnly=yes", text)
            self.assertIn("StrictHostKeyChecking=yes", text)
            self.assertIn("BatchMode=yes", text)
            self.assertNotRegex(text, r"(?i)password\s*=|Bearer\s+[A-Za-z0-9]|BEGIN (OPENSSH|RSA) PRIVATE")


if __name__ == "__main__":
    unittest.main()
