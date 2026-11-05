"""Owner report 2026-11-05: the queue showed CO-0841 (Case 3) "Created: UUID not written" although Salesforce held the
Account ID and UUID. A Case 3 create writes them, and its stage, through the Case 3 follow-up log, not the CE-only logs that
``onboarded_summary`` read. Also CO-0839 (CE-only): the stage move to User Created was sent but its read-back failed, which
hid the verified "Scan Completed Successfully" before it. Hermetic: temporary record folders, patched create history.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import case3_followup as fu
from integration.onboarding import production_create_record as records
from integration.onboarding import production_create_ui as ui

AT = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / records.RECORDS_SUBDIR).mkdir(parents=True)
        patcher = mock.patch.multiple(ui, load_co_records=mock.Mock(return_value=[{}]),
                                      co_status=mock.Mock(return_value={"code": ui.CREATED_VERIFIED, "uuid": "a" * 32}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def stage_line(self, to_stage, result, reason=""):
        with (self.folder / records.RECORDS_SUBDIR / ui.STAGE_WRITES_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"co_reference": "CO-0839", "to_stage": to_stage, "result": result, "reason": reason}) + "\n")

    def uuid_line(self, co):
        with (self.folder / records.RECORDS_SUBDIR / ui.UUID_WRITES_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"co_reference": co, "field": "Account_UUID__c", "result": "written_verified"}) + "\n")

    def test_case3_ids_and_stage_from_the_follow_up_log(self):
        before = ui.onboarded_summary(self.folder, "CO-0841")
        self.assertEqual(before["label"], "Created: UUID not written")  # nothing written anywhere yet
        fu._record(self.folder, "CO-0841", "ids", "written_verified", AT)
        fu._record(self.folder, "CO-0841", "stage", "written_verified", AT, {"to_stage": "Account Scanning"})
        found = ui.onboarded_summary(self.folder, "CO-0841")
        self.assertEqual((found["label"], found["stage"]), ("Onboarded", "Account Scanning"))
        self.assertIn("Account ID and UUID written and read back", found["text"])

    def test_case3_ids_not_verified_still_warn(self):
        fu._record(self.folder, "CO-0841", "ids", "write_uncertain", AT)
        self.assertEqual(ui.onboarded_summary(self.folder, "CO-0841")["label"], "Created: UUID not written")

    def test_an_unconfirmed_later_move_keeps_the_verified_stage(self):
        self.uuid_line("CO-0839")
        self.stage_line("Account Scanning", "written_verified")
        self.stage_line("Scan Completed Successfully", "written_verified")
        self.stage_line("User Created", "write_uncertain", "readback_unreadable")
        found = ui.onboarded_summary(self.folder, "CO-0839")
        self.assertEqual(found["stage"], "Scan Completed Successfully")
        self.assertIn("the move to User Created was sent but not read back", found["text"])

    def test_last_stage_reads_only_verified_case3_lines(self):
        self.assertIsNone(fu.last_stage(self.folder, "CO-0841"))
        fu._record(self.folder, "CO-0841", "stage", "write_started", AT, {"to_stage": "Account Scanning"})
        self.assertIsNone(fu.last_stage(self.folder, "CO-0841"))
        fu._record(self.folder, "CO-0841", "stage", "written_verified", AT, {"to_stage": "Account Scanning"})
        self.assertEqual(fu.last_stage(self.folder, "CO-0841"), "Account Scanning")


if __name__ == "__main__":
    unittest.main()
