"""2026-11-13 page speed: change-check heartbeat (CO page) and the list-view id kept across restarts (landing page)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from tools.serve_attended_open_onboardings_dashboard import ReadUnavailable

VIEW = "00B000000000001AAA"
NEW_VIEW = "00B000000000002BBB"


class ChangeHeartbeatTests(unittest.TestCase):
    def setUp(self):
        saved = dict(dashboard._last_page_activity)
        self.addCleanup(lambda: dashboard._last_page_activity.update(saved))
        dashboard._last_page_activity["at"] = None

    def test_idle_dashboard_runs_no_extra_check(self):
        with patch.object(dashboard, "refresh_changed_cos") as check:
            self.assertFalse(dashboard.change_heartbeat_tick(now=1000.0))
            dashboard.note_page_activity(now=1000.0)
            self.assertFalse(dashboard.change_heartbeat_tick(now=1000.0 + dashboard.CHANGE_HEARTBEAT_ACTIVE_SECONDS + 1))
        check.assert_not_called()

    def test_recent_page_use_keeps_the_check_fresher_than_the_page_needs(self):
        self.assertLess(dashboard.CHANGE_HEARTBEAT_SECONDS + dashboard.CHANGE_HEARTBEAT_POLL_SECONDS,
                        dashboard.CHANGE_CHECK_PAGE_SECONDS)
        dashboard.note_page_activity(now=1000.0)
        with patch.object(dashboard, "refresh_changed_cos") as check:
            self.assertTrue(dashboard.change_heartbeat_tick(now=1100.0))
        check.assert_called_once_with(dashboard.CHANGE_HEARTBEAT_SECONDS)

    def test_a_failing_check_never_stops_the_heartbeat_and_the_vm_runs_none(self):
        dashboard.note_page_activity(now=1000.0)
        with patch.object(dashboard, "refresh_changed_cos", side_effect=RuntimeError("x")):
            self.assertTrue(dashboard.change_heartbeat_tick(now=1001.0))
        with patch.object(dashboard, "vm_mode", return_value=True), patch.object(dashboard, "refresh_changed_cos") as check:
            self.assertFalse(dashboard.change_heartbeat_tick(now=1001.0))
        check.assert_not_called()

    def test_a_fresh_heartbeat_check_lets_the_page_skip_its_own(self):
        # The page's call returns at once (no Salesforce read) when the heartbeat checked less than 15 s ago.
        saved = dict(dashboard._change_state)
        self.addCleanup(lambda: dashboard._change_state.update(saved))
        now = dashboard.datetime.now(dashboard.timezone.utc)
        dashboard._change_state.update(checked_at=now - dashboard.timedelta(seconds=dashboard.CHANGE_HEARTBEAT_SECONDS + 2),
                                       since=now)
        with patch.object(dashboard, "vm_mode", return_value=False), patch.object(dashboard, "sf_json") as read:
            self.assertEqual(dashboard.refresh_changed_cos(dashboard.CHANGE_CHECK_PAGE_SECONDS, now=now), [])
        read.assert_not_called()


class KeptViewIdTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / dashboard.VIEW_ID_FILE_NAME
        for patcher in (patch.object(dashboard, "_view_id_path", return_value=self.path),
                        patch.object(dashboard, "vm_mode", return_value=False),
                        patch.dict(dashboard._VIEW_ID_PERSIST, {"enabled": True, "loaded_from_disk": False})):
            patcher.start()
            self.addCleanup(patcher.stop)
        dashboard._open_onboardings_view_id = None
        self.addCleanup(lambda: setattr(dashboard, "_open_onboardings_view_id", None))

    @staticmethod
    def view_response(view_id):
        return {"status": 0, "result": {"records": [{"Id": view_id}]}}

    def test_a_resolved_id_is_kept_and_the_next_process_reads_no_salesforce(self):
        with patch.object(dashboard, "sf_json", return_value=self.view_response(VIEW)) as read:
            self.assertEqual(dashboard.open_onboardings_view_id(), VIEW)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.path.read_text(encoding="utf-8").strip(), VIEW)
        dashboard._open_onboardings_view_id = None  # a restart
        with patch.object(dashboard, "sf_json") as read:
            self.assertEqual(dashboard.open_onboardings_view_id(), VIEW)
        read.assert_not_called()
        self.assertTrue(dashboard._VIEW_ID_PERSIST["loaded_from_disk"])

    def test_a_malformed_kept_id_is_ignored(self):
        self.path.write_text("not-an-id; DROP\n", encoding="utf-8")
        with patch.object(dashboard, "sf_json", return_value=self.view_response(VIEW)) as read:
            self.assertEqual(dashboard.open_onboardings_view_id(), VIEW)
        self.assertEqual(read.call_count, 1)

    def test_a_kept_id_that_fails_is_forgotten_and_resolved_again_once(self):
        self.path.write_text(VIEW + "\n", encoding="utf-8")
        record = {"columns": [{"fieldNameOrPath": field, "value": "CO-0001" if field == "Name" else None}
                              for field in dashboard.QUEUE_FIELDS]}
        calls = []

        def fake(args, label=None):
            calls.append(args[:2])
            if args[:2] == ["data", "query"]:
                return self.view_response(NEW_VIEW)
            if VIEW in args[3]:
                raise ReadUnavailable()
            return {"records": [record]}
        with patch.object(dashboard, "sf_json", side_effect=fake):
            rows = dashboard.queue_source_rows.__wrapped__()
        self.assertEqual([row["Name"] for row in rows], ["CO-0001"])
        self.assertEqual(calls, [["api", "request"], ["data", "query"], ["api", "request"]])
        self.assertEqual(self.path.read_text(encoding="utf-8").strip(), NEW_VIEW)

    def test_a_resolved_id_that_fails_is_not_retried(self):
        def fake(args, label=None):
            if args[:2] == ["data", "query"]:
                return self.view_response(VIEW)
            raise ReadUnavailable()
        with patch.object(dashboard, "sf_json", side_effect=fake) as read:
            with self.assertRaises(ReadUnavailable):
                dashboard.queue_source_rows.__wrapped__()
        self.assertEqual(read.call_count, 2)

    def test_tests_and_imports_never_touch_the_file(self):
        with patch.dict(dashboard._VIEW_ID_PERSIST, {"enabled": False}):
            with patch.object(dashboard, "sf_json", return_value=self.view_response(VIEW)):
                dashboard.open_onboardings_view_id()
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
