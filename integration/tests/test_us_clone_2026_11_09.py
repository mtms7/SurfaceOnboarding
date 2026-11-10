"""Owner request 2026-11-09 ("implement the US tenant clone"): the US production clone (Redash "Prod US (Cloned) - Mgmt",
data source 14) beside the EU one.

* Own snapshot environment ``prod-clone-us`` and file prefix ``redash-us-clone`` (never ``redash-prod-clone-us``).
* ONE pinned US saved query (``redash_inventory.US_QUERY_ID``); until it is set every US collect fails closed and US COs stay
  "Region US: not checked" exactly as before.
* Once pinned: ``--region us`` collects into the US folder only; a US CO is matched against the US clone; a missing, stale or
  wrong-region US clone blocks (``clone_unavailable``, reason ``us_clone_*``); an EU CO is never cleared by the US clone.

Hermetic: fake rows, fake key, temporary folders; no network, Redash, Salesforce or BackOffice.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.clone_autorefresh as autorefresh
import tools.redash_inventory_collector as collector
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import production_match as match_module
from integration.onboarding import production_views as views
from integration.onboarding import redash_inventory as redash
from integration.tests import test_redash_inventory as base
from integration.tests import test_redash_v2 as v2

NOW = v2.NOW
FAKE_KEY = base.FAKE_KEY
US_ID = 257  # the pinned US query (created with the owner's go 2026-11-09)
US_PATH = ("GET", f"/api/queries/{US_ID}/results.json")


def pinned():
    """The shipped state: query 257 pinned (kept as a context so each test states which state it needs)."""
    return contextlib.nullcontext()


def unpinned():
    """The US clone switched off (``US_QUERY_ID = None``): only the EU profiles remain."""
    eu_only = {query_id: profile for query_id, profile in redash.PROFILES.items() if profile.region == "EU"}
    return patch.dict(redash.PROFILES, eu_only, clear=True)


def collect_us(root: Path, rows=2):
    return collector.collect(base.FakeOpener({US_PATH: v2.reply([v2.row253(i) for i in range(1, rows + 1)])}), FAKE_KEY,
                             root=root, now=NOW, refresh=False, query_id=US_ID)


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)


class PinTests(unittest.TestCase):
    def test_query_257_is_the_pinned_us_query(self):
        self.assertEqual(redash.US_QUERY_ID, 257)
        profile = redash.profile_for(257)
        self.assertEqual((profile.region, profile.row_schema, profile.known), ("US", "v2", redash.KNOWN_COLUMNS_V2))
        self.assertEqual(redash.region_query_id("US"), 257)
        self.assertEqual(redash.region_query_id("EU"), 253)
        self.assertEqual(match_module.clone_regions(), ("EU", "US"))
        self.assertTrue(autorefresh.us_enabled())
        self.assertEqual(collector.key_path(257).name, "query-257.key")
        self.assertEqual(collector.key_file_env(257), "SURFACE_REDASH_KEY_FILE_257")


class NotPinnedTests(unittest.TestCase):
    def test_us_off_when_unpinned(self):
        with unpinned():
            self.assertFalse(redash.region_configured("US"))
            self.assertTrue(redash.region_configured("EU"))
            self.assertEqual(match_module.clone_regions(), ("EU",))
            self.assertFalse(autorefresh.us_enabled())

    def test_us_collect_fails_closed(self):
        with unpinned():
            with self.assertRaises(collector.CollectorError) as caught:
                collector.resolve_query_id(None, region="US")
            self.assertEqual(caught.exception.reason, "redash_us_query_not_configured")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = collector.main(["--collect", "--region", "us"])
        self.assertEqual((code, json.loads(out.getvalue())), (1, {"result": "redash_us_query_not_configured"}))

    def test_eu_selection_is_unchanged(self):
        self.assertEqual(collector.resolve_query_id(None, environ={}), 251)
        self.assertEqual(collector.resolve_query_id(253, environ={}), 253)
        self.assertEqual(collector.environment_for(253), "prod-clone")
        self.assertEqual(collector.environment_for(251), "prod-clone")


class SelectionTests(unittest.TestCase):
    def test_pinned_us_query_is_the_only_us_query(self):
        with pinned():
            self.assertEqual(collector.resolve_query_id(None, region="US"), US_ID)
            self.assertEqual(collector.resolve_query_id(US_ID, region="US"), US_ID)
            self.assertEqual(collector.environment_for(US_ID), "prod-clone-us")
            for wrong, region in ((253, "US"), (251, "US"), (US_ID, "EU")):
                with self.assertRaises(collector.CollectorError) as caught:
                    collector.resolve_query_id(wrong, environ={}, region=region)
                self.assertEqual(caught.exception.reason, "redash_query_region_mismatch", (wrong, region))
            # the EU environment variable never selects the US query
            with self.assertRaises(collector.CollectorError):
                collector.resolve_query_id(None, environ={collector.QUERY_ID_ENV: str(US_ID)}, region="EU")

    def test_unknown_region_refused(self):
        with self.assertRaises(collector.CollectorError) as caught:
            collector.resolve_query_id(None, region="IL")
        self.assertEqual(caught.exception.reason, "redash_region_unsupported")

    def test_environment_and_prefix(self):
        env = inventory.require_environment("prod-clone-us")
        self.assertEqual((env.file_prefix, env.origin), ("redash-us-clone", "https://redash.pentera.io"))
        self.assertFalse(env.file_prefix.startswith("redash-prod-clone"))


class CollectTests(TempRoot):
    def test_us_collect_writes_only_the_us_folder(self):
        with pinned():
            report = collect_us(self.root)
            self.assertEqual((report["result"], report["environment"], report["row_count"]),
                             ("inventory_collected", "prod-clone-us", 2))
            self.assertTrue(report["file"].startswith("redash-us-clone-inventory-"))
            self.assertNotIn(FAKE_KEY, json.dumps(report))
            self.assertFalse((self.root / "prod-clone").exists())
            loaded = inventory.load_latest(self.root, "prod-clone-us", max_age=timedelta(days=1), now=NOW)
            self.assertEqual(loaded["source"]["data_source"], "Prod US (Cloned) - Mgmt")
            self.assertEqual(redash.snapshot_region(loaded), "US")
            self.assertEqual(redash.validate_row_schema(loaded), "v2")
            payload = match_module.load_snapshot(self.root, NOW, max_age=timedelta(days=1), region="US")
            self.assertIsInstance(payload, dict)
            self.assertEqual(match_module.load_snapshot(self.root, NOW, max_age=timedelta(days=1)),
                             "inventory_snapshot_missing")  # EU untouched

    def test_eu_collect_does_not_touch_us(self):
        v2.snapshot_253(self.root, 2)
        self.assertFalse((self.root / "prod-clone-us").exists())
        loaded = inventory.load_latest(self.root, "prod-clone", max_age=timedelta(days=1), now=NOW)
        self.assertEqual(redash.snapshot_region(loaded), "EU")
        self.assertEqual(loaded["source"]["data_source"], "Prod (Cloned) - Mgmt")

    def test_us_shrink_is_judged_against_the_us_snapshot(self):
        with pinned():
            collect_us(self.root, rows=10)
            with self.assertRaises(collector.CollectorError) as caught:
                collect_us(self.root, rows=5)
            self.assertEqual(caught.exception.reason, "redash_result_shrunk")


class RegionConsistencyTests(TempRoot):
    def test_snapshot_region_rules(self):
        self.assertEqual(redash.snapshot_region({"environment": "prod-clone", "source": {"query_id": 253}}), "EU")
        self.assertEqual(redash.snapshot_region({"environment": "prod-clone"}), "EU")  # legacy, no recorded query
        self.assertIsNone(redash.snapshot_region({"environment": "prod-clone-us", "source": {"query_id": 253}}))
        self.assertIsNone(redash.snapshot_region({"environment": "prod-clone-us"}))
        self.assertIsNone(redash.snapshot_region({"environment": "prod-clone", "source": {"query_id": US_ID}}))
        self.assertEqual(redash.snapshot_region({"environment": "prod-clone-us", "source": {"query_id": US_ID}}), "US")
        self.assertIsNone(redash.snapshot_region({"environment": "prod-clone-us", "source": {"query_id": 9999}}))
        with unpinned():
            self.assertIsNone(redash.snapshot_region({"environment": "prod-clone-us", "source": {"query_id": US_ID}}))
        self.assertIsNone(redash.snapshot_region("x"))

    def test_us_snapshot_that_unpinned_code_cannot_place_is_refused(self):
        with pinned():
            collect_us(self.root)
        with unpinned():  # the US query id is no longer pinned: the stored US snapshot cannot be tied to a US query
            self.assertEqual(match_module.load_snapshot(self.root, NOW, max_age=timedelta(days=1), region="US"),
                             "us_clone_inventory_snapshot_wrong_region")
            self.assertEqual(views.clone_status(self.root, NOW, region="US")["reason"], "inventory_snapshot_wrong_region")

    def test_missing_us_clone_reason_is_prefixed(self):
        self.assertEqual(match_module.load_snapshot(self.root, NOW, region="US"), "us_clone_inventory_snapshot_missing")
        self.assertEqual(match_module.load_snapshot(self.root, NOW, region="IL"), "il_clone_region_unsupported")


class ApplyRegionTests(unittest.TestCase):
    def test_us_clone_decides_a_us_co(self):
        clear = {"status": "not_in_production", "reason": ""}
        self.assertIs(match_module.apply_region(clear, "US", "US"), clear)

    def test_us_clone_never_clears_an_eu_or_il_co(self):
        for value in (None, "", "EU", "IL"):
            result = match_module.apply_region({"status": "not_in_production", "reason": ""}, value, "US")
            self.assertEqual(result["status"], "region_not_checked", value)
            self.assertIn("US tenants only", result["reason"])

    def test_eu_default_is_unchanged(self):
        result = match_module.apply_region({"status": "not_in_production", "reason": ""}, "US")
        self.assertIn("EU tenants only (https://app.pentera.io)", result["reason"])


class RunnerTests(unittest.TestCase):
    def run_for(self, region, eu_status="not_in_production", us_snapshot=None, us_status="not_in_production"):
        identity = {"names": ("Ion Example",), "domains": ("ion.example",), "uuid": None, "region": region}
        eu_snap = {"tenants": [], "captured_at": None, "clone": "EU"}
        us_snap = us_snapshot if us_snapshot is not None else {"tenants": [], "captured_at": None, "clone": "US"}

        def load(root, now, max_age=None, region="EU"):
            return us_snap if region == "US" else eu_snap

        def full(co_source, plan, snapshot, **_):
            if isinstance(snapshot, str):  # what the real production_match does with an unusable clone
                return {"status": "clone_unavailable", "reason": snapshot}
            return {"status": us_status if snapshot is us_snap else eu_status, "reason": "", "from": snapshot.get("clone")}

        self.loads = mock.Mock(side_effect=load)
        with patch.object(runner, "_production_expectation", return_value=("surface", {"s": 1}, {"p": 1})), \
                patch.object(runner, "_identity_for", return_value=identity), \
                patch.object(runner, "clone_root", return_value=Path("unused")), \
                patch.object(match_module, "load_snapshot", self.loads), \
                patch.object(match_module, "production_match", side_effect=full), \
                patch.object(runner, "_snapshot_account_uuid", return_value=None), \
                patch.object(runner, "_snapshot_identity", return_value=None), \
                patch.object(runner, "_apply_verified_tenant", side_effect=lambda result, *_: result), \
                patch("integration.onboarding.state_paths.vm_mode", return_value=False):
            return runner.production_match_for("CO-0835", "Surface", "New Product Onboarding")

    def test_unpinned_us_co_stays_not_checked(self):
        with unpinned():
            found = self.run_for("US")
        self.assertEqual((found["status"], found["region"]), ("region_not_checked", "US"))
        self.assertEqual([call.kwargs.get("region") for call in self.loads.call_args_list], ["EU"])

    def test_pinned_us_co_is_matched_against_the_us_clone(self):
        with pinned():
            found = self.run_for("US")
        self.assertEqual((found["status"], found["from"], found["clone_region"]), ("not_in_production", "US", "US"))
        self.assertEqual([call.kwargs.get("region") for call in self.loads.call_args_list], ["EU", "US"])

    def test_pinned_us_duplicate_blocks(self):
        with pinned():
            found = self.run_for("US", us_status="exists_other")
        self.assertEqual(found["status"], "exists_other")

    def test_pinned_missing_us_clone_blocks(self):
        with pinned():
            found = self.run_for("US", us_snapshot="us_clone_inventory_snapshot_missing")
        self.assertEqual((found["status"], found["reason"]), ("clone_unavailable", "us_clone_inventory_snapshot_missing"))

    def test_eu_co_never_reads_the_us_clone(self):
        with pinned():
            for region in (None, "", "EU"):
                found = self.run_for(region)
                self.assertEqual((found["status"], found["from"]), ("not_in_production", "EU"), region)
                self.assertNotIn("clone_region", found)
                self.assertEqual([call.kwargs.get("region") for call in self.loads.call_args_list], ["EU"])

    def test_il_co_stays_not_checked(self):
        with pinned():
            self.assertEqual(self.run_for("IL")["status"], "region_not_checked")


class StatusAndRefreshTests(TempRoot):
    def test_us_clone_status(self):
        self.assertEqual(views.clone_status(self.root, NOW, region="US")["reason"], "inventory_snapshot_missing")
        self.assertEqual(views.clone_status(self.root, NOW, region="IL")["reason"], "region_unsupported")
        with pinned():
            collect_us(self.root)
            info = views.clone_status(self.root, NOW, region="US")
        self.assertNotEqual(info["state"], "unusable")
        self.assertEqual(info["label"], "PROD US (cloned copy via Redash)")

    def test_us_refresher_is_separate_from_eu(self):
        autorefresh.reset()
        self.addCleanup(autorefresh.reset)
        result = autorefresh.tick(NOW, age_seconds=lambda: None, collect_once=lambda: {"result": "redash_unreachable"},
                                  refresher=autorefresh.US)
        self.assertEqual(result, "failed")
        self.assertEqual(autorefresh.status()["state"], "never")  # EU untouched
        self.assertIn("US clone auto-refresh failed", autorefresh.describe(autorefresh.US))
        self.assertEqual(autorefresh.describe(), "")
        # the EU refresher is not in backoff because the US one failed
        self.assertEqual(autorefresh.tick(NOW, age_seconds=lambda: None, collect_once=lambda: {"result": "inventory_collected"}),
                         "refreshed")

    def test_us_collect_once_fails_closed_when_unpinned(self):
        with unpinned(), self.assertRaises(collector.CollectorError) as caught:
            autorefresh.default_collect_once_us()
        self.assertEqual(caught.exception.reason, "redash_us_query_not_configured")


class DashboardTests(unittest.TestCase):
    def test_strip_says_eu_only_until_pinned(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard

        with unpinned():
            chip, note = dashboard._us_clone_chip()
        self.assertIn("Covers EU (app.pentera.io) only", chip)
        self.assertEqual(note, "")

    def test_strip_shows_us_state_once_pinned(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard

        with pinned(), patch.object(dashboard, "clone_root", return_value=Path(tempfile.mkdtemp())):
            chip, note = dashboard._us_clone_chip()
        self.assertIn("US clone unusable: inventory_snapshot_missing", chip)
        self.assertIn("--collect --region us", note)

    def test_us_clone_action(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard

        action = dashboard._prod_action({"status": "clone_unavailable", "reason": "us_clone_inventory_snapshot_stale"})
        self.assertIn("--region us", action)


if __name__ == "__main__":
    unittest.main()
