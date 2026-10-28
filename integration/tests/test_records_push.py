"""PC to VM push of the production create records (owner request 2026-10-27: the VM queue must show what the desktop shows).

The VM never receives approval files. It receives the already-masked create records (the same files the desktop page reads),
merged into the CURRENT snapshot so queue and CO details are never dropped. Hermetic: temporary folders, a fake HMAC key, no
network, no ssh, no Salesforce."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tools.build_records_push_bundle as builder
import tools.ingest_clone_push as ingest
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_views
from integration.onboarding import vm_snapshot as vs

KEY = b"unit-test-records-push-key-0123456789-abcdef-NOT-REAL"
OTHER_KEY = b"another-unit-test-key-0123456789-abcdefgh-NOT-REAL"
NOW = datetime(2026, 10, 27, 20, 0, 0, tzinfo=timezone.utc)
BASE_FILES = {vs.QUEUE_FILE: json.dumps({"rows": [{"Name": "CO-0812"}]}).encode(),
              vs.CO_DETAILS_FILE: json.dumps({"details": {"CO-0812": {"Name": "CO-0812"}}}).encode()}


def record(co="CO-0812", outcome="readback_verified", **extra):
    return {"co_reference": co, "route": "case_2_new_ce_only", "approval_ref": "PROD-CE-0812-1", "outcome": outcome,
            "tenant_name": "Shannon Engine Support (SES) - CE Only", "domains": ["ses.aero"], "account_id": "a" * 24,
            "account_uuid": "b" * 32, "plan_summary": "CE, 1 year", "salesforce_revision": "REV-1", **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        self.desktop = self.tmp / "desktop-state"
        self.desktop.mkdir()
        self.state = self.tmp / "vm-state"
        self.state.mkdir()
        self.key_file = self.tmp / "snapshot.key"
        self.key_file.write_bytes(KEY)

    def current_snapshot(self, producer=vs.PRODUCER_VM_READER, created=NOW - timedelta(minutes=30)):
        vs.ingest_bundle(vs.build_bundle(BASE_FILES, KEY, producer, created), KEY, self.state)

    def write_record(self, moment=NOW - timedelta(hours=1), **kwargs):
        return pcr.record(self.desktop, record(**kwargs), moment)

    def build(self, now=NOW) -> bytes:
        out = self.tmp / "records.zip"
        builder.build(str(self.key_file), out, self.desktop, now)
        return out.read_bytes()

    def push(self, raw: bytes):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ingest.main(["--key-file", str(self.key_file), "--state-dir", str(self.state)], stdin=io.BytesIO(raw))
        return code, buffer.getvalue().strip()


class BuilderTests(Base):
    def test_it_bundles_only_the_masked_records_under_the_records_producer(self):
        self.write_record()
        raw = self.build()
        bundle = vs.verify_bundle(raw, KEY)
        self.assertEqual(bundle.manifest["producer"], vs.RECORDS_PUSH_PRODUCER)
        self.assertEqual(sorted(bundle.files), [vs.PRODUCTION_RECORDS_FILE])
        records = json.loads(bundle.files[vs.PRODUCTION_RECORDS_FILE])["records"]
        self.assertEqual(len(records), 1)
        self.assertTrue(next(iter(records)).startswith("CO-0812-"))

    def test_no_records_is_an_empty_but_valid_bundle(self):
        records = json.loads(vs.verify_bundle(self.build(), KEY).files[vs.PRODUCTION_RECORDS_FILE])["records"]
        self.assertEqual(records, {})

    def test_a_corrupt_record_refuses_the_build(self):
        path = self.write_record()
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(builder.BuildRefused):
            self.build()


class IngestTests(Base):
    def test_records_are_merged_into_the_current_snapshot_without_dropping_anything(self):
        self.current_snapshot()
        self.write_record()
        code, line = self.push(self.build())
        self.assertEqual((code, line), (0, "ingested-records " + vs.current_name(vs.snapshots_root(self.state))))
        root = vs.snapshots_root(self.state)
        self.assertEqual(vs.load_current_json(root, vs.QUEUE_FILE), {"rows": [{"Name": "CO-0812"}]})
        self.assertEqual(vs.load_current_json(root, vs.CO_DETAILS_FILE)["details"]["CO-0812"], {"Name": "CO-0812"})
        # the producer of the current snapshot is kept, so the banner still says where the Salesforce data came from
        self.assertEqual(vs.read_current_manifest(root)["producer"], vs.PRODUCER_VM_READER)

    def test_the_dashboard_reader_sees_the_records_as_verified_in_the_standard_folder(self):
        self.current_snapshot()
        self.write_record()
        self.assertEqual(self.push(self.build())[0], 0)
        current = vs.current_dir(vs.snapshots_root(self.state))
        view = production_views.create_records_by_co(current, missing_is_unavailable=True)
        self.assertEqual(view["state"], "ok")
        self.assertIn("CO-0812", view["by_co"])

    def test_the_same_records_twice_are_unchanged_and_make_no_new_snapshot(self):
        self.current_snapshot()
        self.write_record()
        raw = self.build()
        self.assertEqual(self.push(raw)[0], 0)
        before = vs.list_snapshots(vs.snapshots_root(self.state))
        self.assertEqual(self.push(self.build(now=NOW + timedelta(minutes=1))), (0, "unchanged-records"))
        self.assertEqual(vs.list_snapshots(vs.snapshots_root(self.state)), before)

    def test_without_a_current_snapshot_it_refuses_and_creates_nothing(self):
        self.write_record()
        self.assertEqual(self.push(self.build()), (2, "refused:no_current_snapshot"))
        self.assertIsNone(vs.current_name(vs.snapshots_root(self.state)))

    def test_a_bad_signature_or_extra_files_change_nothing(self):
        self.current_snapshot()
        before = vs.current_name(vs.snapshots_root(self.state))
        self.write_record()
        raw = self.build()
        other = tempfile.NamedTemporaryFile(delete=False)
        self.addCleanup(Path(other.name).unlink)
        other.close()
        bad = vs.build_bundle(vs.verify_bundle(raw, KEY).files, OTHER_KEY, vs.RECORDS_PUSH_PRODUCER, NOW)
        self.assertEqual(self.push(bad)[0], 2)
        extra = vs.build_bundle({**vs.verify_bundle(raw, KEY).files, vs.QUEUE_FILE: b"{}"}, KEY, vs.RECORDS_PUSH_PRODUCER, NOW)
        self.assertEqual(self.push(extra), (2, "refused:records_bundle_has_other_files"))
        self.assertEqual(vs.current_name(vs.snapshots_root(self.state)), before)

    def test_an_invalid_record_inside_a_signed_bundle_is_refused_and_keeps_the_current_snapshot(self):
        self.current_snapshot()
        before = vs.current_name(vs.snapshots_root(self.state))
        payload = json.dumps({"records": {"CO-0812-20261027T190000Z.json": {"co_reference": "CO-0812", "outcome": "x"}}}).encode()
        raw = vs.build_bundle({vs.PRODUCTION_RECORDS_FILE: payload}, KEY, vs.RECORDS_PUSH_PRODUCER, NOW)
        code, line = self.push(raw)
        self.assertEqual(code, 2)
        self.assertTrue(line.startswith("refused:"))
        self.assertEqual(vs.current_name(vs.snapshots_root(self.state)), before)

    def test_a_record_name_that_could_escape_the_folder_is_refused(self):
        self.current_snapshot()
        payload = json.dumps({"records": {"../evil.json": record()}}).encode()
        raw = vs.build_bundle({vs.PRODUCTION_RECORDS_FILE: payload}, KEY, vs.RECORDS_PUSH_PRODUCER, NOW)
        self.assertEqual(self.push(raw)[0], 2)

    def test_the_vm_refresher_keeps_carrying_the_records_file(self):
        from tools import vm_refresh_snapshot as refresher
        self.assertNotIn(vs.PRODUCTION_RECORDS_FILE, refresher.CARRY_EXCLUDED)

    def test_output_is_one_short_line_with_no_record_content(self):
        self.current_snapshot()
        self.write_record()
        _code, line = self.push(self.build())
        for secret in (KEY.decode(), "Shannon", "ses.aero", "b" * 32):
            self.assertNotIn(secret, line)


class ScriptTextTests(unittest.TestCase):
    def test_the_hourly_script_pushes_records_after_the_clone_and_keeps_the_snapshot_push_optional(self):
        run = (Path(__file__).resolve().parents[2] / "tools" / "run_clone_push.ps1").read_text(encoding="utf-8")
        self.assertIn(r"tools\build_records_push_bundle.py", run)
        self.assertIn("ingested-records", run)
        self.assertLess(run.index(r"tools\build_clone_push_bundle.py"), run.index(r"tools\build_records_push_bundle.py"))
        self.assertIn("[switch]$PushSnapshot", run)  # the VM now refreshes Salesforce itself; the PC snapshot push is opt-in
        for text in (run,):
            self.assertIn("IdentitiesOnly=yes", text)
            self.assertIn("BatchMode=yes", text)


if __name__ == "__main__":
    unittest.main()
