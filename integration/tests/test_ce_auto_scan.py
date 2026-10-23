"""docs/56 steps 3-4 (owner decision 2026-10-19, option 2): the dashboard background job that finishes a CE-only onboarding
from the production clone (query 253) with no browser and no login, and the clone data age.

For a CO this dashboard created and moved to Account Scanning, a finished CE scan in the clone row (matched by the verified
Account UUID, ended after the create time) runs ``after_scan_finished``: Account Scanning -> Scan Completed Successfully ->
Onboarding Completed. Same guards as the BackOffice check: both constants True, not the VM, exact from-stage, CE-only route,
read-back, logged, never retried after an uncertain result. Hermetic: fake clone, fake Salesforce, temporary folders.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_create_ui as ui
from integration.onboarding import production_views as views
from integration.tests import test_ce_after_create as after
from integration.tests import test_production_create_ui as base
from integration.tests.locked_build import locked_create_build  # noqa: E402

CO, UUID, NOW = base.CO, base.UUID, base.NOW
CREATED_MS = int(NOW.timestamp() * 1000)  # the create record below is written at NOW
HOUR = 3_600_000


def clone(**v2):
    return {"row_schema": "v2", "source": {"query_id": 253},
            "tenants": [{"id": "t1", "account_uuid": UUID, "v2": v2}]}


FINISHED = dict(ce_status="DONE", ce_start=CREATED_MS + HOUR, ce_end=CREATED_MS + 2 * HOUR, ce_count=1)


class AutoScanTests(base.Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record("readback_verified", account_uuid=UUID)
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        ui.record_stage_write(self.dir, CO, "Account Scanning", "written_verified", NOW)
        self.salesforce = after.sf_row(Onboarding_Stage__c="Account Scanning", Account_UUID__c=UUID)
        self.writes = []
        self.payload = clone(**FINISHED)

    def read(self):
        return dict(self.salesforce)

    def run_argv(self, argv):
        self.writes.append(argv)
        self.salesforce["Onboarding_Stage__c"] = argv[argv.index("--values") + 1].partition("=")[2].strip("'")
        return True

    def job(self, payload="default", **over):
        data = self.payload if payload == "default" else payload
        return ui.auto_scan_finish(self.inputs(**over), lambda: data, self.read, self.run_argv, "surface-onboarding")

    def test_a_finished_ce_scan_completes_the_onboarding_in_two_guarded_steps(self):
        self.assertEqual(self.job(), {"scan": "finished", "stages": ["written_verified", "written_verified"]})
        self.assertEqual(self.salesforce["Onboarding_Stage__c"], "Onboarding Completed")
        self.assertEqual([a[a.index("--values") + 1] for a in self.writes],
                         ["Onboarding_Stage__c='Scan Completed Successfully'", "Onboarding_Stage__c='Onboarding Completed'"])
        self.assertEqual(ui.last_stage_write(self.dir, CO), ("Onboarding Completed", "written_verified"))
        lines = (self.dir / "production-create-records" / ui.STAGE_WRITES_FILE).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["result"] for l in lines][-4:],
                         ["write_started", "written_verified", "write_started", "written_verified"])
        self.assertNotIn(UUID, "".join(lines))

    def test_done_with_errors_counts_as_finished(self):
        result = self.job(clone(**{**FINISHED, "ce_status": "DONE_WITH_ERRORS"}))
        self.assertEqual((result["scan"], result["stages"]), ("finished_with_errors", ["written_verified", "written_verified"]))

    def test_it_runs_once_a_second_tick_writes_nothing(self):
        self.job()
        self.writes.clear()
        self.assertEqual(self.job()["scan"], "not_a_candidate")
        self.assertEqual(self.writes, [])

    def test_a_scan_that_is_not_finished_writes_nothing(self):
        cases = {"running": clone(ce_status="PENDING", ce_start=CREATED_MS + HOUR, ce_count=1),
                 "not_yet": clone(ce_count=0),
                 "unknown": clone(ce_status="CANCELLED", ce_start=CREATED_MS + HOUR, ce_end=CREATED_MS + 2 * HOUR, ce_count=1)}
        for expected, payload in cases.items():
            with self.subTest(expected):
                self.assertEqual(self.job(payload), {"scan": expected, "stages": []})
        self.assertEqual(self.writes, [])

    def test_an_execution_from_before_this_onboarding_never_counts(self):
        old = clone(ce_status="DONE", ce_start=CREATED_MS - 2 * HOUR, ce_end=CREATED_MS - HOUR, ce_count=1)
        self.assertEqual(self.job(old), {"scan": "not_yet", "stages": []})
        self.assertEqual(self.writes, [])

    def test_the_clone_row_must_be_exactly_this_uuid(self):
        other = clone(**FINISHED)
        other["tenants"][0]["account_uuid"] = "f" * 32
        twice = clone(**FINISHED)
        twice["tenants"].append(dict(twice["tenants"][0], id="t2"))
        for payload in (other, twice, None, {}, {"row_schema": "v1", "tenants": []}):
            self.assertEqual(self.job(payload), {"scan": "clone_row_unavailable", "stages": []})
        self.assertEqual(self.writes, [])

    def test_an_unreadable_clone_loader_fails_closed(self):
        def broken():
            raise OSError("x")
        result = ui.auto_scan_finish(self.inputs(), broken, self.read, self.run_argv, "surface-onboarding")
        self.assertEqual(result, {"scan": "clone_row_unavailable", "stages": []})
        self.assertEqual(self.writes, [])

    def test_a_stale_or_unusable_clone_writes_nothing(self):
        for state in ({"state": "stale", "age_seconds": 7 * 3600}, {"state": "unusable", "age_seconds": None}, None,
                      {"state": "fresh", "age_seconds": 60, "data_age_seconds": 200 * 3600}):
            self.assertEqual(self.job(clone=state), {"scan": "clone_stale", "stages": []}, state)
        self.assertEqual(self.writes, [])

    def test_locked_build_and_vm_write_nothing(self):
        with mock.patch.object(ui.readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            self.assertEqual(self.job()["scan"], "production_dry_run_not_supported")
        self.assertEqual(self.job(vm=True)["scan"], "vm_read_only")
        self.assertEqual(self.writes, [])

    def test_only_a_co_this_dashboard_moved_to_account_scanning_is_a_candidate(self):
        for stage in ("Request Approved", "Onboarding Completed"):
            ui.record_stage_write(self.dir, CO, stage, "written_verified", NOW)
            self.assertEqual(self.job()["scan"], "not_a_candidate", stage)
        for result in ("write_started", "write_uncertain", "write_rejected"):
            ui.record_stage_write(self.dir, CO, "Account Scanning", result, NOW)
            self.assertEqual(self.job()["scan"], "not_a_candidate", result)
        self.assertEqual(self.writes, [])

    def test_a_co_without_a_verified_create_or_uuid_write_is_skipped(self):
        (self.dir / "production-create-records").joinpath(ui.UUID_WRITES_FILE).unlink()
        self.assertEqual(self.job()["scan"], "no_verified_create")
        self.assertEqual(self.writes, [])

    def test_the_uuid_must_still_be_on_the_co(self):
        self.salesforce["Account_UUID__c"] = None
        self.assertEqual(self.job(), {"scan": "finished", "stages": ["uuid_not_on_co"]})
        self.assertEqual(self.writes, [])

    def test_a_stage_changed_by_hand_is_left_alone(self):
        self.salesforce["Onboarding_Stage__c"] = "User Created"
        self.assertEqual(self.job(), {"scan": "finished", "stages": ["stage_not_expected"]})
        self.assertEqual(self.writes, [])

    def test_an_uncertain_write_is_recorded_and_never_retried(self):
        calls = []

        def flaky(argv):
            calls.append(argv)
            return None  # timeout
        first = ui.auto_scan_finish(self.inputs(), lambda: self.payload, self.read, flaky, "surface-onboarding")
        self.assertEqual(first, {"scan": "finished", "stages": ["write_uncertain"]})
        self.assertEqual(ui.last_stage_write(self.dir, CO), ("Scan Completed Successfully", "write_uncertain"))
        calls.clear()
        again = ui.auto_scan_finish(self.inputs(), lambda: self.payload, self.read, flaky, "surface-onboarding")
        self.assertEqual(again["scan"], "not_a_candidate")
        self.assertEqual(calls, [])

    def test_a_crash_between_the_two_moves_resumes_without_a_second_first_write(self):
        ui.record_stage_write(self.dir, CO, "Scan Completed Successfully", "written_verified", NOW)
        self.salesforce["Onboarding_Stage__c"] = "Scan Completed Successfully"
        result = self.job()
        self.assertEqual(result, {"scan": "finished", "stages": ["already_present", "written_verified"]})
        self.assertEqual([a[a.index("--values") + 1] for a in self.writes], ["Onboarding_Stage__c='Onboarding Completed'"])


class CandidateTests(base.Base):
    def test_candidates_are_the_cos_whose_newest_stage_line_is_a_verified_wait_for_the_scan(self):
        for co, stages in (("CO-0001", [("Account Scanning", "write_started"), ("Account Scanning", "written_verified")]),
                           ("CO-0002", [("Account Scanning", "written_verified"), ("Scan Completed Successfully", "written_verified")]),
                           ("CO-0003", [("Account Scanning", "written_verified"), ("Onboarding Completed", "written_verified")]),
                           ("CO-0004", [("Account Scanning", "write_uncertain")]),
                           ("CO-0005", [("Account Scanning", "written_verified"), ("Scan Completed Successfully", "write_uncertain")])):
            for stage, result in stages:
                ui.record_stage_write(self.dir, co, stage, result, NOW)
        self.assertEqual(ui.auto_scan_candidates(self.dir), ["CO-0001", "CO-0002"])

    def test_no_or_unreadable_log_has_no_candidates(self):
        self.assertEqual(ui.auto_scan_candidates(self.dir), [])
        folder = self.dir / "production-create-records"
        folder.mkdir(exist_ok=True)
        (folder / ui.STAGE_WRITES_FILE).write_text("not json\n", encoding="utf-8")
        self.assertEqual(ui.auto_scan_candidates(self.dir), [])


class DataAgeTests(unittest.TestCase):
    def test_the_gate_refuses_a_clone_whose_data_is_too_old_even_if_it_was_just_retrieved(self):
        class Gate(base.Base):
            def runTest(self):
                pass
        case = Gate()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.unlocked()
        stale = {"state": "fresh", "age_seconds": 600, "data_age_seconds": views.DATA_LIMIT_SECONDS + 1}
        self.assertEqual(ui.gate(case.inputs(clone=stale))[0], "clone_stale")
        for bad in (None, -1, True, "1"):
            self.assertEqual(ui.gate(case.inputs(clone={**stale, "data_age_seconds": bad}))[0], "clone_stale", bad)

    def test_the_strip_and_the_line_show_the_data_age(self):
        info = {"state": "fresh", "captured_at": "2026-10-19T20:02:00Z", "age_seconds": 900, "reason": "",
                "limit_seconds": 21600, "label": "Production", "data_age_seconds": 20 * 3600 + 60,
                "data_limit_seconds": views.DATA_LIMIT_SECONDS}
        with mock.patch.object(dashboard, "_clone_status", return_value=info):
            strip = dashboard._clone_strip()
        self.assertIn("data 20 h 1 min old", strip)
        self.assertIn("trusted", strip)
        stale = {**info, "state": "stale", "reason": "data_stale", "data_age_seconds": views.DATA_LIMIT_SECONDS + 60}
        with mock.patch.object(dashboard, "_clone_status", return_value=stale):
            self.assertIn("data 36 h 1 min old", dashboard._clone_strip())
            self.assertIn("not trusted", dashboard._clone_strip())


class ScanCardTests(base.Base):
    def test_the_scan_card_shows_the_last_automatic_clone_check(self):
        ui.record_stage_write(self.dir, CO, "Account Scanning", "written_verified", NOW)
        self.record("readback_verified", account_uuid=UUID)
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        html = ui.scan_card_html(self.dir, CO, running=False, auto={"scan": "running", "at": "2026-10-19T21:00:00Z"})
        self.assertIn("Automatic clone check 2026-10-19 21:00:00", html)
        self.assertIn(ui.AUTO_SCAN_TEXT["running"], html)
        self.assertIn("Check CE scan in BackOffice", html)  # the manual fallback stays
        self.assertNotIn("Automatic clone check", ui.scan_card_html(self.dir, CO, running=False))
        self.assertIn("Automatic clone check", ui.scan_card_html(self.dir, CO, running=False, auto={"scan": "weird", "at": "x"}))


if __name__ == "__main__":
    unittest.main()


class TickTests(AutoScanTests):
    """The dashboard's loop body: candidates only, never on the VM, never while the owner's check runs, errors recorded."""

    def tick(self, **patches):
        stack = {"production_records_parent": lambda: self.dir, "_create_ui_inputs": lambda ref, row: self.inputs(reference=ref),
                 "_clone_payload_for_job": lambda: self.payload, "_read_production_writeback_row": lambda ref: self.read(),
                 "_run_sf_argv": self.run_argv, "vm_mode": lambda: False, **patches}
        with mock.patch.multiple(dashboard, **stack), mock.patch.dict(dashboard.AUTO_SCAN_STATE, clear=True):
            dashboard._auto_scan_tick()
            return dict(dashboard.AUTO_SCAN_STATE)

    def test_a_tick_finishes_a_finished_scan_and_records_the_state(self):
        state = self.tick()
        self.assertEqual(state[CO]["scan"], "finished")
        self.assertEqual(self.salesforce["Onboarding_Stage__c"], "Onboarding Completed")
        self.assertEqual(self.tick(), {})  # no longer a candidate

    def test_a_tick_on_the_vm_does_nothing(self):
        self.assertEqual(self.tick(vm_mode=lambda: True), {})
        self.assertEqual(self.writes, [])

    def test_a_tick_skips_a_co_whose_backoffice_check_is_running(self):
        with mock.patch.dict(dashboard.PRODUCTION_SCAN_RUNS, {CO: {"running": True, "stages": None}}):
            self.assertEqual(self.tick(), {})
        self.assertEqual(self.writes, [])

    def test_an_error_inside_a_tick_is_recorded_and_writes_nothing(self):
        def broken():
            raise RuntimeError("x")
        self.assertEqual(self.tick(_clone_payload_for_job=broken)[CO]["scan"], "clone_row_unavailable")

        def boom(ref, row):
            raise RuntimeError("x")
        self.assertEqual(self.tick(_create_ui_inputs=boom)[CO]["scan"], "follow_up_failed")
        self.assertEqual(self.writes, [])


class OnboardedSummaryTests(base.Base):
    """The lists and the CO page say which COs this dashboard already onboarded, from recorded evidence (never the clone alone:
    the clone is a nightly snapshot, so a fresh create is not in it yet)."""

    def done(self, stage=None, uuid=True):
        self.record("readback_verified", account_uuid=UUID)
        if uuid:
            ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        if stage:
            ui.record_stage_write(self.dir, CO, stage, "written_verified", NOW)

    def test_no_verified_create_is_not_onboarded(self):
        self.assertIsNone(ui.onboarded_summary(self.dir, CO))
        self.record("uncertain_submit")
        self.assertIsNone(ui.onboarded_summary(self.dir, CO))

    def test_created_but_uuid_not_written_is_a_warning_not_onboarded(self):
        self.done(uuid=False)
        summary = ui.onboarded_summary(self.dir, CO)
        self.assertEqual((summary["css"], summary["label"]), ("chip-warn", "Created: UUID not written"))

    def test_onboarded_shows_the_stage_and_the_clone_scan_state(self):
        self.done("Account Scanning")
        none = ui.onboarded_summary(self.dir, CO)
        self.assertEqual((none["css"], none["label"], none["stage"]), ("chip-ok", "Onboarded", "Account Scanning"))
        self.assertIn("Account Scanning", none["text"])
        self.assertNotIn("CE scan", none["text"])  # no clone given: nothing guessed
        cases = {"finished": FINISHED, "running": dict(ce_status="PENDING", ce_start=CREATED_MS + HOUR, ce_count=1),
                 "not_yet": dict(ce_count=0)}
        for expected, v2 in cases.items():
            with self.subTest(expected):
                summary = ui.onboarded_summary(self.dir, CO, clone(**v2))
                self.assertEqual(summary["scan"], expected)
                self.assertIn("CE scan (clone)", summary["text"])
        missing = ui.onboarded_summary(self.dir, CO, {"row_schema": "v2", "tenants": []})
        self.assertEqual(missing["scan"], "clone_row_unavailable")
        self.assertIn("not in the clone yet", missing["text"])

    def test_completed_is_labelled_completed(self):
        self.done("Onboarding Completed")
        summary = ui.onboarded_summary(self.dir, CO, clone(**FINISHED))
        self.assertEqual(summary["label"], "Onboarded: completed")
        self.assertNotIn("CE scan", summary["text"])

    def test_nothing_sensitive_is_in_the_text(self):
        self.done("Account Scanning")
        self.assertNotIn(UUID, json.dumps(ui.onboarded_summary(self.dir, CO, clone(**FINISHED))))

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_the_locked_create_card_says_where_the_onboarding_stands(self):
        self.record("readback_verified", account_uuid=UUID)
        html = ui.create_card_html(self.inputs())
        self.assertIn("Next: write the Account UUID", html)  # nothing written yet
        ui.record_uuid_write(self.dir, CO, UUID, "written_verified", NOW)
        ui.record_stage_write(self.dir, CO, "Account Scanning", "written_verified", NOW)
        html = ui.create_card_html(self.inputs())
        self.assertNotIn("Next: write the Account UUID", html)
        self.assertIn("Onboarded", html)
        self.assertIn("Account Scanning", html)
        self.assertFalse(self.has_control(html))
