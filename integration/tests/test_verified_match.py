"""The production match of a CO uses the live-verified readback record (owner request 2026-10-21): a CO whose Account UUID is set but not in
the clone yet shows ONBOARDED when a Case 3 readback verified that exact tenant; the display cache notices a new record. Hermetic.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_create_record as records
import tools.attended_ce_only_playwright as runner

UUID = "0123456789abcdef0123456789abcdef"
ROW_ID = "0123456789abcdef01234567"
NOW = datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)


def match(status="uuid_not_in_clone"):
    return {"status": status, "reason": "UUID set but tenant not found in clone", "fields": [], "differs": [], "candidates": [],
            "target": None, "captured_at": None, "age_seconds": None, "tenants_checked": 5449}


def write(parent, **over):
    data = {"co_reference": "CO-0800", "outcome": "readback_verified", "route": runner.CASE3_ENGINE, "tenant_name": "Example TG",
            "account_id": ROW_ID, "account_uuid": UUID, "domains": ["example.example"], "approval_ref": "PROBE-D4-20261016"}
    data.update(over)
    return records.record(parent, data, NOW)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.parent = Path(tmp.name)

    def apply(self, result=None, uuid=UUID, co="CO-0800"):
        return runner._apply_verified_tenant(result or match(), co, uuid, self.parent)

    def test_a_verified_record_makes_the_co_onboarded(self):
        write(self.parent)
        out = self.apply()
        self.assertEqual(out["status"], "onboarded")
        self.assertEqual(out["verified_live"]["account_id"], ROW_ID)

    def test_no_record_a_different_uuid_or_another_co_leaves_the_match_alone(self):
        self.assertEqual(self.apply()["status"], "uuid_not_in_clone")
        write(self.parent)
        self.assertEqual(self.apply(uuid="fedcba9876543210fedcba9876543210")["status"], "uuid_not_in_clone")
        self.assertEqual(self.apply(co="CO-0801")["status"], "uuid_not_in_clone")
        self.assertEqual(self.apply(uuid=None)["status"], "uuid_not_in_clone")

    def test_only_uuid_not_in_clone_is_ever_changed(self):
        write(self.parent)
        for status in ("onboarded", "ambiguous", "exists_other", "clone_unavailable", "not_checked", "not_in_production"):
            self.assertEqual(self.apply(match(status))["status"], status)

    def test_an_unreadable_record_folder_leaves_the_match_alone(self):
        write(self.parent)
        (self.parent / records.RECORDS_SUBDIR / "CO-0800-20990101T000000Z.json").write_text("not json", encoding="utf-8")
        self.assertEqual(self.apply()["status"], "uuid_not_in_clone")


class StampTests(unittest.TestCase):
    def test_a_new_record_changes_the_cache_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            with mock.patch.object(dashboard, "production_records_parent", return_value=parent):
                before = dashboard._match_stamp()
                write(parent)
                after = dashboard._match_stamp()
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
