"""2026-11-13 (CO-0845): the Operator Account confirmed by hand when neither BackOffice nor Redash carries it."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import post_onboard_audit as audit
from integration.onboarding import production_create_record as records
from integration.onboarding import recent_onboardings as recent

CO = "CO-0845"
NOW = datetime(2026, 11, 13, 19, 30, tzinfo=timezone.utc)
LIVE_UNKNOWN = {"recorded_at": "2026-11-13T19:06:07Z", "ce": "done", "recon": "none",
                "facts": {"operator_assigned": None, "max_scan_hours": None}}
LIVE_NO_OPERATOR = {**LIVE_UNKNOWN, "facts": {"operator_assigned": False}}


def operator_row(item):
    return next(c for c in item["checks"] if c["key"] == "operator")


class ConfirmationRecordTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.parent = Path(folder.name)

    def test_round_trip_keeps_only_co_user_and_time(self):
        self.assertEqual(audit.operator_confirmation(self.parent, CO), (None, None))
        audit.record_operator_confirmation(self.parent, CO, "MMachados", NOW)
        self.assertEqual(audit.operator_confirmation(self.parent, CO), ("mmachados", NOW))
        line = json.loads((self.parent / records.RECORDS_SUBDIR / audit.OPERATOR_CONFIRMATIONS).read_text(encoding="utf-8"))
        self.assertEqual(set(line), {"co_reference", "check", "confirmed_by", "recorded_at"})
        self.assertEqual(audit.operator_confirmation(self.parent, "CO-0001"), (None, None))

    def test_bad_input_is_refused_and_bad_lines_are_skipped(self):
        for co, by in (("CO-x", "mmachados"), (CO, ""), (CO, "<script>")):
            with self.assertRaises(ValueError):
                audit.record_operator_confirmation(self.parent, co, by, NOW)
        folder = self.parent / records.RECORDS_SUBDIR
        folder.mkdir(parents=True)
        (folder / audit.OPERATOR_CONFIRMATIONS).write_text(
            "not json\n" + json.dumps({"co_reference": CO, "check": "operator", "confirmed_by": "<b>", "recorded_at":
                                       "2026-11-13T19:00:00Z"}) + "\n", encoding="utf-8")
        self.assertEqual(audit.operator_confirmation(self.parent, CO), (None, None))


class MergeRuleTests(unittest.TestCase):
    def test_unknown_operator_becomes_done_when_confirmed(self):
        item = audit.view("ce_only", LIVE_UNKNOWN, None, None, operator_confirmed=("mmachados", NOW))
        row = operator_row(item)
        self.assertEqual((row["state"], row["source"], row["by"]), (audit.DONE, "confirmed", "mmachados"))
        self.assertTrue(audit.all_done(item))
        self.assertFalse(audit.operator_unknown(item))

    def test_a_known_no_operator_is_never_overridden(self):
        item = audit.view("case3", LIVE_NO_OPERATOR, None, None, operator_confirmed=("mmachados", NOW))
        self.assertEqual((operator_row(item)["state"], operator_row(item)["source"]), (audit.OPEN, "live"))

    def test_without_confirmation_it_stays_unknown(self):
        item = audit.view("ce_only", LIVE_UNKNOWN, None, None)
        self.assertEqual(operator_row(item)["state"], audit.UNKNOWN)
        self.assertTrue(audit.operator_unknown(item))

    def test_daily_history_accepts_the_new_source(self):
        item = audit.view("ce_only", LIVE_UNKNOWN, None, None, operator_confirmed=("mmachados", NOW))
        line = recent.daily_line(CO, "2026-11-13", "ce_only", None, None, item["checks"])
        self.assertEqual(line["checks"]["operator"], {"state": audit.DONE, "source": "confirmed"})


class CardTests(unittest.TestCase):
    def test_button_only_while_unknown_and_allowed(self):
        unknown = audit.view("ce_only", LIVE_UNKNOWN, None, None)
        self.assertIn(audit.OPERATOR_CONFIRM_PATH, audit.card_html(unknown, CO, may_confirm=True))
        self.assertNotIn(audit.OPERATOR_CONFIRM_PATH, audit.card_html(unknown, CO, may_confirm=False))
        self.assertNotIn(audit.OPERATOR_CONFIRM_PATH, audit.card_html(unknown))
        done = audit.view("ce_only", LIVE_UNKNOWN, None, None, operator_confirmed=("mmachados", NOW))
        html = audit.card_html(done, CO, may_confirm=True)
        self.assertNotIn(audit.OPERATOR_CONFIRM_PATH, html)
        self.assertIn("confirmed by mmachados", html)

    def test_dashboard_and_module_use_one_path(self):
        self.assertEqual(dashboard.OPERATOR_CONFIRM_PATH, audit.OPERATOR_CONFIRM_PATH)
        self.assertIn(dashboard.OPERATOR_CONFIRM_PATH, dashboard._PER_CO_POSTS)


class PostTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.parent = Path(folder.name)
        self.views = {CO: audit.view("ce_only", LIVE_UNKNOWN, None, None)}
        for patcher in (patch.object(dashboard, "production_records_parent", return_value=self.parent),
                        patch.object(dashboard, "vm_mode", return_value=False),
                        patch.object(dashboard, "audit_views", side_effect=lambda fresh=False: dict(self.views)),
                        patch.object(audit, "verified_creates", side_effect=lambda parent: {CO: {"route": "ce_only"}})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_records_once_for_an_unknown_operator(self):
        self.assertEqual(dashboard._post_operator_confirm(CO, "mmachados"), "ok")
        self.assertEqual(audit.operator_confirmation(self.parent, CO)[0], "mmachados")

    def test_refusals_record_nothing(self):
        self.assertEqual(dashboard._post_operator_confirm("CO-0001", "mmachados"), "no_verified_create")
        self.assertEqual(dashboard._post_operator_confirm(CO, ""), "dashboard_not_signed_in")
        self.views[CO] = audit.view("case3", LIVE_NO_OPERATOR, None, None)
        with patch.object(audit, "verified_creates", side_effect=lambda parent: {CO: {"route": "case3"}}):
            self.assertEqual(dashboard._post_operator_confirm(CO, "mmachados"), "operator_not_unknown")
        with patch.object(dashboard, "vm_mode", return_value=True):
            self.assertEqual(dashboard._post_operator_confirm(CO, "mmachados"), "vm_read_only")
        self.assertEqual(audit.operator_confirmation(self.parent, CO), (None, None))


if __name__ == "__main__":
    unittest.main()
