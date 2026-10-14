"""VM refresher (docs/43 part A): merges fresh queue/details into a new signed snapshot, keeps everything else, fails
closed. Hermetic: temporary state dir, fake Salesforce reads, no network, no process, no real key."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import tools.publish_vm_snapshot as publisher
import tools.serve_attended_open_onboardings_dashboard as dashboard
import tools.vm_refresh_snapshot as refresher
from integration.onboarding import vm_snapshot as vs
from tools import vm_salesforce_reader as vr

KEY = b"unit-test-snapshot-key-0123456789-abcdef-NOT-REAL"
STATE_FILE = "attended_ce_only_run_log.json"
CONFIG = {"login_url": "https://login.salesforce.com", "client_id": "3MVG9" + "x" * 30, "username": "svc@pentera.example",
          "private_key_file": "/etc/surface-onboarding/sf-reader.pem", "api_version": "v60.0"}
FIRST = datetime(2026, 10, 13, 8, 0, 0, tzinfo=timezone.utc)
SECOND = datetime(2026, 10, 14, 8, 0, 0, tzinfo=timezone.utc)


def detail(reference, revision):
    return {"Name": reference, "LastModifiedDate": revision}


class RefreshCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = str(self.root / "state")
        self.key = self.root / "snapshot.key"
        self.key.write_bytes(KEY)
        self.config = self.root / "sf-reader.json"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")
        files = {vs.QUEUE_FILE: json.dumps({"rows": [{"Name": "CO-0001"}, {"Name": "CO-0002"}]}).encode(),
                 vs.CO_DETAILS_FILE: json.dumps({"details": {"CO-0001": detail("CO-0001", "old"),
                                                             "CO-0002": detail("CO-0002", "old")}}).encode(),
                 STATE_FILE: b'{"CO-0001": "run history from the desktop"}'}
        self.first = vs.ingest_bundle(vs.build_bundle(files, KEY, "desktop", FIRST), KEY, self.state)
        self.reads_calls = []
        self.seen_sf_json = []

    def fake_reads(self, queue=("CO-0001", "CO-0003"), details=None, raises=None):
        details = details if details is not None else {"CO-0001": detail("CO-0001", "new"), "CO-0003": detail("CO-0003", "new")}

        def collect(extra_references=()):
            self.reads_calls.append(sorted(extra_references))
            self.seen_sf_json.append(dashboard.sf_json)
            if raises is not None:
                raise raises
            return ({vs.QUEUE_FILE: json.dumps({"rows": [{"Name": n} for n in queue]}).encode(),
                     vs.CO_DETAILS_FILE: json.dumps({"details": details}).encode()},
                    {"queue_rows": len(queue), "co_details": len(details), "co_details_unavailable": 0})
        return patch.object(publisher, "collect_dashboard_reads", collect)

    @contextlib.contextmanager
    def environment(self):
        env = {k: v for k, v in os.environ.items() if k != "SURFACE_ONBOARDING_RUNTIME"}
        with patch.dict(os.environ, env, clear=True), patch.object(vr, "_private_file", lambda *a, **k: None):
            yield

    def run_refresh(self, **reads):
        with self.environment(), self.fake_reads(**reads):
            return refresher.refresh(str(self.config), str(self.key), self.state, now=SECOND)

    def current(self):
        root = vs.snapshots_root(self.state)
        return root, vs.read_current_manifest(root)


class RefreshTests(RefreshCase):
    def test_new_queue_and_details_are_published_and_everything_else_is_carried_over(self):
        counts = self.run_refresh()
        root, manifest = self.current()
        self.assertEqual(manifest["producer"], vs.PRODUCER_VM_READER)
        self.assertEqual(vs.load_current_json(root, vs.QUEUE_FILE), {"rows": [{"Name": "CO-0001"}, {"Name": "CO-0003"}]})
        details = vs.load_current_json(root, vs.CO_DETAILS_FILE)["details"]
        self.assertEqual(details["CO-0001"]["LastModifiedDate"], "new")
        self.assertEqual(details["CO-0003"]["LastModifiedDate"], "new")
        self.assertEqual(details["CO-0002"]["LastModifiedDate"], "old")  # no longer queued: keeps its page
        self.assertEqual(vs.load_current_json(root, STATE_FILE), {"CO-0001": "run history from the desktop"})
        self.assertEqual(counts["co_details_kept_from_previous"], 1)
        self.assertEqual(self.reads_calls, [["CO-0001", "CO-0002"]])
        self.assertNotEqual(vs.current_name(root), self.first)

    def test_a_failed_detail_read_keeps_the_previous_detail(self):
        self.run_refresh(queue=("CO-0001", "CO-0002"), details={"CO-0001": detail("CO-0001", "new")})
        root, _ = self.current()
        details = vs.load_current_json(root, vs.CO_DETAILS_FILE)["details"]
        self.assertEqual(details["CO-0002"]["LastModifiedDate"], "old")
        self.assertEqual(details["CO-0001"]["LastModifiedDate"], "new")

    def test_a_salesforce_failure_changes_nothing(self):
        before = vs.current_name(vs.snapshots_root(self.state))
        with self.assertRaises(refresher.RefreshRefused) as caught:
            self.run_refresh(raises=publisher.PublishRefused("queue_unavailable", 3))
        self.assertEqual((caught.exception.code, caught.exception.exit_code), ("queue_unavailable", 3))
        self.assertEqual(vs.current_name(vs.snapshots_root(self.state)), before)

    def test_dashboard_sf_json_is_swapped_only_while_reading_and_always_restored(self):
        original = dashboard.sf_json
        self.run_refresh()
        self.assertIsNot(self.seen_sf_json[0], original)
        self.assertIs(dashboard.sf_json, original)
        with self.assertRaises(refresher.RefreshRefused):
            self.run_refresh(raises=publisher.PublishRefused("queue_unavailable", 3))
        self.assertIs(dashboard.sf_json, original)

    def test_the_swapped_reader_answers_only_the_cli_call_shape(self):
        class Boom(Exception):
            pass
        with self.environment():
            reader_client = vr.RestQueryClient(vr.ReaderConfig(**CONFIG), object(), lambda *a: b"s")
            sf_json = vr.make_sf_json(reader_client, dashboard.ReadUnavailable)
            with self.assertRaises(dashboard.ReadUnavailable):
                sf_json(["data", "update", "record"])

    def test_vm_runtime_is_refused_because_it_would_serve_snapshots_not_read_salesforce(self):
        with self.environment(), patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}), self.fake_reads():
            with self.assertRaises(refresher.RefreshRefused) as caught:
                refresher.refresh(str(self.config), str(self.key), self.state, now=SECOND)
        self.assertEqual(caught.exception.code, "runtime_must_not_be_vm")
        self.assertEqual(self.reads_calls, [])

    def test_bad_key_or_config_is_refused_before_any_read(self):
        short = self.root / "short.key"
        short.write_bytes(b"short")
        with self.environment(), self.fake_reads():
            with self.assertRaises(refresher.RefreshRefused):
                refresher.refresh(str(self.config), str(short), self.state, now=SECOND)
            self.config.write_text("{}", encoding="utf-8")
            with self.assertRaises(refresher.RefreshRefused) as caught:
                refresher.refresh(str(self.config), str(self.key), self.state, now=SECOND)
        self.assertEqual(caught.exception.code, "config_invalid")
        self.assertEqual(self.reads_calls, [])

    def test_an_older_clock_cannot_roll_the_snapshot_back(self):
        with self.environment(), self.fake_reads():
            with self.assertRaises(refresher.RefreshRefused) as caught:
                refresher.refresh(str(self.config), str(self.key), self.state, now=FIRST - timedelta(hours=1))
        self.assertEqual(caught.exception.code, "older_than_current")

    def test_first_refresh_without_any_snapshot_works(self):
        fresh = str(self.root / "fresh-state")
        with self.environment(), self.fake_reads():
            refresher.refresh(str(self.config), str(self.key), fresh, now=SECOND)
        root = vs.snapshots_root(fresh)
        self.assertEqual(vs.read_current_manifest(root)["producer"], vs.PRODUCER_VM_READER)
        self.assertEqual(vs.load_current_json(root, vs.QUEUE_FILE)["rows"][0]["Name"], "CO-0001")


class RealDashboardReadsThroughTheAdapterTests(unittest.TestCase):
    """The dashboard's real queue and detail reads, answered by the REST adapter over a fake Salesforce.

    Guards against the adapter supporting a different call shape than the dashboard actually makes (the queue is a list view
    results GET, not a SOQL query)."""

    def test_queue_and_details_come_through_the_adapter_in_the_dashboards_own_shapes(self):
        from integration.tests.test_vm_salesforce_reader import FakeOpener, login

        columns = [{"fieldNameOrPath": field, "value": ("CO-0780" if field == "Name" else "x")}
                   for field in dashboard.QUEUE_FIELDS]
        detail_record = {field: "x" for field in dashboard.DETAIL_FIELDS}
        detail_record["Name"] = "CO-0780"
        opener = FakeOpener({
            "/services/oauth2/token": [login()],
            "FROM+ListView": [{"done": True, "records": [{"Id": "00BAA000000abcd"}]}],
            "/listviews/": [{"size": 1, "records": [{"columns": columns}]}],
            "CO-0780": [{"done": True, "records": [detail_record]}],
        })
        config = vr.ReaderConfig(**CONFIG)
        client = vr.RestQueryClient(config, opener, lambda key_file, message: b"sig", clock=lambda: SECOND)
        original, cached = dashboard.sf_json, dashboard._open_onboardings_view_id
        dashboard._open_onboardings_view_id = None
        dashboard.sf_json = vr.make_sf_json(client, dashboard.ReadUnavailable)
        try:
            env = {k: v for k, v in os.environ.items() if k != "SURFACE_ONBOARDING_RUNTIME"}
            with patch.dict(os.environ, env, clear=True):
                files, counts = publisher.collect_dashboard_reads()
        finally:
            dashboard.sf_json = original
            dashboard._open_onboardings_view_id = cached
        self.assertEqual((counts["queue_rows"], counts["co_details"], counts["co_details_unavailable"]), (1, 1, 0))
        self.assertEqual(json.loads(files[vs.QUEUE_FILE])["rows"][0]["Name"], "CO-0780")
        self.assertEqual(json.loads(files[vs.CO_DETAILS_FILE])["details"]["CO-0780"]["Name"], "CO-0780")
        methods = [(m, u.split("salesforce.com")[1].split("?")[0]) for m, u, *_ in opener.requests[1:]]
        self.assertEqual(methods[1], ("GET", "/services/data/v67.0/sobjects/Customer_Onboarding__c/listviews/00BAA000000abcd/results"))
        self.assertTrue(all(method == "GET" for method, _ in methods), "every Salesforce call is a read")


class CommandLineTests(RefreshCase):
    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = refresher.main(list(argv))
        return code, out.getvalue()

    def test_usage_and_dry_run_and_output_is_counts_and_codes_only(self):
        self.assertEqual(self.main()[0], 64)
        self.assertEqual(self.main("--config", str(self.config), "--key-file", str(self.key), "--state-dir", "relative")[0], 64)
        key_pem = self.root / "sf-reader.pem"
        key_pem.write_text("pem", encoding="ascii")
        data = {**CONFIG, "private_key_file": str(key_pem)}
        self.config.write_text(json.dumps(data), encoding="utf-8")
        if os.name == "posix":
            os.chmod(self.config, 0o640)
            os.chmod(key_pem, 0o600)
            code, text = self.main("--config", str(self.config), "--key-file", str(self.key), "--state-dir", self.state, "--dry-run")
            self.assertEqual(code, 0)
            self.assertIn("nothing was read or written", text)
        with self.environment(), self.fake_reads():
            code, text = self.main("--config", str(self.config), "--key-file", str(self.key), "--state-dir", self.state)
        if os.name == "posix":
            self.assertEqual(code, 0)
            self.assertTrue(text.strip().endswith("refreshed"))
            self.assertNotIn(KEY.decode(), text)
            self.assertNotIn("sf-reader", text)

    @unittest.skipUnless(os.name == "posix", "flock")
    def test_a_second_refresh_while_one_is_running_exits_75(self):
        handle = refresher._lock(self.state)
        self.addCleanup(handle.close)
        code, text = self.main("--config", str(self.config), "--key-file", str(self.key), "--state-dir", self.state)
        self.assertEqual((code, text.strip()), (75, "refused:refresh_already_running"))

    def test_failure_prints_a_reason_code_and_never_a_traceback(self):
        with self.environment(), patch.object(refresher, "refresh", side_effect=RuntimeError("secret path /etc/x")), \
                patch.object(refresher, "_lock", lambda state: io.StringIO()):
            code, text = self.main("--config", str(self.config), "--key-file", str(self.key), "--state-dir", self.state)
        self.assertEqual((code, text.strip()), (2, "refused:unexpected_error"))
        self.assertNotIn("secret", text)


if __name__ == "__main__":
    unittest.main()
