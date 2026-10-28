"""Owner request 2026-10-28: menus and CO pages took several seconds (a cold queue 77 s) after a restart.

Each `sf` CLI read costs ~5 s on the PC and the 17-23 MB clone was parsed and SHA-256 re-checked 4-6 times per page. Covered
here: the display-only clone memo (creates and writes always re-verify), stale-while-revalidate display reads, the background
warm-up, per-CO cache clearing, sign-in POSTs keeping the cache, the auto-scan pass loading the clone once and holding a CO
moved by hand, and the match rule for a UUID the clone does not hold yet (CO-0824 / CO-0813). No network, no `sf` CLI.
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timedelta, timezone
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import tools.redash_inventory_collector as collector
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import production_match as pm
from integration.tests.test_identity_match import IDENTITY, OTHER, identity
from integration.tests.test_production_match import CO, PLAN, snapshot, tenant
from integration.tests.test_redash_inventory import FAKE_KEY, GET, FakeOpener, result, row as clone_row

CAPTURED = datetime(2026, 10, 16, 9, 50, tzinfo=timezone.utc)


def in_display(fn, *args):
    """Run ``fn`` as a display GET would (the cache and the clone memo apply only then)."""
    def run():
        dashboard._display_reads.set(True)
        return fn(*args)
    return contextvars.copy_context().run(run)


def drain():
    """Wait for the background display reads started so far."""
    for pool in (dashboard._display_prefetch_pool, dashboard._display_warm_pool):
        pool.submit(lambda: None).result(timeout=10)
    deadline = time.monotonic() + 5
    while dashboard._display_refreshing and time.monotonic() < deadline:
        time.sleep(0.01)


class CloneMemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        collector.collect(FakeOpener({GET: result([clone_row(1, _modified="2026-10-16T09:40:00.000000")], "2026-10-16T09:50:00")}),
                          FAKE_KEY, root=self.root, now=CAPTURED + timedelta(minutes=5))
        inventory._memo.clear()
        self.addCleanup(inventory._memo.clear)
        self.now = CAPTURED + timedelta(hours=1)

    def load(self, display, max_age=timedelta(hours=6)):
        with mock.patch.object(inventory, "MEMO_WHEN", lambda: display):
            return inventory.load_latest(self.root, "prod-clone", max_age=max_age, now=self.now)

    def test_display_reuses_the_verified_clone_until_its_files_change(self):
        with mock.patch.object(inventory, "_rows_sha256", wraps=inventory._rows_sha256) as digest:
            first, second = self.load(True), self.load(True)
            self.assertEqual(digest.call_count, 1)
            self.assertEqual(first["tenants"], second["tenants"])
            self.assertEqual(second["age_seconds"], 3600)
            latest = self.root / "prod-clone" / "latest.json"
            stamp = latest.stat().st_mtime_ns + 5_000_000_000
            import os
            os.utime(latest, ns=(stamp, stamp))
            self.load(True)
            self.assertEqual(digest.call_count, 2)  # a changed file is verified again

    def test_creates_and_writes_always_verify(self):
        with mock.patch.object(inventory, "_rows_sha256", wraps=inventory._rows_sha256) as digest:
            self.load(True)
            self.load(False)
            self.load(False)
            self.assertEqual(digest.call_count, 3)

    def test_the_age_is_still_checked_on_a_memo_hit(self):
        self.load(True)
        with self.assertRaises(inventory.InventoryError) as error:
            self.load(True, max_age=timedelta(minutes=10))
        self.assertEqual(error.exception.reason, "inventory_snapshot_stale")

    def test_a_tampered_snapshot_is_never_memoised(self):
        snap = next((self.root / "prod-clone").glob("redash-*.json"))
        snap.write_text(snap.read_text(encoding="utf-8").replace('"row_count": 1', '"row_count": 2'), encoding="utf-8")
        for _ in range(2):
            with self.assertRaises(inventory.InventoryError):
                self.load(True)
        self.assertEqual(inventory._memo, {})

    def test_the_dashboard_turns_the_memo_on_only_for_display_reads(self):
        self.assertEqual(inventory.MEMO_WHEN, dashboard._display_reads.get)
        self.assertFalse(inventory.MEMO_WHEN())
        self.assertTrue(in_display(lambda: inventory.MEMO_WHEN()))


class StaleWhileRevalidateTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        self.calls = 0
        self.clock = [1000.0]
        patcher = mock.patch.object(dashboard, "monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def counter(self):
        def read_value(ref):
            self.calls += 1
            return {"ref": ref, "n": self.calls}
        return dashboard._display_cached(read_value)

    def test_an_expired_value_is_served_at_once_and_re_read_in_the_background(self):
        read = self.counter()
        self.assertEqual(in_display(read, "CO-1")["n"], 1)
        self.clock[0] += dashboard.DISPLAY_READ_TTL_SECONDS + 1
        self.assertEqual(in_display(read, "CO-1")["n"], 1)  # no wait on the CLI
        drain()
        self.assertEqual(in_display(read, "CO-1")["n"], 2)
        self.assertEqual(self.calls, 2)

    def test_too_old_values_are_read_on_the_page(self):
        read = self.counter()
        in_display(read, "CO-1")
        self.clock[0] += dashboard.DISPLAY_STALE_MAX_SECONDS + 1
        self.assertEqual(in_display(read, "CO-1")["n"], 2)

    def test_outside_a_display_get_the_read_is_always_fresh(self):
        read = self.counter()
        in_display(read, "CO-1")
        self.assertEqual(read("CO-1")["n"], 2)
        self.assertEqual(read("CO-1")["n"], 3)

    def test_a_background_read_started_before_a_clear_never_stores(self):
        generation = dashboard._display_generation
        dashboard.clear_display_cache()
        dashboard._store_display_value(("read_value", "CO-1"), {"n": 99}, generation)
        self.assertIsNone(dashboard.peek_display_cache("read_value", "CO-1"))

    def test_a_new_data_stamp_is_never_served_the_previous_match(self):
        def production_match_display(ref, stamp):
            self.calls += 1
            return {"stamp": stamp}
        read = dashboard._display_cached(production_match_display)
        self.assertEqual(in_display(read, "CO-1", "s1"), {"stamp": "s1"})
        self.assertEqual(in_display(read, "CO-1", "s2"), {"stamp": "s2"})
        self.assertIsNone(dashboard.peek_display_cache("production_match_display", "CO-1", "s1"))


class ClearTests(unittest.TestCase):
    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)

    def put(self, *key):
        dashboard._store_display_value(key, {"v": 1}, dashboard._display_generation)

    def test_a_write_to_one_co_clears_only_that_co_and_the_queue_lists(self):
        for key in (("detail_row", "CO-1"), ("detail_row", "CO-2"), ("production_match_display", "CO-1", "", "", "s"),
                    ("queue_source_rows",), ("queue_start_dates", ("CO-1", "CO-2"))):
            self.put(*key)
        dashboard.clear_display_cache_for("CO-1")
        self.assertIsNotNone(dashboard.peek_display_cache("detail_row", "CO-2"))
        for key in (("detail_row", "CO-1"), ("production_match_display", "CO-1", "", "", "s"), ("queue_source_rows",),
                    ("queue_start_dates", ("CO-1", "CO-2"))):
            self.assertIsNone(dashboard.peek_display_cache(*key), key)
        self.assertTrue(dashboard.DISPLAY_WARM_WAKE.is_set())

    def test_sign_in_and_session_posts_keep_the_cache_every_other_post_clears_it(self):
        self.assertTrue({"/attended/prepare-sessions", "/attended/session-recheck", "/login/start"}
                        <= dashboard._POSTS_KEEPING_DISPLAY_CACHE)
        for path in ("/attended/production-create", "/attended/production-scan-check", "/attended/onboard-approve"):
            self.assertNotIn(path, dashboard._POSTS_KEEPING_DISPLAY_CACHE)


class WarmUpTests(unittest.TestCase):
    ROWS = [{"Name": "CO-0001", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding"},
            {"Name": "CO-0002", "Onboarding_Product__c": "Surface", "Onboarding_Type__c": "New Product Onboarding"},
            {"Name": "bad", "Onboarding_Product__c": None, "Onboarding_Type__c": None}]

    def setUp(self):
        dashboard.clear_display_cache()
        self.addCleanup(dashboard.clear_display_cache)
        self.reads: list[tuple] = []
        lock = threading.Lock()

        def fake(name, value):
            def read(*args):
                with lock:
                    self.reads.append((name, *args))
                return value(*args)
            read.__name__ = name
            return dashboard._display_cached(read)
        patches = {"queue_source_rows": fake("queue_source_rows", lambda: [dict(r) for r in self.ROWS]),
                   "queue_start_dates": fake("queue_start_dates", lambda refs: {ref: "2026-11-01" for ref in refs}),
                   "production_match_display": fake("production_match_display", lambda *a: {"status": "not_in_production"}),
                   "detail_row": fake("detail_row", lambda ref: {"Name": ref}),
                   "_match_stamp": lambda: "stamp", "vm_mode": lambda: False}
        patcher = mock.patch.multiple(dashboard, **patches)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_pass_warms_the_queue_and_every_co(self):
        self.assertEqual(dashboard._display_warm_tick(), 2)
        self.assertIsNotNone(dashboard.peek_display_cache("queue_source_rows"))
        self.assertIsNotNone(dashboard.peek_display_cache("queue_start_dates", ("CO-0001", "CO-0002")))
        for ref, product in (("CO-0001", "Credential Exposure"), ("CO-0002", "Surface")):
            self.assertIsNotNone(dashboard.peek_display_cache("production_match_display", ref, product,
                                                              "New Product Onboarding", "stamp"))
            self.assertIsNotNone(dashboard.peek_display_cache("detail_row", ref))
        count = len(self.reads)
        dashboard._display_warm_tick()
        self.assertEqual(len(self.reads), count)  # still fresh: nothing re-read

    def test_never_on_the_vm(self):
        with mock.patch.object(dashboard, "vm_mode", lambda: True):
            self.assertEqual(dashboard._display_warm_tick(), 0)
        self.assertEqual(self.reads, [])

    def test_a_new_clone_wakes_the_warm_up_before_the_interval(self):
        stamps = iter(["s1", "s1", "s2"])
        with mock.patch.object(dashboard, "_match_stamp", lambda: next(stamps)), \
                mock.patch.object(dashboard, "DISPLAY_WARM_STAMP_POLL_SECONDS", 0.01):
            dashboard.DISPLAY_WARM_WAKE.clear()
            started = time.monotonic()
            dashboard._wait_for_warm_reason(threading.Event())
        self.assertLess(time.monotonic() - started, 5)

    def test_the_loop_stops_on_request(self):
        stop = threading.Event()
        stop.set()
        dashboard.DISPLAY_WARM_WAKE.set()
        with mock.patch.object(dashboard, "_display_warm_tick") as tick:
            dashboard._display_warm_loop(stop)
        tick.assert_called_once()


class DisplayProbeTests(unittest.TestCase):
    def test_a_page_never_runs_the_slow_salesforce_probe_the_create_post_does(self):
        old = dashboard.SessionStatus(dashboard.SessionState.READY, "salesforce_ready",
                                      dashboard.readiness_now() - timedelta(hours=2))
        with mock.patch.object(dashboard, "session_statuses", return_value={"salesforce": old}), \
                mock.patch.object(dashboard, "probe_salesforce_readiness") as probe:
            probe.return_value = dashboard.SessionStatus(dashboard.SessionState.EXPIRED, "x", dashboard.readiness_now())
            self.assertTrue(in_display(dashboard._salesforce_ready_for_create))
            probe.assert_not_called()
            self.assertFalse(dashboard._salesforce_ready_for_create())
            probe.assert_called_once()


class AutoScanPassTests(unittest.TestCase):
    def setUp(self):
        dashboard.AUTO_SCAN_STATE.clear()
        self.addCleanup(dashboard.AUTO_SCAN_STATE.clear)

    def run_tick(self, results):
        loads = []

        def finish(inputs, load_clone, *_rest):
            load_clone()
            return results[inputs]
        with mock.patch.multiple(dashboard, vm_mode=lambda: False, production_records_parent=lambda: Path("."),
                                 _create_ui_inputs=lambda ref, row: ref,
                                 _clone_payload_for_job=lambda: loads.append(1) or {"tenants": []}), \
                mock.patch.object(dashboard.create_ui, "auto_scan_candidates", return_value=sorted(results)), \
                mock.patch.object(dashboard.create_ui, "auto_scan_finish", side_effect=finish), \
                mock.patch.object(dashboard, "clear_display_cache_for") as cleared:
            dashboard._auto_scan_tick()
        return loads, cleared

    def test_one_clone_load_per_pass_and_a_moved_stage_clears_only_that_co(self):
        loads, cleared = self.run_tick({"CO-1": {"scan": "finished", "stages": ["written_verified"] * 3},
                                        "CO-2": {"scan": "running", "stages": []}})
        self.assertEqual(len(loads), 1)
        cleared.assert_called_once_with("CO-1")

    def test_a_co_moved_by_hand_is_held_for_a_day(self):
        self.run_tick({"CO-1": {"scan": "finished", "stages": ["stage_not_expected"]}})
        self.assertGreater(dashboard.AUTO_SCAN_STATE["CO-1"]["hold_until"],
                           datetime.now(timezone.utc).timestamp() + dashboard.AUTO_SCAN_HOLD_SECONDS - 60)
        loads, _ = self.run_tick({"CO-1": {"scan": "finished", "stages": ["written_verified"]}})
        self.assertEqual(loads, [])  # held: not looked at again


class UuidNotInCloneRuleTests(unittest.TestCase):
    """A UUID the clone does not hold yet plus only non-live name matches: "UUID set, not in clone", not "Other tenants only"."""

    def test_identity_match(self):
        old = tenant("Acme", "acme.example", enabled=False)
        found = pm.identity_match(identity(uuid=OTHER), snapshot(old), renewal=False)
        self.assertEqual(found["status"], "uuid_not_in_clone")
        self.assertEqual(len(found["candidates"]), 1)
        self.assertEqual(pm.identity_match(IDENTITY, snapshot(old), renewal=False)["status"], "exists_other")  # no UUID: unchanged

    def test_a_live_paid_name_match_still_blocks_as_a_duplicate(self):
        live = tenant("Acme", "acme.example")
        self.assertNotEqual(pm.identity_match(identity(uuid=OTHER), snapshot(live), renewal=False)["status"], "uuid_not_in_clone")
        self.assertNotEqual(pm.production_match(CO, PLAN, snapshot(live), account_uuid=OTHER)["status"], "uuid_not_in_clone")

    def test_production_match(self):
        old = tenant("Acme", "acme.example", deleted=True)
        self.assertEqual(pm.production_match(CO, PLAN, snapshot(old), account_uuid=OTHER)["status"], "uuid_not_in_clone")
        self.assertEqual(pm.production_match(CO, PLAN, snapshot(old))["status"], "exists_other")


if __name__ == "__main__":
    unittest.main()
