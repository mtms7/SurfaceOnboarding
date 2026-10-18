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
PAGES = ("/", "/dev", "/prod/approvals", "/history", "/dev/history", "/dev/onboarded", "/prod", "/prod/matches", "/prod/tenants", "/inventory",
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
        # The summary- and create-approval files are desktop-time constants under the repo; keep the test hermetic (nothing
        # there = unavailable). 2026-10-18: the first real create approval on this PC exposed the missing second patch.
        for name, file_name in (("REVIEW_APPROVAL_PATH", "onboarding_review_approvals.json"),
                                ("PRODUCTION_APPROVAL_PATH", "production_create_approvals.json")):
            patcher = patch.object(dashboard, name, folder / file_name)
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
        _, _, body = self.get("/dev")  # the Dev queue moved from / to /dev (production-first dashboard, 2026-10-16)
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

    def test_no_live_read_wording_refresh_buttons_or_sessions_link(self):
        for path in PAGES:
            status, _, body = self.get(path, email=OWNER)
            lowered = re.sub(r"<style>.*?</style>", "", body.lower(), flags=re.DOTALL)
            self.assertNotIn("read from salesforce at", lowered, path)
            self.assertNotIn("open to retry", lowered, path)
            self.assertNotIn("name='refresh'", lowered, path)
            self.assertNotIn(">refresh<", lowered, path)
            self.assertNotIn(">sessions<", lowered, path)
            self.assertNotIn("<summary>more</summary>", lowered, path)

    def test_strip_actions_rewrites_only_vm_markup(self):
        page = ("<span class='note'>Read from Salesforce at 21:23:48</span><div class='head-meta'>"
                "<form method='get' action='/'><input type='hidden' name='refresh' value='1'>"
                "<button class='ghost' type='submit'>Refresh</button></form>"
                "<details class='menu'><summary>More</summary><div class='menu-pop'>"
                "<form method='post' action='/x'><button>Go</button></form></div></details></div>"
                "<form method='get' action='/search'><input name='q'></form><small>Unavailable · open to retry</small>")
        out = dashboard.vm_strip_actions(page)
        self.assertEqual(out, "<span class='note'>Page rendered at 21:23:48</span><div class='head-meta'></div>"
                              "<form method='get' action='/search'><input name='q'></form>"
                              "<small>Not included in the published snapshot</small>")

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


class VmStartDatesFromSnapshotTests(SnapshotDashboardCase):
    """Start dates on the VM come from the published CO comments (same validated parser), never a live read."""

    def publish_with_comments(self, comments):
        details = {ref: {**detail(ref, "Credential Exposure", "New Product Onboarding"), "Onboarding_Comments__c": text}
                   for ref, text in comments.items()}
        files = {**FILES, vs.CO_DETAILS_FILE: json.dumps({"details": details}).encode()}
        vs.ingest_bundle(vs.build_bundle(files, KEY, "desktop", datetime.now(timezone.utc)), KEY, self.state)
        dashboard.clear_display_cache()

    def test_summaries_and_start_dates_come_from_the_published_comments(self):
        self.publish_with_comments({"CO-0001": "DealHub 2027-03-05 - 2028-03-04 validated", "CO-0002": "no range here"})
        self.assertEqual(dashboard.subscription_summaries(("CO-0001", "CO-0002")),
                         {"CO-0001": {"Subscription_Start": "2027-03-05", "Subscription_End": "2028-03-04"},
                          "CO-0002": {"Subscription_Start": "Not verified", "Subscription_End": "Not verified"}})
        self.assertEqual(dashboard.queue_start_dates(("CO-0001", "CO-0002")), {"CO-0001": "2027-03-05"})

    def test_a_co_without_a_published_detail_fails_closed(self):
        self.publish_with_comments({"CO-0001": "2027-03-05 - 2028-03-04"})
        with self.assertRaises(dashboard.ReadUnavailable):
            dashboard.subscription_summaries(("CO-0001", "CO-0003"))

    def test_queue_page_shows_the_start_date_without_the_unavailable_note_and_no_comment(self):
        self.publish_with_comments({"CO-0001": "Secret note 2027-03-05 - 2028-03-04", "CO-0002": None})
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Mar 5, 2027", body)
        self.assertNotIn("could not be read from Salesforce", body)
        self.assertNotIn("Secret note", body)


class VmCloneWordingTests(SnapshotDashboardCase):
    """On the VM an unusable clone points to the VM's own collector, never to a Windows command (docs/43 section 9)."""

    def test_unusable_clone_line_and_action_name_the_vm_collector(self):
        line = dashboard._clone_line({"reason": "inventory_snapshot_missing"})
        self.assertIn("inventory_snapshot_missing", line)
        self.assertIn("VM", line)
        self.assertNotIn("redash_inventory_collector.py", line)
        action = dashboard._prod_action({"status": "clone_unavailable", "reason": "inventory_snapshot_missing"})
        self.assertNotIn("redash_inventory_collector.py", action)

    def test_the_prod_tenants_page_has_no_windows_command_when_the_clone_is_missing(self):
        status, _, body = self.get("/prod/tenants")
        self.assertEqual(status, 200)
        self.assertNotIn("redash_inventory_collector.py --collect</code>", body)


class VmProductionMatchTests(SnapshotDashboardCase):
    """The VM answers the production match from the signed snapshot: the clone plus the published per-CO inputs
    (docs/43 section 9). It never reads Salesforce, and anything unexpected fails closed."""

    PRODUCT, KIND = "Credential Exposure", "New Product Onboarding"

    def item(self, **over):
        value = {"engine": "case_2_ce_only", "product": self.PRODUCT, "type": self.KIND,
                 "co_source": {"tenant_names": ["Acme Snapshot Customer - CE Only"], "domains": ["snapshot-customer.example"]},
                 "plan": {"primary_domain": "snapshot-customer.example", "alternate_domains": [],
                          "license_type": "Prepaid annual subscription", "license_end": "2027-10-18",
                          "license_start": "2026-10-01", "spycloud_shown": True}}
        value.update(over)
        return value

    def publish_inputs(self, inputs):
        files = {**FILES, vs.PRODUCTION_INPUTS_FILE: json.dumps({"inputs": inputs}).encode()}
        vs.ingest_bundle(vs.build_bundle(files, KEY, "desktop", datetime.now(timezone.utc)), KEY, self.state)
        dashboard.clear_display_cache()

    def test_the_expectation_comes_from_the_published_inputs(self):
        self.publish_inputs({"CO-0001": self.item()})
        engine, source, plan = runner._production_expectation("CO-0001", self.PRODUCT, self.KIND)
        self.assertEqual(engine, "case_2_ce_only")
        self.assertEqual(source, {"tenant_names": ("Acme Snapshot Customer - CE Only",), "domains": ("snapshot-customer.example",)})
        self.assertEqual(plan["license_end"], "2027-10-18")

    def test_a_missing_different_or_malformed_entry_fails_closed(self):
        self.publish_inputs({"CO-0001": self.item(), "CO-0002": self.item(engine=None),
                             "CO-0003": self.item(co_source={"tenant_names": "x", "domains": []})})
        for reference, product, kind in (("CO-0009", self.PRODUCT, self.KIND),      # no entry
                                         ("CO-0001", "Surface", self.KIND),          # the CO changed product
                                         ("CO-0002", self.PRODUCT, self.KIND),       # no engine
                                         ("CO-0003", self.PRODUCT, self.KIND)):      # malformed names
            with self.subTest(reference=reference, product=product):
                with self.assertRaises(RuntimeError):
                    runner._production_expectation(reference, product, kind)

    def test_without_a_published_file_the_expectation_is_unavailable(self):
        with self.assertRaises(RuntimeError):
            runner._production_expectation("CO-0001", self.PRODUCT, self.KIND)

    def test_the_match_uses_the_clone_and_the_published_inputs_without_a_salesforce_read(self):
        from integration.tests.test_production_match import snapshot as clone, tenant

        self.publish_inputs({"CO-0001": self.item()})
        with patch.object(runner, "_sf_records", side_effect=AssertionError("Salesforce read in VM mode")),                 patch("integration.onboarding.production_match.load_snapshot", return_value=clone(tenant("Other Co", "other.example"))):
            result = runner.production_match_for("CO-0001", self.PRODUCT, self.KIND)
            self.assertEqual(result["status"], "not_in_production")
            self.assertEqual(result["route"], "case_2_ce_only")
            duplicate = clone(tenant("Acme Snapshot Customer - CE Only", "snapshot-customer.example"))
        with patch("integration.onboarding.production_match.load_snapshot", return_value=duplicate):
            self.assertIn(runner.production_match_for("CO-0001", self.PRODUCT, self.KIND)["status"],
                          ("exists_matches", "exists_differs"))

    def test_country_round_trips_and_the_sheet_reads_it(self):
        self.publish_inputs({"CO-0001": self.item(country="Chile")})
        self.assertEqual(dashboard._read_sheet_country("CO-0001"), "Chile")

    def test_a_real_country_name_with_punctuation_round_trips(self):
        for name in ("C\u00f4te d'Ivoire", "Korea, Republic of", "Congo (Kinshasa)", "T\u00fcrkiye", "Timor-Leste"):
            self.publish_inputs({"CO-0001": self.item(country=name)})
            self.assertEqual(dashboard._read_sheet_country("CO-0001"), name)

    def test_the_vm_country_read_never_calls_salesforce(self):
        self.publish_inputs({"CO-0001": self.item(country="Chile")})
        calls = []
        dashboard.clear_display_cache()
        with patch.object(dashboard, "sf_json", side_effect=lambda *a, **k: calls.append(a)):
            self.assertEqual(dashboard._read_sheet_country("CO-0001"), "Chile")
            self.assertIsNone(dashboard._read_sheet_country("CO-0009"))
        self.assertEqual(calls, [])

    def test_old_snapshot_without_country_still_ingests_and_the_country_is_unavailable(self):
        self.publish_inputs({"CO-0001": self.item()})
        self.assertIsNone(dashboard._read_sheet_country("CO-0001"))
        self.assertIsNone(dashboard._read_sheet_country("CO-0009"))  # no entry at all

    def test_malformed_country_is_unavailable_and_never_a_mismatch(self):
        for bad in ("Chile<script>", "", " Chile", "Chile ", "C" * 61, "Chile" + chr(10), 7, None, ["Chile"], "Chile 2"):
            self.publish_inputs({"CO-0001": self.item(country=bad)})
            self.assertIsNone(dashboard._read_sheet_country("CO-0001"), repr(bad))
            runner._production_expectation("CO-0001", self.PRODUCT, self.KIND)  # still accepted

    def test_the_expectation_ignores_the_country_key(self):
        self.publish_inputs({"CO-0001": self.item()})
        without = runner._production_expectation("CO-0001", self.PRODUCT, self.KIND)
        self.publish_inputs({"CO-0001": self.item(country="Chile")})
        self.assertEqual(runner._production_expectation("CO-0001", self.PRODUCT, self.KIND), without)

    def test_the_page_no_longer_says_the_co_source_is_unreadable(self):
        self.publish_inputs({"CO-0001": self.item()})
        with patch("integration.onboarding.production_match.load_snapshot", return_value="inventory_snapshot_missing"):
            status, _, body = self.get("/co/CO-0001?env=prod")
        self.assertEqual(status, 200)
        self.assertNotIn("co_source_unreadable", body)


class VmProductionFirstPagesTests(SnapshotDashboardCase):
    """The production landing, CO page and Approvals page on the VM (docs/47 0.5): the approval files and the create
    records are not part of the signed snapshot, so those cells say "unavailable on this server", never "None",
    "Not approved yet" or "No approvals recorded"."""

    def test_landing_says_unavailable_not_none_and_has_no_controls(self):
        status, _, body = self.get("/", email=OWNER)
        self.assertEqual(status, 200)
        self.assertIn("Data as of ", body)
        row = body[body.index(">CO-0001<"):body.index("</tr>", body.index(">CO-0001<"))]
        self.assertEqual(row.count("Unavailable on this server"), 2)  # create approval and create record
        self.assertIn("<a class='co' href='/co/CO-0001?env=prod'>CO-0001</a>", body)  # the review summary cannot be built on the VM; the snapshot-backed view is linked
        for wording in ("chip-neutral'>None</span>", "Not approved yet", "No approvals recorded", "Locked</span>"):
            self.assertNotIn(wording, row)
        self.assertNotIn("<form method='post'", body)
        self.assertNotIn("<button", re.sub(r"<form class='sidesearch'.*?</form>", "", body, flags=re.S))

    def test_approvals_page_is_unavailable_read_only_and_names_both_kinds(self):
        status, _, body = self.get("/prod/approvals", email=OWNER)
        self.assertEqual(status, 200)
        self.assertEqual(body.count("unavailable on this server"), 2)
        for wording in ("No approvals recorded", "Not approved yet"):
            self.assertNotIn(wording, body)
        self.assertIn("Create approvals (7 days, one use)", body)
        self.assertIn("Summary approvals (4 hours)", body)
        self.assertNotIn("<form method='post'", body)

    def test_dev_queue_moved_to_dev_and_the_nav_is_production_first(self):
        status, _, body = self.get("/dev")
        self.assertEqual(status, 200)
        self.assertIn("CO-0001", body)
        aside = body[body.index("<aside"):body.index("</aside>")]
        self.assertIn("PRODUCTION", aside)
        self.assertLess(aside.index("PRODUCTION"), aside.index("DEV - LEONARDO"))
        self.assertNotIn(">Sessions<", aside)  # no Sessions link on the VM
        self.assertEqual(self.calls, [])

    def test_the_co_page_is_unavailable_or_read_only_never_a_create_control(self):
        status, _, body = self.get("/onboard/CO-0001", email=OWNER)
        self.assertIn(status, (200, 503))
        lowered = body.lower()
        self.assertNotIn("create in production", lowered)
        self.assertNotIn("start onboarding", lowered)
        self.assertNotIn("not approved yet", lowered)
        self.assertNotIn("<form method='post'", lowered)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()



class VmMatchFromPushedCloneTests(SnapshotDashboardCase):
    """The VM computes the per-CO match from the FRESH pushed clone (<state>/clone-inventory), never from a replayed result;
    a CO that already owns a clone tenant (its Account UUID) is Onboarded, not a duplicate (owner 2026-10-17)."""

    PRODUCT, KIND = "Credential Exposure", "New Product Onboarding"
    UUID = f"{2:032x}"  # the accountUuid of tenant row 2 of test_clone_push.row
    NAME, DOMAIN = "Acme Snapshot Customer - CE Only", "snapshot-customer.example"

    def item(self, **over):
        value = {"engine": runner.CE_ENGINE, "product": self.PRODUCT, "type": self.KIND,
                 "co_source": {"tenant_names": [self.NAME], "domains": [self.DOMAIN]},
                 "plan": {"primary_domain": self.DOMAIN, "alternate_domains": [], "license_type": "Prepaid annual subscription",
                          "license_end": "2027-10-18", "license_start": "2026-10-01", "spycloud_shown": True}}
        value.update(over)
        return value

    def publish(self, inputs, uuid=None):
        refs = ("CO-0001", "CO-0002")
        details = {ref: {**detail(ref, "Credential Exposure" if ref == "CO-0001" else "Surface", "New Product Onboarding"),
                         "Account_UUID__c": uuid if ref == "CO-0001" else None} for ref in refs}
        files = {**FILES, vs.CO_DETAILS_FILE: json.dumps({"details": details}).encode(),
                 vs.PRODUCTION_INPUTS_FILE: json.dumps({"inputs": inputs}).encode()}
        vs.ingest_bundle(vs.build_bundle(files, KEY, "desktop", datetime.now(timezone.utc)), KEY, self.state)
        dashboard.clear_display_cache()

    def push_clone(self, name=None, domain=None, age=timedelta(minutes=50), **tenant_fields):
        from integration.onboarding import leonardo_inventory as inv, redash_inventory as redash
        from integration.tests import test_clone_push as cp
        rows = [cp.row(1), {**cp.row(2), "accountName": name or self.NAME, "accountDomain": domain or self.DOMAIN,
                            "alternateDomains": [], "accountLicense.licenseType": "Prepaid annual subscription",
                            "accountLicense.expirationDate": 1823817600000, "accountLicense.assetsNumber": 1,
                            "accountLicense.domainsNumber": 1, "accountLicense.subDomainsNumber": 1, **tenant_fields}]
        retrieved = (datetime.now(timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%S")
        normalized = redash.normalize({"query_result": {"retrieved_at": retrieved, "data": {
            "columns": [{"name": key, "type": "string"} for key in rows[0]], "rows": rows}}})
        payload = inv.snapshot_payload(inv.require_environment("prod-clone"), inv.assemble_rows(normalized.rows),
                                       normalized.retrieved_at, acquisition=inv.REDASH_ACQUISITION,
                                       expected_paths=redash.EXPECTED_REDASH_PATHS)
        inv.write_snapshot(payload, self.state / state_paths.CLONE_SUBDIR, keep=4)

    def match(self, reference="CO-0001"):
        return runner.production_match_for(reference, self.PRODUCT, self.KIND)

    def test_the_match_uses_the_fresh_pushed_clone_unpatched(self):
        self.publish({"CO-0001": self.item()})
        self.push_clone()
        self.assertIn(self.match()["status"], ("exists_matches", "exists_differs"))
        self.push_clone("Other Co", "other.example")
        self.assertEqual(self.match()["status"], "not_in_production")

    def test_a_stale_pushed_clone_still_fails_closed(self):
        self.publish({"CO-0001": self.item()})
        self.push_clone(age=timedelta(hours=7))
        self.assertEqual((self.match()["status"], self.match()["reason"]), ("clone_unavailable", "inventory_snapshot_stale"))

    def test_a_new_push_is_reflected_at_once_not_after_the_display_cache_expires(self):
        self.publish({"CO-0001": self.item()})
        self.push_clone("Other Co", "other.example")
        token = dashboard._display_reads.set(True)  # the display cache is only active inside a display GET
        self.addCleanup(dashboard._display_reads.reset, token)
        self.assertEqual(dashboard._match_of("CO-0001", self.PRODUCT, self.KIND)["status"], "not_in_production")
        self.push_clone()  # the tenant appears in the next push; no cache clearing in between
        self.assertIn(dashboard._match_of("CO-0001", self.PRODUCT, self.KIND)["status"], ("exists_matches", "exists_differs"))

    def test_a_co_that_owns_the_clone_tenant_is_onboarded_not_duplicate(self):
        self.publish({"CO-0001": self.item()}, uuid=self.UUID)
        self.push_clone()
        found = self.match()
        self.assertEqual(found["status"], "onboarded")
        self.assertEqual(found["review"]["warnings"], [])
        status, _, body = self.get("/", email=OWNER)
        row = body[body.index(">CO-0001<"):body.index("</tr>", body.index(">CO-0001<"))]
        self.assertIn("Onboarded", row)
        self.assertIn("license active until 2027-10-18", row)
        self.assertNotIn(">Duplicate<", row)
        self.assertNotIn(self.UUID, body)
        self.assertNotIn("<form method='post'", body)

    def test_the_onboarded_status_line_carries_each_deviation_as_a_warning(self):
        self.publish({"CO-0001": self.item()}, uuid=self.UUID)
        self.push_clone(**{"enabled": False})
        found = self.match()
        self.assertEqual(found["status"], "onboarded")
        self.assertIn("tenant_disabled", found["review"]["warnings"])
        _, _, body = self.get("/", email=OWNER)
        self.assertIn("tenant disabled", body)

    def test_uuid_set_but_not_in_the_clone_is_not_onboarded_and_not_clear(self):
        self.publish({"CO-0001": self.item()}, uuid=f"{99:032x}")
        self.push_clone("Other Co", "other.example")
        self.assertEqual(self.match()["status"], "uuid_not_in_clone")
        _, _, body = self.get("/", email=OWNER)
        self.assertIn("UUID set, not in clone", body)

    def test_a_different_uuid_on_the_matching_tenant_stays_a_blocking_duplicate(self):
        self.publish({"CO-0001": self.item()}, uuid=f"{99:032x}")
        self.push_clone()
        self.assertIn(self.match()["status"], ("exists_matches", "exists_differs"))
        _, _, body = self.get("/", email=OWNER)
        self.assertIn(">Duplicate<", body)

    def test_a_co_without_prepared_inputs_is_not_checked_and_does_not_blame_the_clone(self):
        self.publish({"CO-0001": self.item()})
        self.push_clone()
        found = self.match("CO-0002")
        self.assertEqual((found["status"], found["reason"]), ("not_checked", "production_inputs_not_prepared"))
        _, _, body = self.get("/", email=OWNER)
        row = body[body.index(">CO-0002<"):body.index("</tr>", body.index(">CO-0002<"))]
        self.assertIn("Not checked", row)
        self.assertNotIn("Clone unavailable", row)
        self.assertNotIn("Clone stale", row)

    def test_only_inactive_tenants_show_other_tenants_only(self):
        self.publish({"CO-0001": self.item()})
        self.push_clone(**{"enabled": False})
        _, _, body = self.get("/", email=OWNER)
        row = body[body.index(">CO-0001<"):body.index("</tr>", body.index(">CO-0001<"))]
        self.assertIn("Other tenants only", row)

    def test_cache_is_bounded_and_a_new_stamp_supersedes_the_old_entry(self):
        token = dashboard._display_reads.set(True)
        self.addCleanup(dashboard._display_reads.reset, token)
        with patch.object(dashboard, "production_match_for", return_value={"status": "not_in_production", "reason": ""}):
            dashboard.production_match_display("CO-0001", "p", "t", "stamp-1")
            dashboard.production_match_display("CO-0001", "p", "t", "stamp-2")
            keys = [k for k in dashboard._display_cache if k[0] == "production_match_display"]
            self.assertEqual(keys, [("production_match_display", "CO-0001", "p", "t", "stamp-2")])
            with patch.object(dashboard, "DISPLAY_CACHE_MAX_ENTRIES", 5):
                for index in range(20):
                    dashboard.production_match_display(f"CO-{index:04d}", "p", "t", "s")
            self.assertLessEqual(len(dashboard._display_cache), 5)

    def test_a_cached_match_is_re_aged_at_read_time_and_fails_closed_after_6_hours(self):
        now = datetime.now(timezone.utc)
        cached = {"status": "onboarded", "reason": "x", "captured_at": "2026-10-17T10:00:00Z", "age_seconds": 5 * 3600 + 59 * 60,
                  "computed_at": now.isoformat(), "review": {"warnings": []}}
        self.assertEqual(dashboard._fresh_match(cached, now + timedelta(seconds=30))["status"], "onboarded")
        later = dashboard._fresh_match(cached, now + timedelta(minutes=2))
        self.assertEqual((later["status"], later["reason"]), ("clone_unavailable", "inventory_snapshot_stale"))
        self.assertEqual(dashboard._fresh_match({**cached, "computed_at": "garbage"}, now)["status"], "clone_unavailable")
        unstamped = {"status": "not_in_production", "reason": "", "candidates": []}
        failed = dashboard._fresh_match(unstamped)  # production default: no computed_at -> fail closed
        self.assertEqual((failed["status"], failed["reason"]), ("clone_unavailable", "match_age_unverified"))
        with patch.object(dashboard, "UNSTAMPED_MATCH_ALLOWED", True):  # the explicit test-only exemption
            self.assertEqual(dashboard._fresh_match(unstamped)["status"], "not_in_production")
        self.assertFalse(dashboard.UNSTAMPED_MATCH_ALLOWED)

    def test_two_deleted_uuid_twins_read_ambiguous_not_live_paid(self):
        self.publish({"CO-0001": self.item()}, uuid=self.UUID)
        self.push_clone(**{"isDeleted": True})
        from integration.onboarding import production_views as pv
        match = {"status": "ambiguous", "reason": "", "candidates": [{"live_paid": False}, {"live_paid": False}]}
        self.assertEqual(pv.production_check(match)["label"], "Ambiguous")

    def test_filters_keep_uuid_and_not_checked_out_of_clone_unavailable(self):
        self.assertEqual(dashboard.MATCH_FILTERS["clone_unavailable"], ("clone_unavailable",))
        self.assertEqual(dashboard.MATCH_FILTERS["uuid_not_in_clone"], ("uuid_not_in_clone",))
        self.assertEqual(dashboard.MATCH_FILTERS["not_checked"], ("not_checked",))
        self.assertEqual(dashboard.MATCH_FILTERS["onboarded"], ("onboarded",))
        self.assertNotIn("onboarded", dashboard.MATCH_FILTERS["matches"])
        self.assertEqual({key for key, _ in dashboard.MATCH_FILTER_LABELS}, set(dashboard.MATCH_FILTERS))

    def test_the_desktop_match_says_the_uuid_check_runs_on_the_vm(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}):
            self.assertIsNone(runner._snapshot_account_uuid("CO-0001"))

    def test_an_onboarded_co_has_no_overdue_chip_and_a_neutral_page(self):
        self.publish({"CO-0001": self.item()}, uuid=self.UUID)
        self.push_clone()
        with patch.object(dashboard, "queue_start_dates", return_value={"CO-0001": "2026-05-01", "CO-0002": "2026-05-01"}):
            _, _, body = self.get("/", email=OWNER)
        self.assertIn("overdue", body.casefold())  # the other CO (not onboarded) still shows it
        row = body[body.index(">CO-0001<") - 600:body.index("</tr>", body.index(">CO-0001<"))]
        self.assertIn(">done<", row)
        self.assertNotIn("overdue", row.casefold())
        status, _, page = self.get("/co/CO-0001?env=prod", email=OWNER)
        self.assertEqual(status, 200)
        self.assertIn("banner banner-ok", page)
        self.assertIn("Onboarded: tenant found in production, enabled, license active until 2027-10-18", page)
        self.assertNotIn("Enter in BackOffice", page)
        self.assertNotIn("Salesforce already holds an Account UUID", page)
        self.assertIn("tenant clone is separate and is ", page)
        self.assertNotIn("<form method='post'", page)
