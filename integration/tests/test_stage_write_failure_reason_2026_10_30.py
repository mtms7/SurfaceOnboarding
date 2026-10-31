"""2026-10-30: every automatic Onboarding Completed move so far (CO-0811, CO-0804, CO-0809, CO-0812, CO-0831, CO-0806) ended
write_uncertain and the Salesforce CLI's error was thrown away. A failed stage write now records, in the existing ``reason``
field of its stage-write line, the CLI exit code and Salesforce's API error code (fixed words, never the message text), or
``readback_<verdict>`` when the write was accepted but the read-back disagrees. Outcomes and the no-retry rule are unchanged.
Hermetic: fake Salesforce reads and writes, a fake subprocess, temporary folders.
"""

import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import production_create_ui as ui
from integration.tests import test_ce_after_create as stages
import tools.serve_attended_open_onboardings_dashboard as dashboard

CO = stages.CO
REFUSAL = ("Error (1): Failed to update record. FIELD_CUSTOM_VALIDATION_EXCEPTION: Contact Jane Doe "
           "(jane@example.com) must sign off first")


class StageReasonTests(stages.base.Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record("readback_verified", account_uuid=stages.UUID)
        ui.record_uuid_write(self.dir, CO, stages.UUID, "written_verified", stages.base.NOW)
        self.salesforce = stages.sf_row(Onboarding_Stage__c="Account Scanning", Account_UUID__c=stages.UUID)
        self.writes = []

    read = stages.LaterStagesTests.read
    run_argv = stages.LaterStagesTests.run_argv

    def lines(self):
        path = self.dir / "production-create-records" / ui.STAGE_WRITES_FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def finish_with(self, run_argv):
        self.salesforce["Onboarding_Stage__c"] = "User Created"
        ui.record_stage_write(self.dir, CO, "User Created", "written_verified", stages.base.NOW)
        return ui.after_scan_finished(self.inputs(), self.read, run_argv, "surface-onboarding", scan_state="scan_completed")

    def test_a_failed_write_records_the_runners_reason(self):
        def refuse(argv):
            self.writes.append(argv)
            return None
        refuse.failure_reason = lambda: "sf_exit_1:FIELD_CUSTOM_VALIDATION_EXCEPTION"
        self.assertEqual(self.finish_with(refuse)[-1], "write_uncertain")
        last = self.lines()[-1]
        self.assertEqual((last["to_stage"], last["result"], last["reason"]),
                         ("Onboarding Completed", "write_uncertain", "sf_exit_1:FIELD_CUSTOM_VALIDATION_EXCEPTION"))
        again = ui.after_scan_finished(self.inputs(), self.read, self.run_argv, "surface-onboarding", scan_state="scan_completed")
        self.assertEqual(again, ["stage_write_needs_review"])  # never retried after an uncertain write
        self.assertEqual(self.salesforce["Onboarding_Stage__c"], "User Created")

    def test_a_reason_that_is_not_fixed_words_is_dropped(self):
        for bad in ("has spaces", "x" * 97, 42, mock.Mock()):
            def refuse(argv, bad=bad):
                return None
            refuse.failure_reason = lambda bad=bad: bad
            self.assertEqual(ui._run_failure_reason(refuse), "")
        self.assertEqual(ui._run_failure_reason(lambda argv: None), "")  # a runner without the attribute
        self.assertEqual(ui._run_failure_reason(mock.Mock()), "")         # a test double

    def test_an_accepted_write_the_readback_disagrees_with_names_the_verdict(self):
        def ignored(argv):
            self.writes.append(argv)
            return True  # exit 0, but the stage did not change
        self.assertEqual(self.finish_with(ignored)[-1], "write_uncertain")
        self.assertEqual(self.lines()[-1]["reason"], "readback_mismatch")

    def test_a_verified_write_has_no_reason(self):
        self.assertEqual(self.finish_with(self.run_argv)[-1], "written_verified")
        self.assertEqual(self.lines()[-1]["reason"], "")


class RunSfArgvTests(unittest.TestCase):
    def run_with(self, **outcome):
        def fake_run(*_args, **_kwargs):
            if "raises" in outcome:
                raise outcome["raises"]
            return SimpleNamespace(returncode=outcome["code"], stdout=outcome.get("stdout", ""), stderr=outcome.get("stderr", ""))
        with mock.patch.object(dashboard, "salesforce_cli_command", return_value="sf"), \
                mock.patch.object(dashboard.subprocess, "run", side_effect=fake_run):
            result = dashboard._run_sf_argv(["sf", "data", "update", "record"])
        return result, dashboard._run_sf_argv.failure_reason()

    def test_the_contract_is_unchanged(self):
        self.assertEqual(self.run_with(code=0), (True, ""))
        self.assertIsNone(self.run_with(code=1)[0])

    def test_a_refusal_keeps_only_the_exit_and_the_error_code(self):
        result, reason = self.run_with(code=1, stderr=REFUSAL)
        self.assertIsNone(result)
        self.assertEqual(reason, "sf_exit_1:FIELD_CUSTOM_VALIDATION_EXCEPTION")
        self.assertEqual(ui._run_failure_reason(dashboard._run_sf_argv), reason)

    def test_no_code_in_the_output_keeps_the_exit_only(self):
        self.assertEqual(self.run_with(code=2, stderr="Something went wrong for Jane Doe"), (None, "sf_exit_2"))

    def test_timeouts_and_a_missing_cli_are_named(self):
        self.assertEqual(self.run_with(raises=subprocess.TimeoutExpired("sf", 35)), (None, "sf_timeout"))
        self.assertEqual(self.run_with(raises=FileNotFoundError("sf")), (None, "sf_unavailable"))

    def test_a_success_clears_the_previous_reason(self):
        self.run_with(code=1, stderr=REFUSAL)
        self.assertEqual(self.run_with(code=0), (True, ""))

    def test_the_reason_is_per_thread(self):
        self.run_with(code=1, stderr=REFUSAL)
        seen = []
        thread = threading.Thread(target=lambda: seen.append(dashboard._run_sf_argv.failure_reason()))
        thread.start()
        thread.join()
        self.assertEqual(seen, [""])


if __name__ == "__main__":
    unittest.main()
