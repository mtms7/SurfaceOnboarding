"""The Surface scan state of a verified Case 3 tenant, from the read-only scan observation (owner request 2026-10-21), and the reason the
Duration Per Scan executions read failed (a fixed step token, never free text). The scan row says COMPLETED even when the executions list
cannot be read: the Surface scan state must not depend on the CE executions. Pure and hermetic.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from integration.onboarding import production_scan_check as scan
from integration.onboarding import verified_tenant as vt
import tools.attended_ce_only_playwright as runner

UUID = "0123456789abcdef0123456789abcdef"
ROW_ID = "0123456789abcdef01234567"
NOW = datetime(2026, 10, 21, 21, 11, 2, tzinfo=timezone.utc)
VERIFIED = {"recorded_at": "2026-10-21T21:08:16Z", "account_id": ROW_ID, "account_uuid": UUID, "tenant_name": "Example TG"}


def observation(**fields):
    base = {"lastScanStatusEnum": "COMPLETED", "lastReconScan": 1792602901380, "lastReconScanDurationMilliseconds": 5966328}
    base.update(fields)
    return {"co_reference": "CO-0800", "recorded_at": "2026-10-21T21:11:02Z", "state": "executions_unavailable", "found": True,
            "fields": base}


def uuid_not_in_clone():
    return {"status": "uuid_not_in_clone", "reason": "UUID set but tenant not found in clone", "fields": [], "differs": [],
            "candidates": [], "target": None, "captured_at": None, "age_seconds": None, "tenants_checked": 5449}


class StateTests(unittest.TestCase):
    def test_a_completed_scan_row_is_completed_with_its_times(self):
        state = scan.surface_scan_state(observation())
        self.assertEqual(state["state"], "completed")
        self.assertEqual(state["last_scan"], "2026-10-21T17:15:01Z")
        self.assertEqual(state["checked_at"], "2026-10-21T21:11:02Z")

    def test_running_statuses_are_running_and_anything_else_is_unknown(self):
        for value in ("RUNNING", "IN_PROGRESS", "PENDING", "STARTED"):
            self.assertEqual(scan.surface_scan_state(observation(lastScanStatusEnum=value))["state"], "running", value)
        for value in ("FAILED", "WITHHELD", "<withheld>", None, 5, "weird value"):
            self.assertEqual(scan.surface_scan_state(observation(lastScanStatusEnum=value))["state"], "unknown", value)

    def test_nothing_is_claimed_without_a_found_row(self):
        for bad in (None, {}, {"found": False, "fields": {"lastScanStatusEnum": "COMPLETED"}}, {"found": True},
                    {"found": True, "fields": "x"}, "x"):
            self.assertIsNone(scan.surface_scan_state(bad), bad)

    def test_a_missing_or_odd_scan_time_gives_no_time_not_a_guess(self):
        for value in (None, "x", -1, 10**30, True):
            state = scan.surface_scan_state(observation(lastReconScan=value))
            self.assertEqual(state["state"], "completed")
            self.assertIsNone(state["last_scan"], value)


class ApplyTests(unittest.TestCase):
    def test_the_onboarded_line_names_the_surface_scan(self):
        state = scan.surface_scan_state(observation())
        out = vt.apply(uuid_not_in_clone(), VERIFIED, state)
        self.assertEqual(out["status"], "onboarded")
        self.assertIn("Surface scan: completed", out["reason"])
        self.assertIn("2026-10-21 17:15 UTC", out["reason"])
        self.assertEqual(out["review"]["line"], out["reason"])
        self.assertEqual(out["verified_live"]["surface_scan"], "completed")

    def test_running_and_unchecked_are_said_plainly(self):
        running = vt.apply(uuid_not_in_clone(), VERIFIED, scan.surface_scan_state(observation(lastScanStatusEnum="RUNNING")))
        self.assertIn("Surface scan: running", running["reason"])
        unchecked = vt.apply(uuid_not_in_clone(), VERIFIED, None)
        self.assertIn("Surface scan: not checked yet", unchecked["reason"])
        unknown = vt.apply(uuid_not_in_clone(), VERIFIED, scan.surface_scan_state(observation(lastScanStatusEnum="FAILED")))
        self.assertIn("Surface scan: status unclear", unknown["reason"])


class RecordedMatchTests(unittest.TestCase):
    def test_the_runner_adds_the_newest_scan_observation_to_the_onboarded_line(self):
        from integration.onboarding import production_create_record as records

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            records.record(parent, {"co_reference": "CO-0800", "outcome": "readback_verified", "route": runner.CASE3_ENGINE,
                                    "tenant_name": "Example TG", "account_id": ROW_ID, "account_uuid": UUID,
                                    "domains": ["example.example"], "approval_ref": "PROBE-D4-20261016"}, NOW)
            before = runner._apply_verified_tenant(uuid_not_in_clone(), "CO-0800", UUID, parent)
            self.assertIn("Surface scan: not checked yet", before["reason"])
            scan.record(parent, "CO-0800", {"state": "executions_unavailable", "found": True,
                                            "fields": observation()["fields"]}, NOW)
            after = runner._apply_verified_tenant(uuid_not_in_clone(), "CO-0800", UUID, parent)
            self.assertIn("Surface scan: completed", after["reason"])


class ProblemTokenTests(unittest.TestCase):
    def test_the_observation_keeps_a_fixed_problem_token_and_drops_anything_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            for token, kept in (("details_open", "details_open"), ("reply_wait", "reply_wait"), ("tab_click", "tab_click"),
                                ("some free text with spaces", None), ("DROP TABLE", None), (None, None), (5, None)):
                reply = {"state": "executions_unavailable", "found": True, "fields": {}, "executions_problem": token}
                path = scan.record(parent, "CO-0800", reply, NOW)
                data = __import__("json").loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("executions_problem"), kept, token)

    def test_the_step_of_a_failed_executions_read_is_carried_on_the_error(self):
        class Page:
            keyboard = type("K", (), {"press": staticmethod(lambda *_a, **_k: None)})()

            def on(self, *_a):
                pass

            def remove_listener(self, *_a):
                pass

            def wait_for_timeout(self, *_a):
                pass

        def boom(*_a, **_k):
            raise TimeoutError("row not clickable")

        original = runner._open_row_action
        runner._open_row_action = boom
        try:
            with self.assertRaises(RuntimeError) as caught:
                runner.read_scan_executions(Page(), "Example TG", ROW_ID, runner.production_probe.PRODUCTION_ORIGIN)
        finally:
            runner._open_row_action = original
        self.assertEqual(str(caught.exception), "scan_status_executions_unavailable")
        self.assertEqual(caught.exception.step, "details_open")


if __name__ == "__main__":
    unittest.main()
