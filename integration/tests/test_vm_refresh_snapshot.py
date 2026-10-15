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
            with patch.dict(os.environ, env, clear=True),                     patch.object(dashboard, "production_review_inputs", side_effect=RuntimeError("no live read in tests")):
                files, counts = publisher.collect_dashboard_reads()
        finally:
            dashboard.sf_json = original
            dashboard._open_onboardings_view_id = cached
        self.assertEqual((counts["queue_rows"], counts["co_details"], counts["co_details_unavailable"]), (1, 1, 0))
        self.assertEqual((counts["production_inputs"], counts["production_inputs_unavailable"]), (0, 1))
        self.assertEqual(json.loads(files[vs.PRODUCTION_INPUTS_FILE]), {"inputs": {}})
        self.assertEqual(json.loads(files[vs.QUEUE_FILE])["rows"][0]["Name"], "CO-0780")
        self.assertEqual(json.loads(files[vs.CO_DETAILS_FILE])["details"]["CO-0780"]["Name"], "CO-0780")
        methods = [(m, u.split("salesforce.com")[1].split("?")[0]) for m, u, *_ in opener.requests[1:]]
        self.assertEqual(methods[1], ("GET", "/services/data/v67.0/sobjects/Customer_Onboarding__c/listviews/00BAA000000abcd/results"))
        self.assertTrue(all(method == "GET" for method, _ in methods), "every Salesforce call is a read")


class ProductionInputsTests(RefreshCase):
    """The production-check inputs travel with every refresh; a failed read keeps the previous entry (docs/43 s.9)."""

    ITEM = {"engine": "case_2_ce_only", "co_source": {"tenant_names": ["A"], "domains": ["a.example"]}, "plan": {}}

    def publish_previous(self, inputs):
        files = {vs.QUEUE_FILE: json.dumps({"rows": [{"Name": "CO-0001"}, {"Name": "CO-0002"}]}).encode(),
                 vs.CO_DETAILS_FILE: json.dumps({"details": {"CO-0001": detail("CO-0001", "old"),
                                                             "CO-0002": detail("CO-0002", "old")}}).encode(),
                 vs.PRODUCTION_INPUTS_FILE: json.dumps({"inputs": inputs}).encode()}
        vs.ingest_bundle(vs.build_bundle(files, KEY, "desktop", FIRST + timedelta(hours=1)), KEY, self.state)

    def reads_with_inputs(self, fresh):
        def collect(extra_references=()):
            return ({vs.QUEUE_FILE: json.dumps({"rows": [{"Name": "CO-0001"}, {"Name": "CO-0003"}]}).encode(),
                     vs.CO_DETAILS_FILE: json.dumps({"details": {"CO-0001": detail("CO-0001", "new"),
                                                                 "CO-0003": detail("CO-0003", "new")}}).encode(),
                     vs.PRODUCTION_INPUTS_FILE: json.dumps({"inputs": fresh}).encode()},
                    {"queue_rows": 2, "co_details": 2, "co_details_unavailable": 0})
        return patch.object(publisher, "collect_dashboard_reads", collect)

    def published_inputs(self):
        root, _manifest = self.current()
        return vs.load_current_json(root, vs.PRODUCTION_INPUTS_FILE)["inputs"]

    def test_fresh_inputs_win_and_a_failed_read_keeps_the_previous_entry(self):
        self.publish_previous({"CO-0001": {**self.ITEM, "engine": "old"}, "CO-0002": self.ITEM})
        with self.environment(), self.reads_with_inputs({"CO-0001": self.ITEM}):
            counts = refresher.refresh(str(self.config), str(self.key), self.state, now=SECOND)
        inputs = self.published_inputs()
        self.assertEqual(inputs["CO-0001"]["engine"], "case_2_ce_only")        # fresh, not the old value
        self.assertIn("CO-0002", inputs)                                      # kept: its CO page is kept too
        self.assertNotIn("CO-0003", inputs)                                   # never had one: unavailable, not invented
        self.assertEqual(counts["production_inputs"], 2)

    def test_the_publisher_collects_the_inputs_of_every_queued_co_as_plain_json(self):
        from datetime import date

        row = {field: None for field in dashboard.QUEUE_FIELDS}
        row.update(Name="CO-0001", Onboarding_Product__c="Credential Exposure", Onboarding_Type__c="New Product Onboarding")

        def inputs(reference, product, kind):
            if reference == "CO-0002":
                raise RuntimeError("unreadable")
            return {"engine": "case_2_ce_only", "co_source": {"tenant_names": ("N",), "domains": ("d.example",)},
                    "plan": {"license_end": date(2027, 1, 2), "alternate_domains": ()}}

        second = {**row, "Name": "CO-0002"}
        with patch.object(dashboard, "queue_source_rows", return_value=[row, second]),                 patch.object(dashboard, "detail_row", return_value={"Name": "x"}),                 patch.object(dashboard, "dev_onboarded_refs", return_value=[]),                 patch.object(dashboard, "production_review_inputs", side_effect=inputs):
            files, counts = publisher.collect_dashboard_reads()
        stored = json.loads(files[vs.PRODUCTION_INPUTS_FILE])["inputs"]
        self.assertEqual(sorted(stored), ["CO-0001"])                          # the unreadable CO has no entry
        self.assertEqual(stored["CO-0001"]["co_source"], {"tenant_names": ["N"], "domains": ["d.example"]})
        self.assertEqual(stored["CO-0001"]["plan"]["license_end"], "2027-01-02")
        self.assertEqual((stored["CO-0001"]["product"], stored["CO-0001"]["type"]),
                         ("Credential Exposure", "New Product Onboarding"))
        self.assertEqual((counts["production_inputs"], counts["production_inputs_unavailable"]), (1, 1))

    def test_the_file_is_never_carried_as_is_from_the_previous_snapshot(self):
        self.assertIn(vs.PRODUCTION_INPUTS_FILE, refresher.CARRY_EXCLUDED)


class SessionDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        patcher = patch.object(refresher, "MEMORY_ROOTS", (self.root + os.sep,))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, name="sf", auth=True, mode=0o700):
        folder = os.path.join(self.root, name)
        os.makedirs(folder, exist_ok=True)
        if os.name == "posix":
            os.chmod(folder, mode)
        if auth:
            os.makedirs(os.path.join(folder, ".sfdx"), exist_ok=True)
            Path(folder, ".sfdx", "someone@example.com.json").write_text("{}", encoding="utf-8")
        return folder

    def test_only_a_memory_folder_is_accepted(self):
        with patch.object(refresher, "MEMORY_ROOTS", ("/run/", "/dev/shm/")):
            for path in (self.make(), os.path.join(self.root, "sf"), "/var/lib/surface-onboarding/sf", "/home/x/.sf", "/tmp/sf"):
                with self.subTest(path=path[-20:]):
                    self.assertEqual(refresher.session_dir_problem(path), "session_dir_not_in_memory")
        self.assertEqual(refresher.session_dir_problem("relative/dir"), "session_dir_not_absolute")
        self.assertEqual(refresher.session_dir_problem(""), "session_dir_not_absolute")

    def test_no_folder_or_no_login_means_session_needed(self):
        self.assertEqual(refresher.session_dir_problem(os.path.join(self.root, "missing")), "session_needed")
        folder = self.make("nologin", auth=False)
        if os.name == "posix":
            self.assertEqual(refresher.session_dir_problem(folder), "session_needed")

    @unittest.skipUnless(os.name == "posix", "POSIX modes and ownership")
    def test_folders_the_cli_creates_without_a_login_are_not_a_session(self):
        folder = self.make("fresh", auth=False)
        for name in (".sf", ".cache", ".sfdx"):
            os.makedirs(os.path.join(folder, name), exist_ok=True)
        Path(folder, ".sf", "config.json").write_text("{}", encoding="utf-8")
        Path(folder, ".sfdx", "key.json").write_text("{}", encoding="utf-8")  # the CLI's own key file, not a login
        self.assertEqual(refresher.session_dir_problem(folder), "session_needed")
        Path(folder, ".sfdx", "milton@example.com.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(refresher.session_dir_problem(folder))

    @unittest.skipUnless(os.name == "posix", "POSIX modes and ownership")
    def test_a_private_folder_with_a_login_is_accepted_and_a_loose_one_is_not(self):
        self.assertIsNone(refresher.session_dir_problem(self.make("ok", mode=0o700)))
        for mode in (0o750, 0o755, 0o770, 0o777):
            with self.subTest(mode=oct(mode)):
                self.assertEqual(refresher.session_dir_problem(self.make("loose", mode=mode)), "session_dir_unsafe")

    @unittest.skipUnless(os.name == "posix", "symlinks")
    def test_a_link_is_refused(self):
        target = self.make("real")
        link = os.path.join(self.root, "link")
        os.symlink(target, link)
        self.assertEqual(refresher.session_dir_problem(link), "session_dir_not_in_memory")


class StatusTests(unittest.TestCase):
    def test_result_codes(self):
        self.assertEqual(refresher.status_result(None, "cli"), "ok")
        self.assertEqual(refresher.status_result("session_needed", "cli"), "session_needed")
        self.assertEqual(refresher.status_result("queue_unavailable", "cli"), "session_needed")
        self.assertEqual(refresher.status_result("queue_unavailable", "rest"), "error")
        self.assertEqual(refresher.status_result("bad_zip", "cli"), "error")

    def test_the_status_file_has_only_time_result_and_transport_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            refresher.write_status(tmp, "session_needed", "cli", SECOND)
            self.assertEqual(json.loads((Path(tmp) / refresher.STATUS_NAME).read_text(encoding="utf-8")),
                             {"at": "2026-10-14T08:00:00Z", "result": "session_needed", "transport": "cli"})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [refresher.STATUS_NAME])
        refresher.write_status("/definitely/not/a/folder", "ok", "cli")  # best effort: never raises


class CliTransportTests(RefreshCase):
    def run_cli(self, **reads):
        seen = {}

        def collect(extra_references=()):
            seen["home"], seen["keychain"] = os.environ.get("HOME"), os.environ.get("SF_USE_GENERIC_UNIX_KEYCHAIN")
            seen["sf_json"] = dashboard.sf_json
            return ({vs.QUEUE_FILE: b'{"rows": [{"Name": "CO-0001"}]}',
                     vs.CO_DETAILS_FILE: json.dumps({"details": {"CO-0001": detail("CO-0001", "new")}}).encode()},
                    {"queue_rows": 1, "co_details": 1, "co_details_unavailable": 0})

        session = str(self.root / "session")
        with self.environment(), patch.object(publisher, "collect_dashboard_reads", reads.get("collect", collect)), \
                patch.object(refresher, "session_dir_problem", return_value=reads.get("problem")):
            os.environ["HOME"] = "original-home"
            counts = refresher.refresh("", str(self.key), self.state, now=SECOND, transport="cli", session_dir=session)
            seen["home_after"] = os.environ.get("HOME")
        return counts, seen, os.path.realpath(session)

    def test_the_dashboards_own_cli_reads_run_with_the_whole_cli_home_in_memory_and_the_environment_is_restored(self):
        original = dashboard.sf_json
        counts, seen, session = self.run_cli()
        self.assertEqual(seen["home"], session)
        self.assertEqual(seen["keychain"], "true")
        self.assertIs(seen["sf_json"], original, "cli mode uses the dashboard's real CLI reader, not the REST adapter")
        self.assertEqual(seen["home_after"], "original-home")
        root = vs.snapshots_root(self.state)
        self.assertEqual(vs.read_current_manifest(root)["producer"], vs.PRODUCER_VM_READER)
        self.assertEqual(counts["files_in_bundle"], 4)  # + production_inputs.json

    def test_environment_is_restored_even_when_the_read_fails(self):
        def failing(extra_references=()):
            raise publisher.PublishRefused("queue_unavailable", 3)
        with self.environment(), patch.object(publisher, "collect_dashboard_reads", failing), \
                patch.object(refresher, "session_dir_problem", return_value=None):
            os.environ.pop("SF_USE_GENERIC_UNIX_KEYCHAIN", None)
            os.environ["HOME"] = "original-home"
            with self.assertRaises(refresher.RefreshRefused) as caught:
                refresher.refresh("", str(self.key), self.state, now=SECOND, transport="cli", session_dir=str(self.root / "s"))
            self.assertEqual(caught.exception.code, "queue_unavailable")
            self.assertNotIn("SF_USE_GENERIC_UNIX_KEYCHAIN", os.environ)
            self.assertEqual(os.environ["HOME"], "original-home")

    def test_no_session_is_a_clean_session_needed_refusal_before_any_read(self):
        with self.assertRaises(refresher.RefreshRefused) as caught:
            self.run_cli(problem="session_needed", collect=lambda *a: self.fail("no read without a session"))
        self.assertEqual((caught.exception.code, caught.exception.exit_code), ("session_needed", 3))
        with self.assertRaises(refresher.RefreshRefused) as caught:
            self.run_cli(problem="session_dir_not_in_memory", collect=lambda *a: self.fail("no read"))
        self.assertEqual((caught.exception.code, caught.exception.exit_code), ("session_dir_not_in_memory", 2))

    def test_an_unknown_transport_is_refused(self):
        with self.assertRaises(refresher.RefreshRefused) as caught:
            refresher.refresh("", str(self.key), self.state, transport="smtp")
        self.assertEqual(caught.exception.code, "unknown_transport")

    def test_main_records_the_status_for_the_banner(self):
        out = io.StringIO()
        base = ["--transport", "cli", "--session-dir", str(self.root / "s"), "--key-file", str(self.key), "--state-dir", self.state]
        with self.environment(), patch.object(refresher, "session_dir_problem", return_value="session_needed"), \
                patch.object(refresher, "_lock", lambda state: io.StringIO()), contextlib.redirect_stdout(out):
            code = refresher.main(base)
        self.assertEqual((code, out.getvalue().strip()), (3, "refused:session_needed"))
        status = json.loads((Path(self.state) / refresher.STATUS_NAME).read_text(encoding="utf-8"))
        self.assertEqual((status["result"], status["transport"]), ("session_needed", "cli"))
        out = io.StringIO()
        with self.environment(), self.fake_reads(), patch.object(refresher, "session_dir_problem", return_value=None), \
                patch.object(refresher, "_lock", lambda state: io.StringIO()), contextlib.redirect_stdout(out):
            code = refresher.main(base)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((Path(self.state) / refresher.STATUS_NAME).read_text(encoding="utf-8"))["result"], "ok")

    def test_main_usage_for_cli_needs_the_session_dir(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = refresher.main(["--transport", "cli", "--key-file", str(self.key), "--state-dir", self.state])
        self.assertEqual(code, 64)


class BannerStatusTests(unittest.TestCase):
    def banner(self, status, producer=vs.PRODUCER_VM_READER):
        manifest = {"created_at": "2026-10-14T08:00:00Z", "producer": producer, "files": []}
        with patch.object(dashboard, "snapshot_root", return_value=Path("x")), \
                patch.object(vs, "read_current_manifest", return_value=manifest), \
                patch.object(dashboard, "refresh_status", return_value=status):
            return dashboard.snapshot_banner(datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc))

    def test_a_newer_failed_attempt_says_so_and_turns_amber(self):
        text = self.banner({"at": "2026-10-14T08:20:00Z", "result": "session_needed", "transport": "cli"})
        self.assertIn("Salesforce session needed", text)
        self.assertIn("08:20 UTC", text)
        self.assertIn("snapbanner stale", text)
        self.assertIn("Last refresh attempt failed", self.banner({"at": "2026-10-14T08:20:00Z", "result": "error", "transport": "cli"}))

    def test_ok_old_or_missing_status_adds_nothing(self):
        for status in ({"at": "2026-10-14T08:20:00Z", "result": "ok", "transport": "cli"},
                       {"at": "2026-10-14T07:00:00Z", "result": "session_needed", "transport": "cli"}, None):
            text = self.banner(status)
            self.assertNotIn("session needed", text)
            self.assertNotIn("attempt failed", text)
            self.assertNotIn("snapbanner stale", text)

    def test_status_file_reading_is_strict(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(dashboard.state_paths, "state_dir", return_value=Path(tmp)):
            path = Path(tmp) / dashboard.REFRESH_STATUS_NAME
            good = {"at": "2026-10-14T08:20:00Z", "result": "ok", "transport": "rest"}
            path.write_text(json.dumps(good), encoding="utf-8")
            self.assertEqual(dashboard.refresh_status(), good)
            for bad in ("not json", "[]", json.dumps({**good, "result": "fine"}), json.dumps({**good, "at": "yesterday"}),
                        json.dumps({**good, "extra": "x"}), json.dumps({**good, "transport": "ssh"})):
                path.write_text(bad, encoding="utf-8")
                self.assertIsNone(dashboard.refresh_status(), bad)
            path.unlink()
            self.assertIsNone(dashboard.refresh_status())
        with patch.object(dashboard.state_paths, "state_dir", return_value=None):
            self.assertIsNone(dashboard.refresh_status())


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
