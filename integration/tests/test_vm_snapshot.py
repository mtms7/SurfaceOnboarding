"""Signed VM snapshot (docs/41): bundle round trip, every refusal, atomic switch, prune, tools, no secret in output.

Hermetic: temporary folders only; the key is a made-up test value that exists only in this file. No real attended_*.json
is read or written (the publisher tests point the state folder at a temporary one).
"""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from integration.onboarding import vm_snapshot as vs
from integration.onboarding import leonardo_inventory
import tools.attended_ce_only_playwright as runner
import tools.ingest_vm_snapshot as ingest_tool
import tools.publish_vm_snapshot as publish_tool

KEY = b"unit-test-snapshot-key-0123456789-abcdef-NOT-REAL"
OTHER_KEY = b"another-unit-test-key-0123456789-abcdef-NOT-REAL"
BASE_TIME = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
CONTENT = b'{"CO-0001": {"outcome": "SENTINEL-CONTENT-DO-NOT-PRINT"}}'


def good_files(**extra):
    files = {"attended_leonardo_readbacks.json": CONTENT, "queue.json": b'{"rows": []}'}
    files.update(extra)
    return files


def craft(files, *, listed=None, mutate=None, extra=None, attrs=None, sign_key=KEY, created_at=BASE_TIME,
          sign_over=None):
    """A bundle written by hand so a test can break exactly one rule (the real builder refuses most of them)."""
    listed = files if listed is None else listed
    manifest = {"schema_version": vs.SCHEMA_VERSION, "created_at": vs._stamp(created_at), "producer": "test",
                "files": [{"name": name, "size": len(data), "sha256": __import__("hashlib").sha256(data).hexdigest()}
                          for name, data in sorted(listed.items())]}
    if mutate:
        mutate(manifest)
    signed = manifest if sign_over is None else sign_over
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(vs.MANIFEST_NAME, vs.canonical_manifest_bytes(manifest))
            archive.writestr(vs.SIGNATURE_NAME, vs.sign_manifest(signed, sign_key))
            for name, data in {**files, **(extra or {})}.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                if attrs and name in attrs:
                    info.external_attr = attrs[name] << 16
                archive.writestr(info, data)
    return buffer.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="surface_snapshot_test_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"

    def ingest(self, data, **kwargs):
        return vs.ingest_bundle(data, KEY, self.state, **kwargs)

    @property
    def root(self):
        return vs.snapshots_root(self.state)

    def code(self, data, key=KEY):
        with self.assertRaises(vs.SnapshotError) as caught:
            vs.verify_bundle(data, key)
        return caught.exception.code


class BundleRoundTripTests(TempCase):
    def test_build_verify_ingest_round_trip(self):
        data = vs.build_bundle(good_files(), KEY, "desktop", BASE_TIME)
        verified = vs.verify_bundle(data, KEY)
        self.assertEqual(verified.files, good_files())
        self.assertEqual(verified.manifest["schema_version"], 1)
        self.assertEqual(verified.manifest["created_at"], "2026-10-08T12:00:00Z")
        self.assertEqual(verified.manifest["producer"], "desktop")
        self.assertEqual([entry["name"] for entry in verified.manifest["files"]], sorted(good_files()))
        name = self.ingest(data)
        self.assertEqual(vs.current_name(self.root), name)
        directory = vs.current_dir(self.root)
        self.assertEqual((directory / "attended_leonardo_readbacks.json").read_bytes(), CONTENT)
        self.assertEqual(vs.load_current_json(self.root, "queue.json"), {"rows": []})
        self.assertEqual(vs.read_current_manifest(self.root)["created_at"], "2026-10-08T12:00:00Z")
        self.assertFalse((self.root / vs.INCOMING_DIR).exists() and any((self.root / vs.INCOMING_DIR).iterdir()))

    def test_bundle_from_a_file_path_and_atomic_write(self):
        target = vs.write_bundle(self.tmp / "out" / "b.zip", good_files(), KEY, "desktop", BASE_TIME)
        self.assertTrue(target.is_file())
        self.assertEqual(list(target.parent.glob("*.tmp")), [])
        self.assertEqual(vs.verify_bundle(target, KEY).files, good_files())

    def test_builder_refuses_names_that_are_not_on_the_allow_list(self):
        for bad in ("evil.json", "attended_other.json", "../queue.json", "sub/queue.json", "manifest.json",
                    "attended_ce_only_table_diagnostics.2026-09-24.bak.json"):
            with self.assertRaises(vs.SnapshotError) as caught:
                vs.build_bundle({bad: b"{}"}, KEY, "desktop")
            self.assertEqual(caught.exception.code, "name_not_allowed", bad)

    def test_builder_refuses_oversize_and_empty(self):
        with self.assertRaises(vs.SnapshotError) as caught:
            vs.build_bundle({"queue.json": b"x" * (vs.MAX_FILE_BYTES + 1)}, KEY, "desktop")
        self.assertEqual(caught.exception.code, "file_too_large")
        with self.assertRaises(vs.SnapshotError) as caught:
            vs.build_bundle({}, KEY, "desktop")
        self.assertEqual(caught.exception.code, "empty_bundle")

    def test_the_production_clone_is_not_publishable(self):
        for name in ("redash_prod_clone.json", "inventory_prod_clone_snapshot.json"):
            self.assertNotIn(name, vs.ALLOWED_NAMES)

    def test_key_rules(self):
        short = self.tmp / "short.key"
        short.write_bytes(b"too-short")
        with self.assertRaises(vs.SnapshotError) as caught:
            vs.load_key(short)
        self.assertEqual(caught.exception.code, "key_invalid")
        with self.assertRaises(vs.SnapshotError) as caught:
            vs.load_key(self.tmp / "missing.key")
        self.assertEqual(caught.exception.code, "key_unreadable")
        good = self.tmp / "good.key"
        good.write_bytes(KEY + b"\r\n")
        self.assertEqual(vs.load_key(good), KEY)


class RefusalTests(TempCase):
    def test_bad_signature(self):
        self.assertEqual(self.code(vs.build_bundle(good_files(), KEY, "desktop", BASE_TIME), OTHER_KEY), "bad_signature")

    def test_tampered_manifest_without_resigning(self):
        original = vs.build_bundle(good_files(), KEY, "desktop", BASE_TIME)
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members[vs.MANIFEST_NAME])
        manifest["created_at"] = "2026-10-09T12:00:00Z"
        members[vs.MANIFEST_NAME] = vs.canonical_manifest_bytes(manifest)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        self.assertEqual(self.code(buffer.getvalue()), "bad_signature")

    def test_changed_file_content_is_a_hash_mismatch(self):
        changed = craft({"queue.json": b'{"rows": [1]}'}, listed={"queue.json": b'{"rows": []}'})
        self.assertIn(self.code(changed), ("hash_mismatch", "size_mismatch"))
        same_size = craft({"queue.json": b'{"rows": [1]}'}, listed={"queue.json": b'{"rows": [2]}'})
        self.assertEqual(self.code(same_size), "hash_mismatch")

    def test_extra_file_not_in_the_manifest(self):
        self.assertEqual(self.code(craft(good_files(), extra={"attended_spycloud.json": b"{}"})), "extra_file")

    def test_extra_file_that_is_not_allow_listed(self):
        self.assertEqual(self.code(craft(good_files(), extra={"evil.sh": b"x"})), "extra_file")

    def test_missing_file_listed_in_the_manifest(self):
        files = good_files()
        data = craft({"queue.json": files["queue.json"]}, listed=files)
        self.assertEqual(self.code(data), "missing_file")

    def test_signed_manifest_naming_a_file_that_is_not_allow_listed(self):
        self.assertEqual(self.code(craft({"evil.json": b"{}"})), "name_not_allowed")
        self.assertEqual(self.code(craft(good_files(), extra={"attended_x.json": b"{}"},
                                         listed={**good_files(), "attended_x.json": b"{}"})), "name_not_allowed")

    def test_path_components_and_absolute_names(self):
        for bad in ("sub/queue.json", "../queue.json", "..\\queue.json", "/etc/queue.json", "C:/queue.json",
                    "C:\\queue.json", "queue.json/", "~/queue.json", "a/../queue.json"):
            data = craft({"queue.json": b"{}"}, extra={bad: b"{}"})
            self.assertEqual(self.code(data), "bad_member_name", bad)
        # a bad name inside the signed manifest is refused too
        data = craft({"queue.json": b"{}"}, listed={"../queue.json": b"{}"}, extra={"../queue.json": b"{}"})
        self.assertEqual(self.code(data), "bad_member_name")

    def test_symlink_member(self):
        data = craft(good_files(), attrs={"queue.json": stat.S_IFLNK | 0o777})
        self.assertEqual(self.code(data), "link_member")

    def test_oversize_file_and_oversize_bundle(self):
        big = b"0" * (vs.MAX_FILE_BYTES + 1)
        self.assertEqual(self.code(craft({"queue.json": big})), "file_too_large")
        chunk = b"0" * (vs.MAX_FILE_BYTES - 10)
        files = {name: chunk for name in ("queue.json", "co_details.json", "attended_spycloud.json",
                                          "attended_ce_only_run_log.json", "attended_scan_status.json")}
        self.assertEqual(self.code(craft(files)), "bundle_too_large")

    def test_wrong_schema_version(self):
        for version in (0, 2, "1", None):
            data = craft(good_files(), mutate=lambda manifest, v=version: manifest.__setitem__("schema_version", v))
            self.assertEqual(self.code(data), "schema_version", version)

    def test_duplicate_member_names(self):
        buffer = io.BytesIO()
        manifest_files = good_files()
        manifest = {"schema_version": 1, "created_at": "2026-10-08T12:00:00Z", "producer": "test",
                    "files": [{"name": n, "size": len(d), "sha256": __import__("hashlib").sha256(d).hexdigest()}
                              for n, d in sorted(manifest_files.items())]}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(vs.MANIFEST_NAME, vs.canonical_manifest_bytes(manifest))
                archive.writestr(vs.SIGNATURE_NAME, vs.sign_manifest(manifest, KEY))
                for name, data in manifest_files.items():
                    archive.writestr(name, data)
                archive.writestr("queue.json", b'{"rows": ["second copy"]}')
        self.assertEqual(self.code(buffer.getvalue()), "duplicate_member")

    def test_duplicate_entry_in_the_manifest(self):
        def mutate(manifest):
            manifest["files"].append(dict(manifest["files"][0]))
        self.assertEqual(self.code(craft(good_files(), mutate=mutate)), "duplicate_member")

    def test_not_a_zip_missing_manifest_and_garbage_manifest(self):
        self.assertEqual(self.code(b"this is not a zip"), "bad_zip")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("queue.json", b"{}")
        self.assertEqual(self.code(buffer.getvalue()), "manifest_missing")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(vs.MANIFEST_NAME, b"not json")
            archive.writestr(vs.SIGNATURE_NAME, b"00")
        self.assertEqual(self.code(buffer.getvalue()), "manifest_invalid")

    def test_manifest_with_unexpected_fields_and_bad_values(self):
        self.assertEqual(self.code(craft(good_files(), mutate=lambda m: m.__setitem__("note", "x"))), "manifest_invalid")
        self.assertEqual(self.code(craft(good_files(), mutate=lambda m: m.__setitem__("created_at", "yesterday"))), "bad_created_at")
        self.assertEqual(self.code(craft(good_files(), mutate=lambda m: m["files"][0].__setitem__("sha256", "zz"))), "manifest_invalid")

    def test_inventory_pair_rules(self):
        latest = json.dumps({"file": "leonardo-dev-inventory-20261004T215154Z.json"}).encode()
        self.assertEqual(self.code(craft({vs.INVENTORY_DEV_LATEST: latest})), "inventory_incomplete")
        bad = json.dumps({"file": "../../evil.json"}).encode()
        self.assertEqual(self.code(craft({vs.INVENTORY_DEV_LATEST: bad, vs.INVENTORY_DEV_SNAPSHOT: b"{}"})), "inventory_invalid")

    def test_refusal_text_never_contains_content_or_key(self):
        try:
            vs.verify_bundle(craft(good_files(), extra={"x.json": b"{}"}), OTHER_KEY)
        except vs.SnapshotError as error:
            text = repr(error) + str(error) + error.code
        self.assertNotIn(KEY.decode(), text)
        self.assertNotIn(OTHER_KEY.decode(), text)
        self.assertNotIn("SENTINEL", text)


class StoreTests(TempCase):
    def bundle(self, minutes, **extra):
        return vs.build_bundle(good_files(**extra), KEY, "desktop", BASE_TIME + timedelta(minutes=minutes))

    def test_previous_snapshot_survives_a_bad_one(self):
        first = self.ingest(self.bundle(0))
        before = {path.name: path.read_bytes() for path in (self.root / first).iterdir() if path.is_file()}
        bad_bundles = [
            craft(good_files(), sign_key=OTHER_KEY, created_at=BASE_TIME + timedelta(minutes=5)),
            craft(good_files(), extra={"attended_spycloud.json": b"{}"}, created_at=BASE_TIME + timedelta(minutes=5)),
            b"garbage",
            craft(good_files(), attrs={"queue.json": stat.S_IFLNK | 0o777}, created_at=BASE_TIME + timedelta(minutes=5)),
        ]
        for data in bad_bundles:
            with self.assertRaises(vs.SnapshotError):
                self.ingest(data)
            self.assertEqual(vs.current_name(self.root), first)
            self.assertEqual({p.name: p.read_bytes() for p in (self.root / first).iterdir() if p.is_file()}, before)
            self.assertEqual(vs.list_snapshots(self.root), [first])
            staging = self.root / vs.INCOMING_DIR
            self.assertFalse(staging.exists() and any(staging.iterdir()))

    def test_a_failure_while_switching_keeps_the_previous_snapshot(self):
        first = self.ingest(self.bundle(0))
        with patch.object(vs, "_switch_pointer", side_effect=OSError("disk")):
            with self.assertRaises(vs.SnapshotError) as caught:
                self.ingest(self.bundle(10))
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertEqual(vs.current_name(self.root), first)
        self.assertEqual(vs.list_snapshots(self.root), [first])

    def test_older_bundle_is_refused_and_same_second_gets_a_suffix(self):
        first = self.ingest(self.bundle(30))
        with self.assertRaises(vs.SnapshotError) as caught:
            self.ingest(self.bundle(0))
        self.assertEqual(caught.exception.code, "older_than_current")
        self.assertEqual(vs.current_name(self.root), first)
        second = self.ingest(self.bundle(30))
        self.assertEqual(second, first + "-2")
        self.assertEqual(vs.current_name(self.root), second)

    def test_prune_keeps_the_newest_five(self):
        names = [self.ingest(self.bundle(minutes)) for minutes in range(0, 70, 10)]  # seven snapshots
        kept = vs.list_snapshots(self.root)
        self.assertEqual(kept, names[-5:])
        self.assertEqual(vs.current_name(self.root), names[-1])
        for gone in names[:2]:
            self.assertFalse((self.root / gone).exists())

    def test_prune_never_removes_current(self):
        for minutes in range(0, 60, 10):
            self.ingest(self.bundle(minutes))
        newest = vs.current_name(self.root)
        removed = vs._prune(self.root, 1, protect=newest)
        self.assertNotIn(newest, removed)
        self.assertEqual(len(removed), 4)  # five were kept; with keep=1 the four older ones go, current stays
        self.assertEqual(vs.list_snapshots(self.root), [newest])
        self.assertEqual(vs.current_name(self.root), newest)

    def test_pointer_is_read_through_one_helper_and_rejects_bad_names(self):
        self.assertIsNone(vs.current_dir(self.root))
        self.assertIsNone(vs.read_current_manifest(self.root))
        self.root.mkdir(parents=True)
        (self.root / vs.CURRENT_MARKER).write_text("../outside", encoding="ascii")
        self.assertIsNone(vs.current_name(self.root))
        name = self.ingest(self.bundle(0))
        self.assertEqual(vs.current_dir(self.root), self.root / name)
        if os.name != "nt":
            self.assertTrue((self.root / vs.CURRENT_LINK).is_symlink())
            self.assertEqual(os.readlink(self.root / vs.CURRENT_LINK), name)
        else:
            self.assertEqual((self.root / vs.CURRENT_MARKER).read_text(encoding="ascii"), name)

    def test_load_current_json_refuses_names_off_the_list(self):
        self.ingest(self.bundle(0))
        with self.assertRaises(vs.SnapshotError):
            vs.load_current_json(self.root, "../manifest.json")
        with self.assertRaises(vs.SnapshotError):
            vs.load_current_json(self.root, "co_details.json")  # allowed name, not in this snapshot

    def test_inventory_pair_is_laid_out_for_the_dashboard_reader(self):
        name_in = "leonardo-dev-inventory-20261004T215154Z.json"
        latest = json.dumps({"file": name_in, "environment": "dev"}).encode()
        name = self.ingest(vs.build_bundle(good_files(**{vs.INVENTORY_DEV_LATEST: latest,
                                                         vs.INVENTORY_DEV_SNAPSHOT: b'{"tenants": []}'}),
                                           KEY, "desktop", BASE_TIME))
        folder = self.root / name / vs.INVENTORY_SUBDIR / "dev"
        self.assertEqual((folder / "latest.json").read_bytes(), latest)
        self.assertEqual((folder / name_in).read_bytes(), b'{"tenants": []}')

    def test_snapshot_dir_holds_only_listed_files_plus_manifest_and_inventory_layout(self):
        name = self.ingest(self.bundle(0))
        self.assertEqual(sorted(p.name for p in (self.root / name).iterdir()),
                         sorted([*good_files(), vs.MANIFEST_NAME]))


class ToolTests(TempCase):
    def run_tool(self, main, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue() + err.getvalue()

    def write_key(self, key=KEY):
        path = self.tmp / "snapshot.key"
        path.write_bytes(key)
        return path

    def test_ingest_tool_success_and_failure_print_no_content_or_key(self):
        key_file = self.write_key()
        good = self.tmp / "good.zip"
        good.write_bytes(vs.build_bundle(good_files(), KEY, "desktop", BASE_TIME))
        code, output = self.run_tool(ingest_tool.main, ["--key-file", str(key_file), "--zip", str(good),
                                                         "--state-dir", str(self.state)])
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("ingested snapshot-"))
        bad = self.tmp / "bad.zip"
        bad.write_bytes(craft(good_files(), sign_key=OTHER_KEY, created_at=BASE_TIME + timedelta(minutes=1)))
        code, output = self.run_tool(ingest_tool.main, ["--key-file", str(key_file), "--zip", str(bad),
                                                         "--state-dir", str(self.state)])
        self.assertEqual(code, 2)
        self.assertEqual(output.strip(), "refused:bad_signature")
        code, output = self.run_tool(ingest_tool.main, ["--key-file", str(self.tmp / "nokey"), "--zip", str(good),
                                                         "--state-dir", str(self.state)])
        self.assertEqual((code, output.strip()), (2, "refused:key_unreadable"))
        code, output = self.run_tool(ingest_tool.main, ["--key-file", str(key_file), "--zip", str(self.tmp / "none.zip"),
                                                         "--state-dir", str(self.state)])
        self.assertEqual((code, output.strip()), (2, "refused:bundle_unreadable"))
        for text in (output,):
            self.assertNotIn(KEY.decode(), text)
            self.assertNotIn("SENTINEL", text)

    def test_ingest_tool_requires_an_absolute_state_dir(self):
        code, output = self.run_tool(ingest_tool.main, ["--key-file", str(self.write_key()), "--zip", "x.zip",
                                                         "--state-dir", "relative"])
        self.assertEqual(code, 64)

    def test_ingest_tool_stays_standalone(self):
        source = Path(ingest_tool.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "socket", "urllib", "http", "requests", "tools.serve", "playwright"):
            self.assertNotIn("import " + forbidden, source)
        self.assertNotIn("from tools", source)


class PublisherTests(TempCase):
    def setUp(self):
        super().setUp()
        self.local = self.tmp / "local_state"
        self.local.mkdir()
        (self.local / "attended_leonardo_readbacks.json").write_bytes(CONTENT)
        (self.local / "attended_ce_only_run_log.json").write_bytes(b"[]")
        patcher = patch.dict(os.environ, {"SURFACE_ONBOARDING_STATE_DIR": str(self.local),
                                          "SURFACE_ONBOARDING_RUNTIME": "desktop"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.key_file = self.tmp / "snapshot.key"
        self.key_file.write_bytes(KEY)

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = publish_tool.main(argv)
        return code, out.getvalue()

    def test_state_files_come_only_from_the_allow_list(self):
        found = publish_tool.collect_state_files()
        self.assertEqual(sorted(found), ["attended_ce_only_run_log.json", "attended_leonardo_readbacks.json"])
        with self.assertRaises(publish_tool.PublishRefused) as caught:
            publish_tool.collect_state_files(["attended_leonardo_readbacks.json", "cookies.json"])
        self.assertEqual(caught.exception.code, "name_not_allowed")
        (self.local / "attended_other.json").write_bytes(b"{}")
        self.assertNotIn("attended_other.json", publish_tool.collect_state_files())

    def test_only_refuses_a_name_off_the_list_on_the_command_line(self):
        code, output = self.run_main(["--key-file", str(self.key_file), "--out-dir", str(self.tmp / "out"),
                                      "--only", "cookies.json"])
        self.assertEqual((code, output.strip()), (2, "refused:name_not_allowed"))
        self.assertFalse((self.tmp / "out").exists())

    def test_dry_run_lists_names_and_sizes_and_writes_nothing(self):
        code, output = self.run_main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("include attended_leonardo_readbacks.json " + str(len(CONTENT)) + " bytes", output)
        self.assertIn("include queue.json", output)
        self.assertIn("nothing written", output)
        self.assertNotIn("SENTINEL", output)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.suffix == ".zip"), [])

    def test_publish_only_state_files_writes_a_verifiable_zip_and_prints_counts_only(self):
        out_dir = self.tmp / "out"
        code, output = self.run_main(["--key-file", str(self.key_file), "--out-dir", str(out_dir),
                                      "--only", "attended_leonardo_readbacks.json"])
        self.assertEqual(code, 0)
        written = next(out_dir.glob("surface-vm-snapshot-*.zip"))
        self.assertIn("written " + str(written), output)
        self.assertIn("state_files=1", output)
        self.assertNotIn("SENTINEL", output)
        self.assertNotIn(KEY.decode(), output)
        self.assertEqual(vs.verify_bundle(written, KEY).files, {"attended_leonardo_readbacks.json": CONTENT})

    def test_full_publish_uses_the_dashboards_existing_fixed_reads(self):
        import tools.serve_attended_open_onboardings_dashboard as dash

        queue = [{**{field: "x" for field in dash.QUEUE_FIELDS}, "Name": "CO-0001", "Local_Leonardo_State": "no"}]
        detail = {field: "v" for field in dash.DETAIL_FIELDS}

        def fake_detail(reference):
            if reference == "CO-0002":
                raise dash.ReadUnavailable()
            return {**detail, "Name": reference, "Extra_Field__c": "must not be published"}

        with patch.object(dash, "queue_source_rows", return_value=queue), \
                patch.object(dash, "detail_row", side_effect=fake_detail), \
                patch.object(dash, "dev_onboarded_refs", return_value=["CO-0002", "CO-0003"]):
            code, output = self.run_main(["--key-file", str(self.key_file), "--out-dir", str(self.tmp / "out")])
        self.assertEqual(code, 0)
        self.assertIn("queue_rows=1", output)
        self.assertIn("co_details=2", output)
        self.assertIn("co_details_unavailable=1", output)
        written = next((self.tmp / "out").glob("*.zip"))
        files = vs.verify_bundle(written, KEY).files
        details = json.loads(files["co_details.json"])["details"]
        self.assertEqual(sorted(details), ["CO-0001", "CO-0003"])
        self.assertEqual(set(details["CO-0001"]), set(dash.DETAIL_FIELDS))  # exactly the fixed fields
        self.assertEqual(set(json.loads(files["queue.json"])["rows"][0]), set(dash.QUEUE_FIELDS))
        self.assertNotIn(b"must not be published", files["co_details.json"])

    def test_queue_unavailable_refuses_the_whole_publish(self):
        import tools.serve_attended_open_onboardings_dashboard as dash

        with patch.object(dash, "queue_source_rows", side_effect=dash.ReadUnavailable()):
            code, output = self.run_main(["--key-file", str(self.key_file), "--out-dir", str(self.tmp / "out")])
        self.assertEqual((code, output.strip()), (3, "refused:queue_unavailable"))
        self.assertFalse((self.tmp / "out").exists())

    def test_missing_arguments_and_bad_key(self):
        self.assertEqual(self.run_main([])[0], 64)
        short = self.tmp / "short.key"
        short.write_bytes(b"short")
        code, output = self.run_main(["--key-file", str(short), "--out-dir", str(self.tmp / "o"), "--only",
                                      "attended_ce_only_run_log.json"])
        self.assertEqual((code, output.strip()), (2, "refused:key_invalid"))

    def test_autopublish_is_off_by_default_and_best_effort(self):
        out_dir = self.tmp / "auto"
        env = {"SURFACE_VM_SNAPSHOT_KEY_FILE": str(self.key_file), "SURFACE_VM_SNAPSHOT_OUT_DIR": str(out_dir)}
        with patch.dict(os.environ, env):
            os.environ.pop("SURFACE_VM_SNAPSHOT_AUTOPUBLISH", None)
            with patch.object(publish_tool, "publish", side_effect=AssertionError("must not run")):
                publish_tool.autopublish_after_run()  # off: nothing happens
        with patch.dict(os.environ, {**env, "SURFACE_VM_SNAPSHOT_AUTOPUBLISH": "1"}):
            with patch.object(publish_tool, "publish", side_effect=RuntimeError("boom")):
                publish_tool.autopublish_after_run()  # a failure never escapes
            with patch.object(publish_tool, "publish") as called:
                publish_tool.autopublish_after_run()
            called.assert_called_once()
            self.assertEqual(called.call_args.kwargs, {"autopublish": True})
        self.assertFalse(out_dir.exists())

    def test_autopublish_writes_one_fixed_name_into_the_configured_folder_only(self):
        self.assertEqual(publish_tool.output_path(Path("out"), True, BASE_TIME).name, publish_tool.AUTOPUBLISH_NAME)
        self.assertEqual(publish_tool.output_path(Path("out"), False, BASE_TIME).name, "surface-vm-snapshot-20261008T120000Z.zip")

    def test_runner_hook_is_off_by_default_and_never_changes_the_run(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SURFACE_VM_SNAPSHOT_AUTOPUBLISH", None)
            with patch.object(runner.subprocess, "Popen", side_effect=AssertionError("must not start")):
                runner._autopublish_vm_snapshot()
        with patch.dict(os.environ, {"SURFACE_VM_SNAPSHOT_AUTOPUBLISH": "1"}):
            with patch.object(runner.subprocess, "Popen", side_effect=OSError("no")):
                runner._autopublish_vm_snapshot()  # swallowed
            with patch.object(runner.subprocess, "Popen") as popen:
                runner._autopublish_vm_snapshot()
            argv = popen.call_args.args[0]
            self.assertEqual(Path(argv[1]).name, "publish_vm_snapshot.py")
            self.assertEqual(argv[2], "--autopublish")


if __name__ == "__main__":
    unittest.main()
