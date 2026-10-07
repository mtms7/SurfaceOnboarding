"""DEV mirror label/skip and the renewal outcome state file (owner follow-ups 2026-10-07).

No network, no browser: every file is a temporary one.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import renewal_mirror as mirror
import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

MIRROR_ID = "m" * 24
OTHER_ID = "o" * 24
UUID = "b" * 32


def record(**overrides) -> dict:
    return {"mirror_of_production": True, "source_prod_id": "p" * 24, "created_on": "2026-10-06", "status": "verified",
            "environment": "dev", "surface_account_id": MIRROR_ID, "account_uuid": UUID, **overrides}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)


class IsDevMirrorTests(unittest.TestCase):
    def test_matches_only_a_verified_dev_mirror_by_tenant_id(self):
        records = {"CO-0767": record()}
        self.assertTrue(mirror.is_dev_mirror(MIRROR_ID, records))
        self.assertFalse(mirror.is_dev_mirror(OTHER_ID, records))

    def test_unverified_or_foreign_records_never_match(self):
        for bad in (record(status="create_attempted"), record(mirror_of_production=False), record(environment="prod"),
                    record(surface_account_id=None), record(surface_account_id=""), "text", None):
            self.assertFalse(mirror.is_dev_mirror(MIRROR_ID, {"CO-0767": bad}), bad)

    def test_missing_id_or_unreadable_store_is_not_a_mirror(self):
        for tenant_id in (None, "", 7, ["x"]):
            self.assertFalse(mirror.is_dev_mirror(tenant_id, {"CO-0767": record()}))
        for records in (None, [], "x", {}):
            self.assertFalse(mirror.is_dev_mirror(MIRROR_ID, records))

    def test_the_skip_reason_is_shared(self):
        self.assertEqual(mirror.DEV_MIRROR_SKIP_REASON, runner.DEV_MIRROR_SKIP_REASON)


class ValidateSkipTests(TempDirCase):
    def setUp(self):
        super().setUp()
        (self.dir / "readbacks.json").write_text(json.dumps({
            "CO-0767": {"surface_account_id": MIRROR_ID, "account_uuid": UUID},
            "CO-0801": {"surface_account_id": OTHER_ID, "account_uuid": UUID}}), encoding="utf-8")
        for name, value in (("READBACK_PATH", self.dir / "readbacks.json"), ("MIRROR_PATH", self.dir / "mirrors.json")):
            patcher = patch.object(runner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_mirrors(self, payload=None, raw=None):
        (self.dir / "mirrors.json").write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")

    def test_validation_inputs_skip_a_mirror_before_any_source_read(self):
        self.write_mirrors({"CO-0767": record()})
        with patch.object(runner, "_validation_route", side_effect=AssertionError("no source read")):
            self.assertEqual(runner._validation_inputs("CO-0767"), "dev_mirror_skipped")

    def test_run_validate_all_reports_the_skip_and_still_validates_the_rest(self):
        self.write_mirrors({"CO-0767": record()})
        with patch.object(runner, "_validation_route", side_effect=OSError()), \
                patch.object(runner, "_record_check") as recorded:
            results = runner.run_validate_all()
        self.assertEqual(results, {"CO-0767": "dev_mirror_skipped", "CO-0801": "validation_source_unavailable"})
        recorded.assert_any_call("CO-0767", "validation", "dev_mirror_skipped")

    def test_sweep_from_inventory_reports_the_skip(self):
        self.write_mirrors({"CO-0767": record()})
        with patch.object(runner, "_validation_route", side_effect=OSError()), patch.object(runner, "_record_check"):
            results = runner._sweep_from_inventory(())
        self.assertEqual(results["CO-0767"], "dev_mirror_skipped")
        self.assertEqual(results["CO-0801"], "validation_source_unavailable")

    def test_missing_or_corrupt_record_file_never_skips(self):
        with patch.object(runner, "_validation_route", side_effect=OSError()):
            self.assertEqual(runner._validation_inputs("CO-0767"), "validation_source_unavailable")  # file absent
            self.write_mirrors(raw="{not json")
            self.assertEqual(runner._validation_inputs("CO-0767"), "validation_source_unavailable")
            self.write_mirrors(raw="[]")
            self.assertEqual(runner._validation_inputs("CO-0767"), "validation_source_unavailable")


class DashboardMirrorTests(TempDirCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(dashboard, "MIRROR_PATH", self.dir / "mirrors.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dev_mirror_ids_fail_open_to_none(self):
        self.assertEqual(dashboard.dev_mirror_ids(), frozenset())
        (self.dir / "mirrors.json").write_text("{bad", encoding="utf-8")
        self.assertEqual(dashboard.dev_mirror_ids(), frozenset())
        (self.dir / "mirrors.json").write_text(json.dumps({"CO-0767": record()}), encoding="utf-8")
        self.assertEqual(dashboard.dev_mirror_ids(), frozenset({MIRROR_ID}))

    def test_validation_card_is_neutral_for_a_mirror(self):
        card = dashboard._validation_section("CO-0767", mirror=True)
        self.assertIn("DEV mirror", card)
        self.assertIn("dev_mirror_skipped", card)
        self.assertNotIn("/attended/validate", card)  # no Validate button
        self.assertIn("/attended/validate", dashboard._validation_section("CO-0801"))

    def test_inventory_labels_only_the_mirror_tenant(self):
        (self.dir / "mirrors.json").write_text(json.dumps({"CO-0767": record()}), encoding="utf-8")
        tenants = [{"id": MIRROR_ID, "account_name": "A2A", "account_uuid": UUID, "enabled": True},
                   {"id": OTHER_ID, "account_name": "Other", "account_uuid": "c" * 32, "enabled": True}]
        payload = {"tenants": tenants, "age_seconds": 0, "captured_at": "2026-10-07T00:00:00+00:00",
                   "row_count": 2, "deleted_count": 0, "environment_label": "Leonardo Development"}
        with patch.object(dashboard.inventory, "load_latest", return_value=payload), \
                patch.object(dashboard, "attended_leonardo_readbacks", return_value={}):
            page = dashboard.render_inventory()
        self.assertEqual(page.count("DEV mirror"), 1)
        row = page[page.index(MIRROR_ID) - 200:page.index(MIRROR_ID) + 400]
        self.assertIn("DEV mirror", row)
        self.assertNotIn("DEV mirror", page[page.index(OTHER_ID):])


class RenewalOutcomeTests(TempDirCase):
    REPORT = {"engine": "case_6_renew_both", "mode": "dry_run", "gate": "production_clone_clear", "added_domains": 2,
              "changes": [{"field": "license_end", "kind": "date", "current": "2026-10-25", "target": "2029-09-29"},
                          {"field": "Number of assets", "kind": "number", "current": 5, "target": 9},
                          {"field": "alternateDomains", "kind": "list", "current": "1 item(s)", "target": "3 item(s)"}]}

    def setUp(self):
        super().setUp()
        self.path = self.dir / "outcomes.json"
        for target, name, value in ((runner, "RENEWAL_OUTCOMES_PATH", self.path), (dashboard, "RENEWAL_OUTCOMES_PATH", self.path),
                                    (runner, "READBACK_PATH", self.dir / "readbacks.json")):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.dir / "readbacks.json").write_text(json.dumps({
            "CO-0767": {"surface_account_id": MIRROR_ID, "account_uuid": UUID}}), encoding="utf-8")

    NOW = datetime(2026, 10, 7, 9, 30)

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_write_keeps_only_codes_dates_counts_and_the_tenant_id(self):
        runner.write_renewal_outcome("CO-0767", "renewal_dry_run_planned", False, "not_performed", self.NOW, MIRROR_ID,
                                     self.REPORT)
        self.assertEqual(self.stored(), {"CO-0767": {
            "result": "renewal_dry_run_planned", "mode": "dry_run", "leonardo_write": "not_performed",
            "observed_at": "2026-10-07T09:30:00", "route": "case_6_renew_both", "tenant_id": MIRROR_ID,
            "old_expiration": "2026-10-25", "new_expiration": "2029-09-29", "changes": 3, "added_domains": 2,
            "gate": "production_clone_clear"}})
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_latest_outcome_replaces_the_previous_one_per_co(self):
        runner.write_renewal_outcome("CO-0767", "renewal_dry_run_planned", False, "not_performed", self.NOW, MIRROR_ID,
                                     self.REPORT)
        runner.write_renewal_outcome("CO-0800", "renewal_tenant_not_found", False, "not_performed", self.NOW)
        runner.write_renewal_outcome("CO-0767", "renewal_edit_verified", True, "verified", self.NOW, MIRROR_ID, {})
        state = self.stored()
        self.assertEqual(set(state), {"CO-0767", "CO-0800"})
        self.assertEqual((state["CO-0767"]["result"], state["CO-0767"]["mode"], state["CO-0767"]["leonardo_write"]),
                         ("renewal_edit_verified", "confirm_write", "verified"))
        self.assertIsNone(state["CO-0767"]["new_expiration"])
        self.assertIsNone(state["CO-0800"]["tenant_id"])

    def test_untrusted_values_are_dropped_and_bad_input_is_refused(self):
        report = {"engine": "not_a_route", "gate": "has spaces <b>", "added_domains": True,
                  "changes": [{"field": "license_end", "current": "a@b.com", "target": "acme.com"}]}
        runner.write_renewal_outcome("CO-0767", "renewal_dry_run_planned", False, "not_performed", self.NOW, "bad id", report)
        entry = self.stored()["CO-0767"]
        self.assertEqual([entry[k] for k in ("route", "tenant_id", "gate", "added_domains", "old_expiration", "new_expiration")],
                         [None] * 6)
        for args in (("bad", "renewal_x", "not_performed"), ("CO-0767", "Bad Result", "not_performed"),
                     ("CO-0767", "renewal_x", "done")):
            with self.assertRaises(ValueError):
                runner.write_renewal_outcome(args[0], args[1], False, args[2], self.NOW)

    def test_corrupt_file_is_never_overwritten(self):
        for raw in ("{not json", "[]"):
            self.path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.write_renewal_outcome("CO-0767", "renewal_x", False, "not_performed", self.NOW)
            self.assertEqual(self.path.read_text(encoding="utf-8"), raw)
            runner.record_renewal_outcome("CO-0767", "renewal_x", False, {})  # best effort: no raise, no change
            self.assertEqual(self.path.read_text(encoding="utf-8"), raw)

    def test_record_uses_the_readback_id_and_the_write_label(self):
        runner.record_renewal_outcome("CO-0767", "renewal_saved_unverified", True, self.REPORT)
        entry = self.stored()["CO-0767"]
        self.assertEqual((entry["tenant_id"], entry["leonardo_write"], entry["mode"]),
                         (MIRROR_ID, "attempted_unverified", "confirm_write"))

    def test_dashboard_reader_drops_malformed_entries_and_a_corrupt_file_counts_as_none(self):
        good = {"result": "renewal_dry_run_planned", "mode": "dry_run", "leonardo_write": "not_performed",
                "observed_at": "2026-10-07T09:30:00", "old_expiration": "2026-10-25", "new_expiration": "2029-09-29",
                "changes": 3, "added_domains": 0}
        self.path.write_text(json.dumps({
            "CO-0767": good, "CO-0768": {**good, "result": "<script>"}, "CO-0769": {**good, "mode": "x"},
            "CO-0770": {**good, "leonardo_write": "maybe"}, "CO-0771": {**good, "observed_at": "soon"}, "bad": good}),
            encoding="utf-8")
        self.assertEqual(list(dashboard.attended_renewal_outcomes()), ["CO-0767"])
        for raw in ("{bad", "[]"):
            self.path.write_text(raw, encoding="utf-8")
            self.assertEqual(dashboard.attended_renewal_outcomes(), {})
            self.assertEqual(dashboard._renewal_outcome_section("CO-0767"), "")

    def test_dashboard_card_shows_the_latest_run_escaped(self):
        self.assertEqual(dashboard._renewal_outcome_section("CO-0767"), "")
        runner.write_renewal_outcome("CO-0767", "renewal_edit_verified", True, "verified", self.NOW, MIRROR_ID, self.REPORT)
        card = dashboard._renewal_outcome_section("CO-0767")
        for text in ("Latest renewal run", "renewal_edit_verified", "confirm write", "2026-10-25 → 2029-09-29",
                     "2026-10-07 09:30", "Verified"):
            self.assertIn(text, card)
        self.assertNotIn(MIRROR_ID, card)
        runner.write_renewal_outcome("CO-0767", "renewal_dry_run_planned", False, "not_performed", self.NOW)
        self.assertIn("Not applied", dashboard._renewal_outcome_section("CO-0767"))


if __name__ == "__main__":
    unittest.main()
