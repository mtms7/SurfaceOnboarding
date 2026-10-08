"""VM data source (docs/41): in VM mode every page reads the published snapshot, makes no process or network call,
shows the "Data as of" banner (amber when stale, an empty state when nothing was published) and offers no action
forms. Desktop mode is unchanged. Hermetic: temporary folders only; module state paths are re-pointed per test."""

import json
import os
import re
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import state_paths, vm_snapshot as vs
from integration.tests.test_vm_proxy_mode import OWNER, PUBLIC, PUBLIC_HOST, SECRET, SECRET_HEADER, VIEWER, ServerCase

KEY = b"unit-test-snapshot-key-0123456789-abcdef-NOT-REAL"
IDENTITY = {dashboard.PROXY_IDENTITY_HEADER: VIEWER}
RUNNER_NAMES = ("RUNNER_STATE_PATH", "CHECK_STATE_PATH", "READBACK_PATH", "SCAN_STATUS_PATH", "VALIDATION_PATH",
                "DIAGNOSTICS_PATH", "RUN_LOG_PATH", "PROGRESS_PATH", "SPYCLOUD_STATE_PATH", "RENEWAL_OUTCOMES_PATH",
                "MIRROR_PATH")
DASHBOARD_NAMES = ("ATTENDED_LEONARDO_READBACK_PATH", "ATTENDED_REMINDERS_PATH", "USER_CREATED_CONFIRMATION_PATH",
                   "ID_WRITEBACK_PATH", "SCAN_STATUS_PATH", "MIRROR_PATH", "RENEWAL_OUTCOMES_PATH",
                   "SPYCLOUD_STATE_PATH", "VALIDATION_PATH")
PAGES = ("/", "/history", "/dev/history", "/dev/onboarded", "/prod", "/prod/matches", "/prod/tenants", "/inventory",
         "/connection", "/co/CO-0001", "/co/CO-0002", "/co/CO-0001?env=prod", "/attended/ce-only-runner-status?ref=CO-0001")
SENTINEL_ACCOUNT = "Acme Snapshot Customer"


def queue_row(reference, product, kind):
    return {"Name": reference, "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New",
            "Onboarding_Product__c": product, "Onboarding_Type__c": kind, "Account__r.Name": SENTINEL_ACCOUNT,
            "Submission_Date__c": "2026-10-01"}


def detail(reference, product, kind):
    values = {field: None for field in dashboard.DETAIL_FIELDS}
    values.update({"Name": reference, "Onboarding_Approval_Status__c": "Approved", "Account_Name__c": SENTINEL_ACCOUNT,
                   "Onboarding_Product__c": product, "Onboarding_Type__c": kind, "Onboarding_Stage__c": "New",
                   "Main_Domain__c": "snapshot-customer.example", "Email_Domains__c": "snapshot-customer.example",
                   "LastModifiedDate": "2026-10-01T10:00:00.000+0000"})
    return values


FILES = {
    vs.QUEUE_FILE: json.dumps({"rows": [queue_row("CO-0001", "Credential Exposure", "New Product Onboarding"),
                                        queue_row("CO-0002", "Surface", "New Product Onboarding")]}).encode(),
    vs.CO_DETAILS_FILE: json.dumps({"details": {
        "CO-0001": detail("CO-0001", "Credential Exposure", "New Product Onboarding"),
        "CO-0002": detail("CO-0002", "Surface", "New Product Onboarding")}}).encode(),
    "attended_leonardo_readbacks.json": json.dumps({"CO-0001": {
        "surface_account_id": "a" * 20, "account_uuid": "b" * 32, "leonardo_state": "Account Scanning",
        "observed_on": "2026-10-02", "source": "Leonardo Development Details readback"}}).encode(),
}


class SnapshotDashboardCase(ServerCase):
    """A real loopback server in VM mode, a temporary state folder, and guards on every process and network door."""

    age_hours = 1.0
    publish = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="surface_snapshot_dashboard_")
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.root = vs.snapshots_root(self.state)
        if self.publish:
            created = datetime.now(timezone.utc) - timedelta(hours=self.age_hours)
            vs.ingest_bundle(vs.build_bundle(FILES, KEY, "desktop", created), KEY, self.state)
        env = {"SURFACE_ONBOARDING_RUNTIME": "vm", "SURFACE_ONBOARDING_STATE_DIR": str(self.state),
               "SURFACE_ONBOARDING_PROXY_SECRET": SECRET, "SURFACE_ONBOARDING_PUBLIC_ORIGIN": PUBLIC,
               "SURFACE_ONBOARDING_ALLOWED_USERS": f"{OWNER},{VIEWER}", "SURFACE_ONBOARDING_OPERATORS": OWNER,
               "SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS": ""}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Module constants were built at import (desktop); VM processes build them through ".../snapshots/current".
        # There is no symlink on Windows, so point them at the resolved snapshot directory the pointer helper reads.
        folder = vs.current_dir(self.root) or self.root / vs.CURRENT_LINK
        for module, names in ((runner, RUNNER_NAMES), (dashboard, DASHBOARD_NAMES)):
            for name in names:
                patcher = patch.object(module, name, folder / getattr(module, name).name)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.calls = []
        self.guard("subprocess.run", "subprocess.Popen", "subprocess.check_output", "os.system", "webbrowser.open_new_tab")
        real_connect = socket.create_connection

        def guarded_connect(address, *args, **kwargs):
            if address[0] not in ("127.0.0.1", "localhost", "::1"):
                self.calls.append(("network", address[0]))
                raise AssertionError("network call in VM mode")
            return real_connect(address, *args, **kwargs)

        patcher = patch("socket.create_connection", guarded_connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.assert_no_external_calls)
        self.addCleanup(self.drain_background_reads)  # runs first: queued warm-up reads finish while VM mode is on
        dashboard.clear_display_cache()

    @staticmethod
    def drain_background_reads():
        for future in [dashboard._display_prefetch_pool.submit(lambda: None) for _ in range(8)]:
            future.result(timeout=30)

    def guard(self, *targets):
        for target in targets:
            def refuse(*_args, _target=target, **_kwargs):
                self.calls.append(("process", _target))
                raise AssertionError("process or browser call in VM mode: " + _target)

            patcher = patch(target, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_no_external_calls(self):
        self.assertEqual(self.calls, [])

    def get(self, path, email=VIEWER):
        return self.request("GET", path, {dashboard.PROXY_IDENTITY_HEADER: email})


class VmPagesFromSnapshotTests(SnapshotDashboardCase):
    def test_queue_page_shows_published_rows_with_the_banner_and_no_external_call(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("CO-0001", body)
        self.assertIn("CO-0002", body)
        self.assertIn(SENTINEL_ACCOUNT, body)
        self.assertIn("Data as of ", body)
        self.assertIn("(published from the desktop)", body)
        self.assertNotIn("snapbanner stale", body)
        self.assertNotIn("snapbanner empty", body)

    def test_local_state_comes_from_the_snapshot_files(self):
        _, _, body = self.get("/")
        self.assertIn("Account Scanning", body)  # attended_leonardo_readbacks.json of the snapshot

    def test_every_page_renders_with_the_banner_and_without_calling_out(self):
        for path in PAGES:
            status, _, body = self.get(path)
            self.assertIn(status, (200, 303), path)  # no page may fall over for lack of a live source
            if status != 303:
                self.assertIn("Data as of ", body, path)
            self.assertNotIn("Traceback", body, path)
        self.assertEqual(self.calls, [])

    def test_co_page_uses_the_published_detail_fields(self):
        status, _, body = self.get("/co/CO-0001")
        self.assertEqual(status, 200)
        self.assertIn("snapshot-customer.example", body)
        self.assertIn(SENTINEL_ACCOUNT, body)

    def test_a_co_that_was_not_published_is_a_clear_unavailable_page(self):
        status, _, body = self.get("/co/CO-0999")
        self.assertEqual(status, 503)
        self.assertIn("Not in the published data", body)
        self.assertIn("Data as of ", body)

    def test_search_redirects_inside_the_dashboard(self):
        status, headers, _ = self.get("/search?q=CO-0001")
        self.assertIn(status, (200, 303))
        self.assertEqual(self.calls, [])

    def test_no_action_forms_or_buttons_on_any_page(self):
        for path in PAGES:
            status, _, body = self.get(path, email=OWNER)  # even the operator gets a read-only view
            lowered = re.sub(r"<style>.*?</style>", "", body.lower(), flags=re.DOTALL)
            self.assertNotIn("method='post'", lowered, path)
            self.assertNotIn('method="post"', lowered, path)
            for label in ("start-form", "start onboarding", "prepare sessions", "check session"):
                self.assertNotIn(label, lowered, path + " " + label)

    def test_server_side_refusals_remain(self):
        status, _, body = self.post("/attended/start-ce-only-runner", OWNER, "CO-0001")
        self.assertEqual(status, 403)
        self.assertIn("Not available on the VM", body)
        status, _, _ = self.post("/attended/prepare-sessions", VIEWER, "CO-0001")
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])

    def test_the_cli_door_is_closed_in_vm_mode(self):
        with self.assertRaises(OSError):
            dashboard.salesforce_cli_command()
        with self.assertRaises(RuntimeError):
            runner.sf_command()
        with self.assertRaises(dashboard.ReadUnavailable):
            dashboard.sf_json(["data", "query", "--query", "SELECT Id FROM Account"])
        with self.assertRaises(dashboard.WriteUnavailable):
            dashboard.sf_write_json(["data", "update", "record"])
        with self.assertRaises(ValueError):
            runner._sf_records("SELECT Id FROM Account")
        self.assertEqual(self.calls, [])

    def test_snapshot_switch_is_picked_up_without_restart(self):
        FILES2 = dict(FILES)
        FILES2[vs.QUEUE_FILE] = json.dumps({"rows": [queue_row("CO-0003", "Surface", "New Product Onboarding")]}).encode()
        vs.ingest_bundle(vs.build_bundle(FILES2, KEY, "desktop", datetime.now(timezone.utc)), KEY, self.state)
        dashboard.clear_display_cache()
        _, _, body = self.get("/")
        self.assertIn("CO-0003", body)
        self.assertNotIn("CO-0002", body)

    def test_banner_time_is_the_published_time(self):
        manifest = vs.read_current_manifest(self.root)
        created = vs.parse_created_at(manifest["created_at"])
        _, _, body = self.get("/")
        self.assertIn("Data as of " + created.strftime("%Y-%m-%d %H:%M") + " UTC (published from the desktop)", body)


class StaleBannerTests(SnapshotDashboardCase):
    age_hours = 8.0

    def test_amber_after_six_hours_by_default(self):
        _, _, body = self.get("/")
        self.assertIn("snapbanner stale", body)
        self.assertIn("older than 6 h", body)

    def test_threshold_follows_the_environment_variable(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS": "12"}):
            _, _, body = self.get("/")
        self.assertNotIn("snapbanner stale", body)
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS": "2"}):
            _, _, body = self.get("/")
        self.assertIn("older than 2 h", body)
        for bad in ("abc", "0", "-3", "1e9", "nan"):
            with patch.dict(os.environ, {"SURFACE_ONBOARDING_SNAPSHOT_STALE_HOURS": bad}):
                self.assertEqual(dashboard.snapshot_stale_hours(), dashboard.DEFAULT_SNAPSHOT_STALE_HOURS, bad)


class EmptyStateTests(SnapshotDashboardCase):
    publish = False

    def test_no_snapshot_means_a_clear_empty_state_on_every_page(self):
        for path in ("/", "/history", "/dev/onboarded", "/prod", "/co/CO-0001", "/connection"):
            status, _, body = self.get(path)
            self.assertIn("No data published yet", body, path)
            self.assertNotIn("Data as of ", body, path)
            self.assertIn(status, (200, 503), path)
            self.assertNotIn("Traceback", body, path)
        self.assertEqual(self.calls, [])

    def test_empty_state_is_not_a_login_or_salesforce_error(self):
        _, _, body = self.get("/")
        self.assertNotIn("Sign in with Salesforce", body)


class DesktopUnchangedTests(unittest.TestCase):
    def test_desktop_shell_has_no_banner_and_keeps_post_forms(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop", "SURFACE_ONBOARDING_STATE_DIR": ""}):
            page = dashboard._app_shell("T", "<form method='post' action='/x'><button>Go</button></form><p>body</p>")
        self.assertIn("method='post'", page)
        self.assertNotIn("snapbanner", page)
        self.assertNotIn("Data as of", page)
        self.assertIn("Attended · localhost only", page)

    def test_desktop_data_functions_do_not_use_the_snapshot_switch(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop", "SURFACE_ONBOARDING_STATE_DIR": ""}):
            self.assertIsNone(state_paths.snapshot_current_dir())
            default = Path("C:/repo/integration/attended_x.json")
            self.assertEqual(state_paths.state_file(default), default)
            with patch.object(dashboard, "snapshot_queue_rows", side_effect=AssertionError("snapshot read on the desktop")), \
                    patch.object(dashboard, "snapshot_detail_row", side_effect=AssertionError("snapshot read on the desktop")), \
                    patch.object(dashboard, "open_onboardings_view_id", side_effect=dashboard.ReadUnavailable()):
                with self.assertRaises(dashboard.ReadUnavailable):
                    dashboard.queue_source_rows()  # went to the Salesforce path, not the snapshot
                with patch.object(dashboard, "sf_json", side_effect=dashboard.ReadUnavailable()):
                    with self.assertRaises(dashboard.ReadUnavailable):
                        dashboard.detail_row("CO-0001")

    def test_desktop_sf_command_still_resolves(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}):
            self.assertTrue(dashboard.salesforce_cli_command())
            self.assertTrue(runner.sf_command())


if __name__ == "__main__":
    unittest.main()
