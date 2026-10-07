from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard

NOW = datetime(2026, 10, 7, 12, 0)


def _observation(**overrides):
    base = {"state": "scan_started", "status_enum": "RUNNING", "last_recon_scan": None, "duration_ms": None,
            "observed_at": "2026-10-04T12:00:00", "expires_at": "2026-10-04T18:00:00"}
    base.update(overrides)
    return base


class ScanCardTests(unittest.TestCase):
    def _scan_file(self, entries):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(entries, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return patch.object(dashboard, "SCAN_STATUS_PATH", Path(handle.name))

    def card(self, checks, entries=None, reference="CO-0767", notice=""):
        with self._scan_file(entries or {}), patch.object(dashboard, "load_check_state", return_value=checks):
            return dashboard._scan_status_section(reference, notice=notice, now=NOW)

    def test_failed_check_without_observation_is_shown(self):
        html = self.card({"CO-0767": {"kind": "scan_status", "result": "scan_status_route_unsupported",
                                      "completed_on": "2026-10-07T09:30:00"}})
        self.assertIn("Not read yet", html)
        self.assertIn("Last scan-status read: <b>failed</b>", html)
        self.assertIn("unsupported route", html)
        self.assertIn("2026-10-07 09:30", html)

    def test_ok_check_and_unknown_code_and_started_only(self):
        ok = self.card({"CO-0767": {"kind": "scan_status", "result": "scan_status_recorded",
                                    "completed_on": "2026-10-07T09:30:00"}})
        self.assertIn("<b>ok</b>", ok)
        unknown = self.card({"CO-0767": {"kind": "scan_status", "result": "weird_code_x",
                                         "completed_on": "2026-10-07T09:30:00"}})
        self.assertIn("<b>failed</b> · weird_code_x", unknown)
        started = self.card({"CO-0767": {"kind": "scan_status", "started_on": "2026-10-07T09:30:00"}})
        self.assertIn("<b>started</b>", started)

    def test_every_required_code_has_plain_text(self):
        for code in ("scan_status_recorded", "scan_status_route_unsupported", "scan_status_renewal_mirror_missing",
                     "duplicate_search_schema_unavailable", "scan_status_schema_unavailable",
                     "scan_status_tenant_not_found", "scan_status_not_onboarded", "scan_status_not_applicable",
                     "scan_status_source_unavailable", "scan_status_write_unavailable", "development_login_timeout",
                     "leonardo_session_expired", "playwright_runtime_unavailable", "attended_ce_runner_unavailable"):
            self.assertIn(code, dashboard.SCAN_CHECK_TEXT)

    def test_values_are_escaped(self):
        html = self.card({"CO-0767": {"kind": "scan_status", "result": "<script>x</script>",
                                      "completed_on": "not-a-time"}})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("unknown time", html)

    def test_other_check_kinds_and_other_cos_are_ignored(self):
        html = self.card({"CO-0767": {"kind": "readback", "result": "readback_verified",
                                      "completed_on": "2026-10-07T09:30:00"},
                          "CO-0768": {"kind": "scan_status", "result": "scan_status_recorded",
                                      "completed_on": "2026-10-07T09:30:00"}})
        self.assertNotIn("Last scan-status read", html)

    def test_unreadable_check_state_does_not_break_the_card(self):
        with self._scan_file({}), patch.object(dashboard, "load_check_state", side_effect=ValueError("x")):
            html = dashboard._scan_status_section("CO-0767", now=NOW)
        self.assertIn("Not read yet", html)
        self.assertNotIn("Last scan-status read", html)

    def test_observation_shows_age_and_stale_marker(self):
        entries = {"CO-0767": _observation()}
        stale = self.card({}, entries)
        self.assertIn("read 3 days ago", stale)
        self.assertIn("stale, refresh", stale)
        fresh = self.card({}, {"CO-0767": _observation(observed_at="2026-10-07T09:00:00",
                                                       expires_at="2026-10-07T18:00:00")})
        self.assertIn("read 3 hours ago", fresh)
        self.assertNotIn("stale", fresh)

    def test_failed_check_is_shown_next_to_an_old_observation(self):
        html = self.card({"CO-0767": {"kind": "scan_status", "result": "leonardo_session_expired",
                                      "completed_on": "2026-10-07T09:30:00"}}, {"CO-0767": _observation()})
        self.assertIn("Leonardo scan status · Scan started", html)
        self.assertIn("<b>failed</b>", html)
        self.assertIn("read 3 days ago", html)

    def test_started_notice_is_kept(self):
        html = self.card({}, notice="started")
        self.assertIn("read-only scan-status read was started", html)
        self.assertNotIn("Last scan-status read", html)

    def test_renewal_mismatch_reason_text(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"CO-0757": {"result": "renewal_domains_mismatch_manual_review", "mode": "dry_run",
                               "leonardo_write": "not_performed", "observed_at": "2026-10-07T09:00:00",
                               "changes": 0}}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with patch.object(dashboard, "RENEWAL_OUTCOMES_PATH", Path(handle.name)):
            html = dashboard._renewal_outcome_section("CO-0757")
        self.assertIn("Salesforce domains differ from the tenant&#x27;s domains", html)
        self.assertIn("nothing was saved", html)


class StageFromLeonardoStatusTests(unittest.TestCase):
    ROW = {"Onboarding_Stage__c": "New", "Onboarding_Approval_Status__c": "Approved", "Name": "CO-0649"}
    READBACK = {"surface_account_id": "a" * 8, "account_uuid": "b" * 8}

    def stage(self, observation):
        scans = {"CO-0649": observation} if observation is not None else {}
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "attended_scan_statuses", return_value=scans), \
                patch.object(dashboard, "_stage_tenant", return_value=(None, "", "")), \
                patch.object(dashboard, "attended_validations", return_value={}), \
                patch.object(dashboard, "load_attended_reminders", return_value={}), \
                patch.object(dashboard, "load_user_created_confirmations", return_value={}):
            return dashboard._stage_state("CO-0649", dict(self.ROW), dict(self.READBACK))

    def test_completed_row_status_without_executions_reaches_scan_completed(self):
        result = self.stage({"state": "scan_completed", "status_enum": "DONE"})
        self.assertEqual(result["stage"], "Scan Completed Successfully")
        self.assertTrue(result["scan_done"])

    def test_execution_done_still_works(self):
        result = self.stage({"state": "scan_started", "status_enum": "RUNNING", "execution_state": "done"})
        self.assertEqual(result["stage"], "Scan Completed Successfully")

    def test_running_failed_unrecognized_and_no_scan_do_not_count(self):
        for state in ("scan_started", "scan_failed", "unrecognized", "no_scan"):
            self.assertEqual(self.stage({"state": state, "status_enum": None})["stage"], "Account Scanning", state)
        self.assertEqual(self.stage({"state": "scan_started", "execution_state": "running"})["stage"],
                         "Account Scanning")
        self.assertEqual(self.stage(None)["stage"], "Account Scanning")


if __name__ == "__main__":
    unittest.main()
