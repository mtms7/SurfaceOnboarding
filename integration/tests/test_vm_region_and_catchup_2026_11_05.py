"""Owner go 2026-11-05 (plan items 1 and 2 of the EU + US collection review):

1. The VM applies the desktop's Surface Tenant Region rule: the snapshot publisher reads every published CO's region in one
   batched read-only query and stores it beside the CO's production inputs; the VM runner applies ``apply_region`` with it.
   A CO without a published region is "Region unknown: not checked" on the VM, never "Clear" (fail closed).
2. The clone auto-refresh catches the daily clone load sooner: from 00:05 UTC until the stored data holds today's load it
   collects every 15 min instead of 45 (GET passes; the collector's own 60-min re-run rule is unchanged), and the clone strip
   says so and names the region the clone covers.

Hermetic: fake reads, fake clock and fake collector; nothing reads Salesforce, Redash or the VM.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.publish_vm_snapshot as publisher
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_match as match_module
from integration.onboarding import production_views as views
from integration.onboarding import vm_snapshot as vs
from tools import clone_autorefresh as auto

SNAPSHOT = {"tenants": [{"account_name": "Other", "account_uuid": "b" * 32, "alternate_domains": []}], "captured_at": None}


def _query_reply(records):
    return {"status": 0, "result": {"records": records}}


class CoRegionsReadTests(unittest.TestCase):
    def test_one_batched_read_blank_is_empty(self):
        records = [{"Name": "CO-0835", "Surface_Tenant_Region__c": "US"}, {"Name": "CO-0816", "Surface_Tenant_Region__c": None}]
        with patch.object(dashboard, "sf_json", return_value=_query_reply(records)) as read:
            regions = dashboard.co_regions(("CO-0816", "CO-0835"))
        self.assertEqual(regions, {"CO-0835": "US", "CO-0816": ""})
        query = read.call_args[0][0][3]
        self.assertIn("Surface_Tenant_Region__c", query)
        self.assertIn("'CO-0816', 'CO-0835'", query)
        self.assertEqual(read.call_count, 1)

    def test_bad_input_or_reply_is_unavailable(self):
        with self.assertRaises(dashboard.ReadUnavailable):
            dashboard.co_regions(("CO-0816' OR Name != '",))
        with self.assertRaises(dashboard.ReadUnavailable):
            dashboard.co_regions(())
        for records in ([{"Name": "CO-9999", "Surface_Tenant_Region__c": "US"}],          # not asked for
                        [{"Name": "CO-0816", "Surface_Tenant_Region__c": 5}],            # wrong type
                        [{"Name": "CO-0816"}],                                           # field missing
                        [{"Name": "CO-0816", "Surface_Tenant_Region__c": "US"}] * 2):    # duplicate
            with self.subTest(records=records), patch.object(dashboard, "sf_json", return_value=_query_reply(records)):
                with self.assertRaises(dashboard.ReadUnavailable):
                    dashboard.co_regions(("CO-0816",))


class PublisherRegionTests(unittest.TestCase):
    def publish(self, regions):
        row = {field: None for field in dashboard.QUEUE_FIELDS}
        row.update(Name="CO-0001", Onboarding_Product__c="Credential Exposure", Onboarding_Type__c="New Product Onboarding")
        found = {"engine": "case_2_ce_only", "co_source": {"tenant_names": ("N",), "domains": ("d.example",)},
                 "plan": {"license_end": date(2027, 1, 2), "alternate_domains": ()}}
        with patch.object(dashboard, "queue_source_rows", return_value=[row, {**row, "Name": "CO-0002"}]), \
                patch.object(dashboard, "detail_row", return_value={"Name": "x"}), \
                patch.object(dashboard, "dev_onboarded_refs", return_value=[]), \
                patch.object(dashboard, "co_identity_for", return_value=None), \
                patch.object(dashboard, "_try_sheet_country", return_value=(True, None)), \
                patch.object(dashboard, "production_review_inputs", return_value=found), \
                patch.object(dashboard, "co_regions", **regions) as read:
            files, counts = publisher.collect_dashboard_reads()
        return json.loads(files[vs.PRODUCTION_INPUTS_FILE])["inputs"], counts, read

    def test_each_published_co_carries_its_region(self):
        inputs, counts, read = self.publish({"return_value": {"CO-0001": "US", "CO-0002": ""}})
        self.assertEqual((inputs["CO-0001"]["region"], inputs["CO-0002"]["region"]), ("US", ""))
        self.assertEqual((counts["co_regions"], counts["co_regions_unavailable"]), (2, 0))
        read.assert_called_once_with(("CO-0001", "CO-0002"))

    def test_a_failed_read_publishes_no_region(self):
        inputs, counts, _read = self.publish({"side_effect": dashboard.ReadUnavailable()})
        self.assertNotIn("region", inputs["CO-0001"])
        self.assertEqual((counts["co_regions"], counts["co_regions_unavailable"]), (0, 2))

    def test_an_unexpected_value_is_not_published(self):
        inputs, counts, _read = self.publish({"return_value": {"CO-0001": "X" * 40, "CO-0009": "US"}})
        self.assertNotIn("region", inputs["CO-0001"])
        self.assertNotIn("CO-0009", inputs)
        self.assertEqual(counts["co_regions"], 0)


class VmRunnerRegionTests(unittest.TestCase):
    def run_vm(self, published, status="not_in_production"):
        full = mock.Mock(return_value={"status": status, "reason": ""})
        with patch.object(runner, "_production_expectation", return_value=("surface", {"s": 1}, {"p": 1})), \
                patch.object(runner, "_identity_for", side_effect=AssertionError("no live read on the VM")), \
                patch.object(runner, "clone_root", return_value=Path("unused")), \
                patch.object(match_module, "load_snapshot", return_value=SNAPSHOT), \
                patch.object(match_module, "production_match", full), \
                patch.object(runner, "_snapshot_account_uuid", return_value=""), \
                patch.object(runner, "_apply_verified_tenant", side_effect=lambda result, *_: result), \
                patch.object(runner, "_snapshot_region", return_value=published), \
                patch("integration.onboarding.state_paths.vm_mode", return_value=True):
            return runner.production_match_for("CO-0835", "Surface", "New Product Onboarding")

    def test_a_us_co_is_not_clear_on_the_vm(self):
        from integration.tests.test_us_clone_2026_11_09 import unpinned

        with unpinned():  # the EU-only rule (US query switched off); the US clone pass: test_us_clone_2026_11_09
            found = self.run_vm("US")
        self.assertEqual((found["status"], found["region"]), ("region_not_checked", "US"))
        self.assertEqual(views.production_check(found)["label"], "Region US: not checked")

    def test_blank_and_eu_stay_clear_on_the_vm(self):
        for region in ("", "EU"):
            self.assertEqual(self.run_vm(region)["status"], "not_in_production", region)

    def test_no_published_region_fails_closed(self):
        found = self.run_vm(runner.VM_REGION_UNKNOWN)
        self.assertEqual((found["status"], found["region"]), ("region_not_checked", "unknown"))

    def test_onboarded_stays_onboarded(self):
        self.assertEqual(self.run_vm("US", status="onboarded")["status"], "onboarded")

    def test_snapshot_region_reads_the_published_inputs(self):
        data = {"inputs": {"CO-0835": {"region": "US"}, "CO-0816": {"region": ""}, "CO-0001": {}, "CO-0002": {"region": 5}}}
        with patch("integration.onboarding.state_paths.state_dir", return_value="unused"), \
                patch.object(vs, "snapshots_root", return_value="unused"), \
                patch.object(vs, "load_current_json", return_value=data):
            self.assertEqual(runner._snapshot_region("CO-0835"), "US")
            self.assertEqual(runner._snapshot_region("CO-0816"), "")
            for reference in ("CO-0001", "CO-0002", "CO-0404"):
                self.assertEqual(runner._snapshot_region(reference), runner.VM_REGION_UNKNOWN, reference)
        with patch("integration.onboarding.state_paths.state_dir", return_value=None):
            self.assertEqual(runner._snapshot_region("CO-0835"), runner.VM_REGION_UNKNOWN)
        self.assertIsNone(match_module.region_of(runner.VM_REGION_UNKNOWN))


DAY = datetime(2026, 11, 5, tzinfo=timezone.utc)
YESTERDAY_LOAD = int(timedelta(hours=24, minutes=10).total_seconds())  # at 00:10 today: newest change 2026-11-04 00:00


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        auto.reset()
        self.calls = 0

    def collect(self):
        self.calls += 1
        return {"result": "inventory_unchanged", "refresh_failed": False}

    def tick(self, now, age, data_age):
        return auto.tick(now, age_seconds=lambda: age, collect_once=self.collect, data_age_seconds=lambda: data_age)

    def test_waiting_for_the_daily_load(self):
        at = DAY + timedelta(minutes=10)
        self.assertTrue(auto.waiting_for_daily_load(at, YESTERDAY_LOAD))
        self.assertFalse(auto.waiting_for_daily_load(at, 5 * 60))                             # today's load is in
        self.assertFalse(auto.waiting_for_daily_load(DAY + timedelta(minutes=3), YESTERDAY_LOAD))  # before 00:05
        for unknown in (None, -1, True, "x"):
            self.assertFalse(auto.waiting_for_daily_load(at, unknown), unknown)

    def test_catch_up_collects_every_15_min_not_every_minute(self):
        start = DAY + timedelta(minutes=10)
        data_age = lambda now: int((now - (DAY - timedelta(days=1))).total_seconds())  # newest change: yesterday 00:00
        self.assertEqual(self.tick(start, 20 * 60, data_age(start)), "refreshed")    # Redash result 20 min old: 45-min rule
        for minutes in range(1, 15):                                                  # would skip, catch-up collects once
            now = start + timedelta(minutes=minutes)
            self.assertEqual(self.tick(now, 20 * 60, data_age(now)), "skipped_fresh", minutes)
        now = start + timedelta(minutes=15)
        self.assertEqual(self.tick(now, 20 * 60, data_age(now)), "refreshed")
        self.assertEqual(self.calls, 2)

    def test_once_today_is_in_the_45_min_rule_is_back(self):
        now = DAY + timedelta(hours=1)
        self.assertEqual(self.tick(now, 20 * 60, 3600), "skipped_fresh")
        self.assertEqual(self.tick(now + timedelta(minutes=30), 50 * 60, 3600 + 1800), "refreshed")
        self.assertEqual(self.calls, 1)

    def test_without_a_data_age_reader_nothing_changes(self):
        now = DAY + timedelta(minutes=10)
        self.assertEqual(auto.tick(now, age_seconds=lambda: 20 * 60, collect_once=self.collect), "skipped_fresh")
        self.assertEqual(self.calls, 0)

    def test_a_failing_data_age_reader_is_ignored(self):
        def broken():
            raise OSError("unreadable")
        now = DAY + timedelta(minutes=10)
        self.assertEqual(auto.tick(now, age_seconds=lambda: 20 * 60, collect_once=self.collect, data_age_seconds=broken),
                         "skipped_fresh")

    def test_backoff_still_wins(self):
        def fail():
            raise RuntimeError("down")
        now = DAY + timedelta(minutes=10)
        self.assertEqual(auto.tick(now, age_seconds=lambda: None, collect_once=fail, data_age_seconds=lambda: YESTERDAY_LOAD),
                         "failed")
        self.assertEqual(self.tick(now + timedelta(minutes=2), None, YESTERDAY_LOAD), "skipped_backoff")


class CloneStripTests(unittest.TestCase):
    def strip(self, info, waiting, vm=False, enabled=True):
        with patch.object(dashboard, "_clone_status", return_value=info), \
                patch.object(auto, "waiting_for_daily_load", return_value=waiting), \
                patch.object(auto, "enabled", return_value=enabled), \
                patch.object(dashboard, "vm_mode", return_value=vm):
            return dashboard._clone_strip()

    INFO = {"state": "fresh", "age_seconds": 600, "data_age_seconds": 25 * 3600, "limit_seconds": 21600, "reason": ""}

    def test_the_strip_names_the_region_and_the_catch_up(self):
        from integration.tests.test_us_clone_2026_11_09 import unpinned

        with unpinned():  # US query switched off: the strip says EU only (pinned: test_us_clone_2026_11_09)
            html = self.strip(self.INFO, waiting=True)
        self.assertIn("Covers EU (app.pentera.io) only", html)
        self.assertIn("clone load (about 00:00 UTC) is not in this data yet; the dashboard reads Redash every 15 min", html)

    def test_the_vm_does_not_promise_a_refresh(self):
        html = self.strip(self.INFO, waiting=True, vm=True)
        self.assertIn("is not in this data yet.", html)
        self.assertNotIn("every 15 min", html)

    def test_no_note_once_today_is_in(self):
        self.assertNotIn("not in this data yet", self.strip(self.INFO, waiting=False))


if __name__ == "__main__":
    unittest.main()


class ChangeWatchTests(unittest.TestCase):
    """Owner report 2026-11-05 (CO-0839): approved in Salesforce, the dashboard kept the cached "Pending"."""

    NOW = datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)

    def setUp(self):
        dashboard._reset_change_watch()
        dashboard.clear_display_cache()
        self.addCleanup(dashboard._reset_change_watch)
        self.addCleanup(dashboard.clear_display_cache)

    def seed(self, *keys):
        from concurrent.futures import Future
        from time import monotonic
        for key in keys:
            future = Future()
            future.set_result({"cached": True})
            dashboard._display_cache[key] = (monotonic() + 300, 0.0, future)

    def check(self, records, now=None, interval=0.0):
        reply = {"status": 0, "result": {"records": records}}
        with patch.object(dashboard, "sf_json", return_value=reply) as read, \
                patch.object(dashboard, "vm_mode", return_value=False):
            changed = dashboard.refresh_changed_cos(interval, now or self.NOW)
        return changed, read

    def test_a_changed_co_and_the_queue_lists_are_dropped_others_kept(self):
        self.seed(("detail_row", "CO-0839"), ("detail_row", "CO-0835"), ("queue_source_rows",))
        first, read = self.check([{"Name": "CO-0839", "LastModifiedDate": "2026-11-05T18:30:00.000+0000"}])
        self.assertEqual(first, ["CO-0839"])  # the cache may hold it from before: dropped on the first check too
        query = read.call_args[0][0][3]
        self.assertIn("SELECT Name, LastModifiedDate FROM Customer_Onboarding__c WHERE LastModifiedDate >= 2026-11-05T", query)
        self.assertNotIn(("detail_row", "CO-0839"), dashboard._display_cache)
        self.assertNotIn(("queue_source_rows",), dashboard._display_cache)
        self.assertIn(("detail_row", "CO-0835"), dashboard._display_cache)

    def test_an_unchanged_timestamp_drops_nothing_and_a_new_one_does(self):
        record = {"Name": "CO-0839", "LastModifiedDate": "2026-11-05T18:30:00.000+0000"}
        self.check([record])
        self.seed(("detail_row", "CO-0839"))
        self.assertEqual(self.check([record], self.NOW + timedelta(minutes=1))[0], [])
        self.assertIn(("detail_row", "CO-0839"), dashboard._display_cache)
        newer = {**record, "LastModifiedDate": "2026-11-05T19:00:30.000+0000"}
        self.assertEqual(self.check([newer], self.NOW + timedelta(minutes=2))[0], ["CO-0839"])
        self.assertNotIn(("detail_row", "CO-0839"), dashboard._display_cache)

    def test_later_checks_ask_only_since_the_last_one_with_overlap(self):
        self.check([])
        _changed, read = self.check([], self.NOW + timedelta(minutes=1))
        self.assertIn("LastModifiedDate >= 2026-11-05T18:58:00Z", read.call_args[0][0][3])

    def test_the_interval_limits_reads(self):
        self.check([])
        _changed, read = self.check([], self.NOW + timedelta(seconds=10), interval=15)
        read.assert_not_called()

    def test_failure_or_bad_rows_change_nothing(self):
        self.seed(("detail_row", "CO-0839"))
        with patch.object(dashboard, "sf_json", side_effect=dashboard.ReadUnavailable()), \
                patch.object(dashboard, "vm_mode", return_value=False):
            self.assertEqual(dashboard.refresh_changed_cos(0, self.NOW), [])
        self.assertIsNone(dashboard._change_state["checked_at"])  # a failed read is retried next time
        bad = [{"Name": "CO-0839' OR", "LastModifiedDate": "2026-11-05T18:30:00.000+0000"},
               {"Name": "CO-0839", "LastModifiedDate": "yesterday"}]
        self.assertEqual(self.check(bad)[0], [])
        self.assertIn(("detail_row", "CO-0839"), dashboard._display_cache)

    def test_never_on_the_vm(self):
        with patch.object(dashboard, "sf_json") as read, patch.object(dashboard, "vm_mode", return_value=True):
            self.assertEqual(dashboard.refresh_changed_cos(0, self.NOW), [])
        read.assert_not_called()

    def test_both_co_pages_check_before_rendering(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertIn("if review:\n                refresh_changed_cos(CHANGE_CHECK_PAGE_SECONDS)", source)
        self.assertIn("if match:\n                refresh_changed_cos(CHANGE_CHECK_PAGE_SECONDS)", source)
        self.assertIn("if refresh_changed_cos(CHANGE_CHECK_LOOP_SECONDS):", source)


class HeaderPhaseTests(unittest.TestCase):
    """Owner report 2026-11-05 (CO-0839): the header said "Create blocked" while the readiness card said "Create ready"."""

    def card(self, approval, statuses):
        items = [{"label": "x" + str(index), "detail": "d", "status": status} for index, status in enumerate(statuses)]
        shown = {}
        with patch.object(dashboard.production_views, "onboard_checklist", return_value=items), \
                patch.object(dashboard.production_views, "create_records_by_co", return_value={"state": "ok", "by_co": {}}), \
                patch.object(dashboard.production_views, "backoffice_session_status", return_value=None), \
                patch.object(dashboard.production_views, "approval_for_co", return_value=None), \
                patch.object(dashboard.production_views, "create_state", return_value=None), \
                patch.object(dashboard, "_clone_status", return_value={}), \
                patch.object(dashboard, "vm_mode", return_value=False):
            html = dashboard._co_ready_card("CO-0839", {"Name": "CO-0839", "Onboarding_Approval_Status__c": approval},
                                            match=None, domain_count=1, start_iso="2026-11-01", ce_route=True,
                                            state={"phase": "blocked", "reason": "approval_missing", "text": "t"},
                                            phase_out=shown)
        return shown["phase"], html

    def test_approved_and_all_ready_is_ready_in_card_and_header(self):
        phase, html = self.card("Approved", ["ready", "ready"])
        self.assertEqual(phase, "ready")
        self.assertIn("Create ready", html)

    def test_pending_or_a_blocked_row_stays_blocked(self):
        self.assertEqual(self.card("Pending", ["ready"])[0], "blocked")
        self.assertEqual(self.card("Approved", ["ready", "blocked"])[0], "blocked")

    def test_the_header_uses_the_card_phase(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertIn('head += _chip(*CREATE_PHASE_CHIPS[shown.get("phase", state["phase"])])', source)
