"""Interim clone push (docs/51): desktop clone-only bundle builder, VM stdin ingest, and the operator scripts.

Hermetic and portable (Windows and Linux): temporary folders, a fake HMAC key, fake tenant rows, no network, no ssh,
no Salesforce, no Redash. The shell scripts are checked as text only (never executed)."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tools.build_clone_push_bundle as builder
import tools.ingest_clone_push as ingest
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import redash_inventory as redash
from integration.onboarding import vm_snapshot as vs

REPO = Path(__file__).resolve().parents[2]
VM_PILOT = REPO / "integration" / "deployment" / "vm_pilot"
KEY = b"unit-test-clone-push-key-0123456789-abcdef-NOT-REAL"
OTHER_KEY = b"another-unit-test-key-0123456789-abcdefgh-NOT-REAL"
NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def row(index: int):
    return {
        "_id": f"68c9{index:020x}", "_created": "2026-08-11 13:57:03", "_modified": "2026-10-04T16:49:00.123000",
        "accountDomain": f"tenant{index}.example.com", "accountName": f"Example Tenant {index}",
        "accountUuid": f"{index:032x}", "alternateDomains": ["Alt.Example.com."], "enabled": True, "isDeleted": False,
        "lastReconScan": "2026-10-04T14:00:00Z", "lastReconScanDurationMilliseconds": 3600000.0,
        "scanningInterval": "WEEKLY", "accountType": "Customer", "operatorCount": 1,
        "accountLicense.assetsNumber": 10, "accountLicense.domainsNumber": 2.0, "accountLicense.subDomainsNumber": 50,
        "accountLicense.startDate": "2026-10-01T00:00:00Z", "accountLicense.expirationDate": 1790000000000,
        "accountLicense.licenseType": "Trial", "accountLicense.enabled": True,
        "accountLicense.scanningFrequency": "Weekly", "accountLicense.leakedCredentialsAllowed": True,
        "accountLicense.leakedCredentialsScannedDomainsNumber": 1, "lastScanStatusEnum": "COMPLETED",
        "leakedCredentialsSettings.spyCloudSettings": {"enabled": True},
    }


def write_clone(root: Path, rows: int, age: timedelta) -> None:
    """A real collector-shaped clone snapshot (latest.json + snapshot file) captured ``age`` before NOW."""
    items = [row(index) for index in range(1, rows + 1)]
    retrieved = (NOW - age).strftime("%Y-%m-%dT%H:%M:%S")
    names = list(items[0])
    normalized = redash.normalize({"query_result": {"retrieved_at": retrieved, "data": {
        "columns": [{"name": name, "type": "string"} for name in names], "rows": items}}})
    payload = inventory.snapshot_payload(
        inventory.require_environment("prod-clone"), inventory.assemble_rows(normalized.rows), normalized.retrieved_at,
        acquisition=inventory.REDASH_ACQUISITION, expected_paths=redash.EXPECTED_REDASH_PATHS)
    inventory.write_snapshot(payload, root, keep=4)


class Base(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        self.desktop = self.tmp / "desktop-clone"
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.key_file = self.tmp / "snapshot.key"
        self.key_file.write_bytes(KEY)
        patcher = patch.object(ingest, "FIRST_PUSH_MIN_ROWS", 10)  # the real floor is 1000; fixtures are tiny
        patcher.start()
        self.addCleanup(patcher.stop)

    def bundle(self, rows=20, age=timedelta(minutes=30), key=KEY, producer=ingest.PRODUCER, created=NOW,
               extra=None) -> bytes:
        shutil.rmtree(self.desktop, ignore_errors=True)
        write_clone(self.desktop, rows, age)
        files = builder.publisher._inventory_pair(self.desktop, "prod-clone", "redash-prod-clone-",
                                                  vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT)
        files.update(extra or {})
        return vs.build_bundle(files, key, producer, created)

    def clone_dir(self) -> Path:
        return self.state / "clone-inventory"

    def served(self):
        return inventory.load_latest(self.clone_dir(), "prod-clone", max_age=timedelta(days=3650), now=NOW)


class BuilderTests(Base):
    def test_builds_a_clone_only_signed_bundle(self):
        write_clone(self.desktop, 20, timedelta(minutes=30))
        out = self.tmp / "bundle.zip"
        counts = builder.build(str(self.key_file), out, self.desktop, NOW)
        self.assertEqual(counts["rows"], 20)
        self.assertEqual(counts["data_age_minutes"], 30)
        verified = vs.verify_bundle(out, KEY)
        self.assertEqual(set(verified.files), ingest.CLONE_FILES)
        self.assertEqual(verified.manifest["producer"], builder.PRODUCER)
        with zipfile.ZipFile(out) as archive:  # no queue, no state files, no Salesforce data
            self.assertEqual(sorted(archive.namelist()), sorted(["manifest.json", "manifest.sig", *ingest.CLONE_FILES]))

    def test_refuses_missing_stale_and_unreadable_inputs(self):
        out = self.tmp / "bundle.zip"
        with self.assertRaises(builder.BuildRefused) as missing:
            builder.build(str(self.key_file), out, self.desktop, NOW)
        self.assertEqual(missing.exception.code, "local_clone_missing")
        write_clone(self.desktop, 20, timedelta(hours=7))
        with self.assertRaises(builder.BuildRefused) as stale:
            builder.build(str(self.key_file), out, self.desktop, NOW)
        self.assertEqual(stale.exception.code, "inventory_snapshot_stale")
        with self.assertRaises(builder.BuildRefused) as nokey:
            builder.build(str(self.tmp / "nope.key"), out, self.desktop, NOW)
        self.assertEqual(nokey.exception.code, "key_unreadable")
        self.assertFalse(out.exists())

    def test_cli_prints_counts_and_code_only(self):
        write_clone(self.desktop, 20, timedelta(minutes=5))
        out = self.tmp / "b.zip"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = builder.main(["--key-file", str(self.tmp / "missing.key"), "--out", str(out)])
        self.assertEqual((code, buffer.getvalue().strip()), (2, "refused:key_unreadable"))


class IngestTests(Base):
    def test_ingests_and_serves_the_clone_with_its_true_age(self):
        message = ingest.ingest(self.bundle(), KEY, self.state, NOW)
        self.assertTrue(message.startswith("ingested "))
        served = self.served()
        self.assertEqual(served["row_count"], 20)
        self.assertEqual(served["age_seconds"], 30 * 60)  # the age is the Redash data age, not the push time

    def test_same_bundle_twice_is_a_noop(self):
        raw = self.bundle()
        ingest.ingest(raw, KEY, self.state, NOW)
        before = sorted(p.name for p in (self.clone_dir() / "prod-clone").iterdir())
        self.assertTrue(ingest.ingest(raw, KEY, self.state, NOW).startswith("unchanged "))
        self.assertEqual(before, sorted(p.name for p in (self.clone_dir() / "prod-clone").iterdir()))

    def test_newer_replaces_older_and_old_files_are_pruned(self):
        for minutes in (400 - 60 * n for n in range(0, 7)):  # 400, 340, ... 40 minutes old: always newer than the last
            age = timedelta(minutes=min(minutes, 350))
            ingest.ingest(self.bundle(age=age), KEY, self.state, NOW)
        snapshots = [p for p in (self.clone_dir() / "prod-clone").iterdir() if p.name != "latest.json"]
        self.assertLessEqual(len(snapshots), ingest.KEEP)
        self.assertEqual(self.served()["age_seconds"], 40 * 60)

    def refused(self, raw, now=NOW, key=KEY):
        with self.assertRaises(ingest.PushRefused) as caught:
            ingest.ingest(raw, key, self.state, now)
        return caught.exception.code

    def test_rejects_an_older_bundle_and_keeps_the_current_one(self):
        ingest.ingest(self.bundle(age=timedelta(minutes=30)), KEY, self.state, NOW)
        self.assertEqual(self.refused(self.bundle(age=timedelta(minutes=90))), "older_than_current")
        self.assertEqual(self.served()["age_seconds"], 30 * 60)

    def test_rejects_shrink_but_allows_small_variation(self):
        ingest.ingest(self.bundle(rows=100, age=timedelta(minutes=120)), KEY, self.state, NOW)
        self.assertEqual(self.refused(self.bundle(rows=80, age=timedelta(minutes=60))), "result_shrunk")
        self.assertEqual(self.served()["row_count"], 100)
        ingest.ingest(self.bundle(rows=95, age=timedelta(minutes=30)), KEY, self.state, NOW)
        self.assertEqual(self.served()["row_count"], 95)

    def test_rejects_data_from_the_future_and_data_older_than_6_hours(self):
        self.assertEqual(self.refused(self.bundle(age=timedelta(minutes=-30))), "inventory_snapshot_tampered")
        self.assertEqual(self.refused(self.bundle(age=timedelta(hours=7))), "inventory_snapshot_stale")
        self.assertEqual(self.refused(self.bundle(created=NOW + timedelta(hours=1))), "future_bundle")
        self.assertFalse((self.clone_dir() / "prod-clone" / "latest.json").exists())

    def test_rejects_bad_signature_wrong_producer_and_extra_members(self):
        self.assertEqual(self.refused(self.bundle(key=OTHER_KEY)), "bad_signature")
        self.assertEqual(self.refused(self.bundle(producer="desktop")), "wrong_producer")
        extra = {vs.QUEUE_FILE: b'{"rows": []}'}
        self.assertEqual(self.refused(self.bundle(extra=extra)), "not_a_clone_only_bundle")
        self.assertEqual(self.refused(b"PK not a zip at all"), "bad_zip")
        self.assertEqual(self.refused(self.bundle()[:-40]), "bad_zip")

    def test_rejects_a_tampered_snapshot_even_when_correctly_signed(self):
        write_clone(self.desktop, 20, timedelta(minutes=30))
        files = builder.publisher._inventory_pair(self.desktop, "prod-clone", "redash-prod-clone-",
                                                  vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT)
        payload = json.loads(files[vs.INVENTORY_CLONE_SNAPSHOT])
        payload["tenants"][0]["account_name"] = "Injected"
        files[vs.INVENTORY_CLONE_SNAPSHOT] = json.dumps(payload).encode()
        self.assertEqual(self.refused(vs.build_bundle(files, KEY, ingest.PRODUCER, NOW)), "inventory_snapshot_tampered")

    def test_a_dev_inventory_cannot_pass_as_the_clone(self):
        dev_root = self.tmp / "dev"
        items = [row(1), row(2)]
        normalized = redash.normalize({"query_result": {"retrieved_at": "2026-10-17T11:30:00", "data": {
            "columns": [{"name": name, "type": "string"} for name in items[0]], "rows": items}}})
        payload = inventory.snapshot_payload(inventory.require_environment("dev"), inventory.assemble_rows(normalized.rows),
                                             normalized.retrieved_at)
        inventory.write_snapshot(payload, dev_root)
        files = builder.publisher._inventory_pair(dev_root, "dev", "", vs.INVENTORY_CLONE_LATEST,
                                                  vs.INVENTORY_CLONE_SNAPSHOT)
        self.assertEqual(self.refused(vs.build_bundle(files, KEY, ingest.PRODUCER, NOW)), "inventory_invalid")

    def test_failure_leaves_no_staging_folder_and_keeps_current(self):
        ingest.ingest(self.bundle(), KEY, self.state, NOW)
        self.refused(self.bundle(key=OTHER_KEY))
        self.assertEqual([p.name for p in self.clone_dir().iterdir()], ["prod-clone"])

    def test_stdin_size_cap_and_cli(self):
        with self.assertRaises(ingest.PushRefused) as caught:
            ingest.read_stdin_limited(io.BytesIO(b"x" * 11), limit=10)
        self.assertEqual(caught.exception.code, "bundle_too_large")
        with self.assertRaises(ingest.PushRefused):
            ingest.read_stdin_limited(io.BytesIO(b""))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ingest.main(["--key-file", str(self.key_file), "--state-dir", str(self.state)],
                               stdin=io.BytesIO(b"garbage"))
        self.assertEqual((code, buffer.getvalue().strip()), (2, "refused:bad_zip"))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ingest.main(["--key-file", str(self.key_file), "--state-dir", "relative/dir"], stdin=io.BytesIO(b"x"))
        self.assertEqual((code, buffer.getvalue().strip()), (64, "refused:state_dir_not_absolute"))

    def test_output_never_contains_content_or_key(self):
        raw = self.bundle()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            ingest.main(["--key-file", str(self.key_file), "--state-dir", str(self.state)], stdin=io.BytesIO(raw))
        text = buffer.getvalue()
        self.assertTrue(text.startswith("ingested "))
        for secret in (KEY.decode(), "Example Tenant", "tenant1.example.com"):
            self.assertNotIn(secret, text)


class HardeningTests(Base):
    def refused(self, raw, now=NOW):
        with self.assertRaises(ingest.PushRefused) as caught:
            ingest.ingest(raw, KEY, self.state, now)
        return caught.exception.code

    def test_high_water_survives_a_missing_or_corrupt_latest(self):
        ingest.ingest(self.bundle(rows=100, age=timedelta(minutes=120)), KEY, self.state, NOW)
        latest = self.clone_dir() / "prod-clone" / "latest.json"
        latest.unlink()
        self.assertEqual(self.refused(self.bundle(rows=50, age=timedelta(minutes=60))), "result_shrunk")
        self.assertEqual(self.refused(self.bundle(rows=100, age=timedelta(minutes=200))), "older_than_current")
        latest.write_text("{ not json")
        self.assertEqual(self.refused(self.bundle(rows=50, age=timedelta(minutes=60))), "result_shrunk")
        ingest.ingest(self.bundle(rows=100, age=timedelta(minutes=30)), KEY, self.state, NOW)  # recovery still works
        self.assertEqual(self.served()["row_count"], 100)

    def test_a_corrupt_high_water_record_fails_closed(self):
        ingest.ingest(self.bundle(rows=100), KEY, self.state, NOW)
        (self.state / ingest.HIGH_WATER_NAME).write_text("garbage")
        self.assertEqual(self.refused(self.bundle(rows=100, age=timedelta(minutes=5))), "high_water_unreadable")

    def test_first_push_needs_the_absolute_floor_and_only_the_vm_can_lower_it(self):
        with patch.object(ingest, "FIRST_PUSH_MIN_ROWS", 1000):
            self.assertEqual(self.refused(self.bundle(rows=20)), "below_first_push_floor")
            with self.assertRaises(ingest.PushRefused):
                ingest.ingest(self.bundle(rows=20), KEY, self.state, NOW)
            self.assertTrue(ingest.ingest(self.bundle(rows=20), KEY, self.state, NOW, min_rows=15).startswith("ingested"))
        self.assertFalse(any("min_rows" in name for name in ingest.vs.ALLOWED_NAMES))  # nothing in a bundle can set it

    def test_shrink_cannot_compound(self):
        ingest.ingest(self.bundle(rows=100, age=timedelta(minutes=200)), KEY, self.state, NOW)
        ingest.ingest(self.bundle(rows=95, age=timedelta(minutes=120)), KEY, self.state, NOW)
        ingest.ingest(self.bundle(rows=91, age=timedelta(minutes=60)), KEY, self.state, NOW)
        self.assertEqual(self.refused(self.bundle(rows=89, age=timedelta(minutes=10))), "result_shrunk")  # 89 < 90% of 100
        self.assertEqual(self.served()["row_count"], 91)

    def test_failed_readback_restores_the_previous_clone_and_never_goes_live(self):
        ingest.ingest(self.bundle(rows=20, age=timedelta(minutes=90)), KEY, self.state, NOW)
        raw = self.bundle(rows=20, age=timedelta(minutes=10))
        with patch.object(ingest, "_readback", return_value=None):
            self.assertEqual(self.refused(raw), "readback_failed")
        self.assertEqual(self.served()["age_seconds"], 90 * 60)
        record = json.loads((self.state / ingest.HIGH_WATER_NAME).read_text())
        self.assertNotEqual(record["newest_captured_at"], (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        ingest.ingest(raw, KEY, self.state, NOW)  # and the same push succeeds afterwards
        self.assertEqual(self.served()["age_seconds"], 10 * 60)

    def test_failed_first_readback_leaves_no_latest(self):
        with patch.object(ingest, "_readback", return_value=None):
            self.assertEqual(self.refused(self.bundle()), "readback_failed")
        self.assertFalse((self.clone_dir() / "prod-clone" / "latest.json").exists())

    def test_stale_staging_folders_are_swept_but_fresh_ones_kept(self):
        old = self.clone_dir() / ".incoming-111"
        fresh = self.clone_dir() / ".incoming-222"
        for folder in (old, fresh):
            folder.mkdir(parents=True)
        import os
        past = NOW.timestamp() - 7200
        os.utime(old, (past, past))
        with patch.object(ingest.time, "time", return_value=NOW.timestamp()):
            ingest.ingest(self.bundle(), KEY, self.state, NOW)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_a_clone_push_bundle_is_never_ingested_as_a_full_snapshot(self):
        with self.assertRaises(vs.SnapshotError) as caught:
            vs.ingest_bundle(self.bundle(), KEY, self.state)
        self.assertEqual(caught.exception.code, "clone_push_bundle_not_a_snapshot")
        self.assertFalse((self.state / "snapshots" / "current").exists())


class ScriptTextTests(unittest.TestCase):
    """The operator scripts are reviewed artifacts: pin the safety properties by text (nothing is executed)."""

    def read(self, name: str) -> str:
        path = VM_PILOT / name
        data = path.read_bytes()
        self.assertNotIn(b"\r", data, name + " must use LF line endings (it runs on Linux)")
        return data.decode("utf-8")

    def test_step22a_installs_a_root_owned_gate_and_an_argument_free_sudoers_rule(self):
        text = self.read("_step22a_vm_install_clone_push_gate.sh")
        self.assertIn('[ "$(id -u)" -eq 0 ]', text)
        self.assertIn("visudo -cf", text)
        self.assertIn('workato ALL=(surface-onboarding) NOPASSWD: /usr/local/sbin/surface-clone-push-ingest ""', text)
        self.assertIn("approved-clone-push", text)  # fail closed without the owner's marker
        self.assertIn("flock", text)
        self.assertIn("revert", text)
        self.assertNotIn("$SSH_ORIGINAL_COMMAND", text)
        self.assertIn("env -i", text)
        self.assertNotRegex(text, r"(?m)^\s*(eval|sh -c|bash -c)\b")

    def test_step22b_authorizes_one_restricted_key(self):
        text = self.read("_step22b_vm_authorize_clone_push_key.sh")
        self.assertIn('command="/usr/local/sbin/surface-clone-push-gate",from="%s",restrict %s %s', text)
        self.assertIn("ssh-ed25519", text)
        self.assertIn("surface-clone-push", text)
        self.assertIn("revert", text)
        self.assertIn("approved-clone-push", text)
        self.assertNotIn("PRIVATE KEY", text)
        line = next(item for item in text.splitlines() if item.lstrip().startswith("printf 'command="))
        self.assertTrue(line.split("'", 2)[1].startswith('command="/usr/local/sbin/surface-clone-push-gate",from="%s",restrict '))
        for loosened in ("permitopen", "port-forwarding", "pty", "environment"):
            self.assertNotIn(loosened, line)
        self.assertIn('0.0.0.0', text)  # any-address is refused explicitly
        self.assertRegex(text, r'\[ "\$PFX" -ge 16 \]')
        self.assertIn(r' $TAG\$', text)  # a re-run replaces its own line (changed FROM takes effect)

    def test_step22a_ingest_program_enforces_the_approval_marker_before_anything_else(self):
        text = self.read("_step22a_vm_install_clone_push_gate.sh")
        program = text.split("<<'EOF_INGEST'" + chr(10), 1)[1].split(chr(10) + "EOF_INGEST", 1)[0]
        marker = program.index("/etc/surface-onboarding/approved-clone-push")
        for later in ("flock", "ingest_clone_push.py", "python"):
            self.assertLess(marker, program.index(later), later)
        self.assertIn("exit 3", program)

    def test_step22c_empty_capture_time_is_not_trusted(self):
        text = self.read("_step22c_vm_clone_push_status.sh")
        self.assertIn('[ -n "$CAP" ]', text)
        self.assertIn("unknown -> NOT trusted", text)

    def test_step22c_is_read_only_status(self):
        text = self.read("_step22c_vm_clone_push_status.sh")
        for forbidden in ("systemctl enable", "systemctl start", "rm -r", "curl"):
            self.assertNotIn(forbidden, text)

    def test_every_new_script_is_review_artifact_and_posix_sh(self):
        for name in ("_step22a_vm_install_clone_push_gate.sh", "_step22b_vm_authorize_clone_push_key.sh",
                     "_step22c_vm_clone_push_status.sh"):
            text = self.read(name)
            self.assertTrue(text.startswith("#!/bin/sh\n# REVIEW ARTIFACT"), name)
            self.assertIn("set -", text)
            self.assertIsNone(re.search(r"(?i)(password|bearer |token\s*=)", text.replace("no password", "")), name)

    def test_powershell_scripts_have_whatif_remove_and_no_secrets(self):
        register = (REPO / "tools" / "register_clone_push_task.ps1").read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess", register)
        self.assertIn("[switch]$Remove", register)
        run = (REPO / "tools" / "run_clone_push.ps1").read_text(encoding="utf-8")
        self.assertIn("--cached-ok", run)
        self.assertIn("IdentitiesOnly=yes", run)
        self.assertIn("StrictHostKeyChecking=yes", run)
        self.assertIn("BatchMode=yes", run)
        for text in (register, run):
            self.assertNotRegex(text, r"(?i)password\s*=|Bearer\s+[A-Za-z0-9]|BEGIN (OPENSSH|RSA) PRIVATE")


if __name__ == "__main__":
    unittest.main()
