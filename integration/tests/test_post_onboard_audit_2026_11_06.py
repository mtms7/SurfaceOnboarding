"""Post-onboarding audit (owner request + go 2026-11-06): candidates, both sources, the Case 3 scan move."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import case3_followup
from integration.onboarding import post_onboard_audit as audit
from integration.onboarding import production_create_record as records

NOW = datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)
UUID = "0123456789abcdef0123456789abcdef"


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _write_create(parent: Path, co: str, route: str, when: datetime, uuid: str = UUID) -> None:
    folder = parent / records.RECORDS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    item = {"co_reference": co, "route": route, "outcome": "readback_verified",
            "recorded_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "account_uuid": uuid}
    (folder / f"{co}-{when.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(json.dumps(item), encoding="utf-8")


class FakeRecords:
    """records.list_records / records.load over plain JSON files (the real validator needs the full contract)."""

    @staticmethod
    def list_records(parent, co=None):
        folder = Path(parent) / records.RECORDS_SUBDIR
        paths = sorted(folder.glob("CO-*.json")) if folder.is_dir() else []
        return [p for p in paths if co is None or p.name.startswith(co + "-")]

    @staticmethod
    def load(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))


class AuditTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name)
        patch_list = mock.patch.object(audit.records, "list_records", FakeRecords.list_records)
        patch_load = mock.patch.object(audit.records, "load", FakeRecords.load)
        patch_list.start()
        patch_load.start()
        self.addCleanup(patch_list.stop)
        self.addCleanup(patch_load.stop)
        self.addCleanup(self.tmp.cleanup)


class CandidateTests(AuditTestCase):
    def test_last_three_days_always_and_thirty_days_only_while_open(self):
        _write_create(self.parent, "CO-0001", audit.CASE3_ROUTE, NOW - timedelta(days=1))
        _write_create(self.parent, "CO-0002", audit.CE_ROUTE, NOW - timedelta(days=10))
        _write_create(self.parent, "CO-0003", audit.CE_ROUTE, NOW - timedelta(days=12))
        _write_create(self.parent, "CO-0004", audit.CE_ROUTE, NOW - timedelta(days=40))
        open_cos = {"CO-0002"}
        chosen = audit.candidates(self.parent, NOW, lambda co, _route: co in open_cos)
        self.assertEqual(chosen, ["CO-0001", "CO-0002"])

    def test_onboarding_completed_and_unknown_routes_are_left_out(self):
        _write_create(self.parent, "CO-0001", audit.CE_ROUTE, NOW - timedelta(days=1))
        _write_create(self.parent, "CO-0002", "case_6_something", NOW - timedelta(days=1))
        log = self.parent / records.RECORDS_SUBDIR / "production-stage-writes.jsonl"
        log.write_text(json.dumps({"co_reference": "CO-0001", "result": "written_verified",
                                   "to_stage": "Onboarding Completed"}) + "\n", encoding="utf-8")
        self.assertEqual(audit.candidates(self.parent, NOW, lambda *_: True), [])


class LiveFactsTests(unittest.TestCase):
    def test_row_facts_keep_no_identity(self):
        row = {"accountName": "Secret Co", "accountDomain": "secret.example", "accountUuid": UUID,
               "primaryUser": {"email": "a@b.c"}, "lastScanStatusEnum": "COMPLETED", "lastReconScan": 5, "_created": 1,
               "operatorAccounts": ["Opname Zeta"], "campaignsTimeoutInHours": 90, "enabled": True}
        facts = audit.live_row_facts(row)
        text = json.dumps(facts)
        for secret in ("Secret", "secret.example", UUID, "a@b.c", "Zeta"):
            self.assertNotIn(secret, text)
        self.assertEqual(facts, {"surface_status": "COMPLETED", "last_recon_ms": 5, "created_ms": 1, "operator_assigned": True,
                                 "max_scan_hours": 90, "primary_user_present": True, "enabled": True})

    def test_log_line_rejects_free_text(self):
        with self.assertRaises(ValueError):
            audit.log_line("CO-0841", NOW, "scan_completed", {"surface_status": "Completed by Bob"}, [])
        with self.assertRaises(ValueError):
            audit.log_line("not-a-co", NOW, "scan_completed", {}, [])

    def test_co0841_live_read_is_finished(self):
        """The 2026-11-06 20:30Z read: row COMPLETED, both recon executions DONE, the CE execution DONE."""
        executions = [{"campaign_type": "RECON_WITH_ASSET_APPROVALS", "status": "DONE"},
                      {"campaign_type": "RECON_WITH_ASSET_APPROVALS", "status": "DONE"},
                      {"campaign_type": "LEAKED_CREDENTIALS_DISCOVERY", "status": "DONE"}]
        facts = {"surface_status": "COMPLETED", "last_recon_ms": 1793919601491, "created_ms": 1793911479041,
                 "operator_assigned": False, "max_scan_hours": 90}
        line = audit.log_line("CO-0841", NOW, "scan_completed", facts, executions)
        result = audit.view("case3", line, None, None)
        states = {c["key"]: c["state"] for c in result["checks"]}
        # 2026-11-11 (CO-0844): the primary user comes from our own record only, so no record = open (as on 2026-11-06)
        self.assertEqual(states, {"surface_scan": "done", "ce_scan": "done", "operator": "open", "max_scan": "done",
                                  "primary_user": "open"})
        self.assertTrue(result["finished"])
        self.assertEqual(result["open"], 2)
        done = audit.view("case3", line, None, None, ("owner_confirmed_by_hand", NOW))
        self.assertEqual({c["key"]: c["state"] for c in done["checks"]}["primary_user"], "done")

    def test_running_recon_or_old_scan_is_not_finished(self):
        running = audit.log_line("CO-0841", NOW, "scan_running", {"surface_status": "COMPLETED", "last_recon_ms": 10,
                                                                  "created_ms": 5},
                                 [{"campaign_type": "RECON_WITH_ASSET_APPROVALS", "status": "PENDING"},
                                  {"campaign_type": "LEAKED_CREDENTIALS_DISCOVERY", "status": "DONE"}])
        self.assertFalse(audit.view("case3", running, None, None)["finished"])
        old = audit.log_line("CO-0841", NOW, "scan_completed", {"surface_status": "COMPLETED", "last_recon_ms": 4,
                                                                "created_ms": 5},
                             [{"campaign_type": "LEAKED_CREDENTIALS_DISCOVERY", "status": "DONE"}])
        self.assertFalse(audit.view("case3", old, None, None)["finished"])

    def test_incomplete_is_unknown_not_done(self):
        line = audit.log_line("CO-0841", NOW, "unconfirmed", {"surface_status": "INCOMPLETE", "last_recon_ms": 10,
                                                              "created_ms": 5}, None)
        checks = {c["key"]: c["state"] for c in audit.view("case3", line, None, None)["checks"]}
        self.assertEqual(checks["surface_scan"], "unknown")
        self.assertEqual(checks["ce_scan"], "unknown")


class MergeTests(unittest.TestCase):
    CLONE_AT = datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)
    CLONE_RUNNING = {"created": 1, "operator_assigned": False, "campaigns_timeout_hours": None,
                     "scan": {"status": "RUNNING", "last_recon_scan_ms": 10}, "v2": {"ce_status": "DONE"}}

    def test_newer_live_read_wins_over_the_nightly_clone(self):
        line = audit.log_line("CO-0841", NOW, "scan_completed",
                              {"surface_status": "COMPLETED", "last_recon_ms": 10, "created_ms": 1},
                              [{"campaign_type": "RECON_WITH_ASSET_APPROVALS", "status": "DONE"},
                               {"campaign_type": "LEAKED_CREDENTIALS_DISCOVERY", "status": "DONE"}])
        result = audit.view("case3", line, self.CLONE_RUNNING, self.CLONE_AT)
        rows = {c["key"]: c for c in result["checks"]}
        self.assertEqual((rows["surface_scan"]["state"], rows["surface_scan"]["source"]), ("done", "live"))
        self.assertTrue(result["finished"])

    def test_clone_alone_works_without_the_pc(self):
        clone_done = {**self.CLONE_RUNNING, "operator_assigned": True, "campaigns_timeout_hours": 90,
                      "scan": {"status": "COMPLETED", "last_recon_scan_ms": 10}}
        result = audit.view("case3", None, clone_done, self.CLONE_AT, ("user_created_verified", NOW))
        self.assertTrue(audit.all_done(result))
        self.assertTrue(all(c["source"] == "clone" for c in result["checks"] if c["key"] != "primary_user"))

    def test_a_known_older_value_beats_a_newer_unknown(self):
        line = audit.log_line("CO-0001", NOW, "scan_completed", {}, None)  # live read with nothing usable
        result = audit.view("ce_only", line, {"operator_assigned": True, "v2": {"ce_status": "DONE"}}, self.CLONE_AT)
        self.assertTrue(audit.all_done(result))

    def test_no_data_is_unknown_never_done(self):
        result = audit.view("ce_only", None, None, None)
        self.assertEqual(result["unknown"], 2)
        self.assertFalse(audit.all_done(result))
        self.assertFalse(result["finished"])


class LogTests(AuditTestCase):
    def test_append_and_latest(self):
        audit.append(self.parent, audit.log_line("CO-0001", NOW - timedelta(hours=2), "scan_running", {}, None))
        audit.append(self.parent, audit.log_line("CO-0001", NOW, "scan_completed", {}, None))
        (self.parent / records.RECORDS_SUBDIR / audit.AUDIT_LOG).open("a").write("not json\n")
        self.assertEqual(audit.latest_live(self.parent)["CO-0001"]["state"], "scan_completed")

    def test_live_due(self):
        self.assertTrue(audit.live_due(None, NOW, None))
        self.assertFalse(audit.live_due(NOW - timedelta(minutes=30), NOW, None))
        self.assertTrue(audit.live_due(NOW - timedelta(minutes=91), NOW, None))
        self.assertTrue(audit.live_due(NOW - timedelta(minutes=30), NOW, NOW - timedelta(minutes=5)))

    def test_window_signed_in_since(self):
        log = "2026-11-05T22:41:49Z close x\n2026-11-06T20:30:41Z launched no_deadline\n2026-11-06T20:31:18Z kept run_end\n"
        self.assertEqual(audit.window_signed_in_since(log), datetime(2026, 11, 6, 20, 30, 41, tzinfo=timezone.utc))

    def test_card_escapes_and_names_sources(self):
        line = audit.log_line("CO-0841", NOW, "scan_completed", {"operator_assigned": False}, None)
        html = audit.card_html(audit.view("case3", line, None, None))
        self.assertIn("After onboarding: BackOffice audit", html)
        self.assertIn("BackOffice", html)
        self.assertNotIn("<script", html)


class Case3ScanMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.inputs = case3_followup.FollowupInputs(reference="CO-0841", records_parent=self.parent, now=NOW, vm=False,
                                                    sf_ready=True)
        self.verified = [{"outcome": "readback_verified", "route": "case_3_combined_baseline", "co_reference": "CO-0841",
                          "recorded_at": "2026-11-05T20:44:44Z", "account_id": "a" * 24, "account_uuid": UUID}]
        self.row = {"Id": "a0B000000000001AAA", "Onboarding_Product__c": "Surface & Credential Exposure",
                    "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Stage__c": "Account Scanning"}
        constants = mock.patch.object(case3_followup, "_both_constants", return_value=True)
        constants.start()
        self.addCleanup(constants.stop)

    def _run(self, rows, accepted=True, scans_done=True):
        calls = []
        reads = iter(rows)

        def run(argv):
            calls.append(argv)
            return accepted

        result = case3_followup.after_scan_finished(self.inputs, self.verified, lambda: next(reads), run, "surface-onboarding",
                                                    scans_done=scans_done)
        return result, calls

    def test_moves_once_and_reads_back(self):
        after = {**self.row, "Onboarding_Stage__c": "Scan Completed Successfully"}
        result, calls = self._run([self.row, after])
        self.assertEqual(result, "written_verified")
        self.assertEqual(len(calls), 1)
        self.assertIn("Onboarding_Stage__c='Scan Completed Successfully'", calls[0])
        self.assertEqual(case3_followup.last_stage(self.parent, "CO-0841"), "Scan Completed Successfully")
        # a later call with the stage already moved writes nothing
        again, calls2 = self._run([after])
        self.assertEqual((again, calls2), ("already_present", []))

    def test_refusals_write_nothing(self):
        self.assertEqual(self._run([self.row], scans_done=False), ("scan_not_finished", []))
        self.assertEqual(self._run([{**self.row, "Onboarding_Stage__c": "Request Approved"}]), ("stage_not_expected", []))
        self.assertEqual(self._run([{**self.row, "Onboarding_Product__c": "Credential Exposure"}]), ("route_not_case3_new", []))
        vm = case3_followup.FollowupInputs(reference="CO-0841", records_parent=self.parent, now=NOW, vm=True, sf_ready=True)
        self.assertEqual(case3_followup.after_scan_finished(vm, self.verified, lambda: self.row, lambda a: True,
                                                            "surface-onboarding", scans_done=True), "vm_read_only")

    def test_uncertain_write_is_never_repeated(self):
        result, _ = self._run([self.row, self.row])  # accepted, but the re-read still shows Account Scanning
        self.assertEqual(result, "write_uncertain")
        self.assertEqual(self._run([self.row]), ("stage_write_needs_review", []))


if __name__ == "__main__":
    unittest.main()
