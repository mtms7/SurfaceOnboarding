"""docs/56 step 2: the CE scan state of one CO from the production clone row (query 253), matched by Account UUID.

Hermetic: dicts only, no network."""

from __future__ import annotations

import unittest

from integration.onboarding import ce_clone_scan as scan

CREATED = 1_792_400_000_000  # the CO's recorded create time (ms)
UUID = "00000000000000000000000000000001"


def tenant(uuid=UUID, **v2):
    return {"id": "x" + uuid[-3:], "account_uuid": uuid, "v2": v2}


def payload(*tenants):
    return {"row_schema": "v2", "source": {"query_id": 253}, "tenants": list(tenants)}


class StateTests(unittest.TestCase):
    def state(self, **v2):
        return scan.ce_scan_state(v2, CREATED)

    def test_finished_only_when_done_and_ended_after_creation(self):
        self.assertEqual(self.state(ce_status="DONE", ce_start=CREATED + 1, ce_end=CREATED + 5, ce_count=1), scan.FINISHED)

    def test_done_with_errors_is_finished_with_a_warning_state(self):
        self.assertEqual(self.state(ce_status="DONE_WITH_ERRORS", ce_start=CREATED + 1, ce_end=CREATED + 5, ce_count=1),
                         scan.FINISHED_WITH_ERRORS)

    def test_an_older_execution_never_counts(self):
        for end in (CREATED, CREATED - 1, CREATED - 10 ** 9):
            with self.subTest(end=end):
                self.assertEqual(self.state(ce_status="DONE", ce_start=end - 5, ce_end=end, ce_count=1), scan.NOT_YET)

    def test_done_without_an_end_is_not_finished(self):
        self.assertEqual(self.state(ce_status="DONE", ce_start=CREATED + 1, ce_count=1), scan.NOT_YET)

    def test_pending_is_running(self):
        self.assertEqual(self.state(ce_status="PENDING", ce_start=CREATED + 1, ce_count=1), scan.RUNNING)

    def test_pending_from_before_creation_is_not_this_scan(self):
        self.assertEqual(self.state(ce_status="PENDING", ce_start=CREATED - 5, ce_count=1), scan.NOT_YET)

    def test_no_execution_is_not_yet(self):
        self.assertEqual(self.state(ce_count=0), scan.NOT_YET)
        self.assertEqual(self.state(), scan.NOT_YET)

    def test_unknown_status_or_missing_create_time_fails_closed(self):
        self.assertEqual(self.state(ce_status="CANCELLED", ce_start=CREATED + 1, ce_end=CREATED + 5, ce_count=1), scan.UNKNOWN)
        self.assertEqual(scan.ce_scan_state({"ce_status": "DONE", "ce_end": CREATED + 5}, None), scan.UNKNOWN)
        self.assertEqual(scan.ce_scan_state({"ce_status": "DONE", "ce_end": CREATED + 5}, True), scan.UNKNOWN)
        self.assertEqual(scan.ce_scan_state(None, CREATED), scan.UNKNOWN)

    def test_only_finished_states_allow_the_stage_moves(self):
        self.assertTrue(scan.is_finished(scan.FINISHED) and scan.is_finished(scan.FINISHED_WITH_ERRORS))
        for state in (scan.RUNNING, scan.NOT_YET, scan.UNKNOWN):
            self.assertFalse(scan.is_finished(state))


class LookupTests(unittest.TestCase):
    def test_matched_by_the_verified_account_uuid(self):
        v2 = {"ce_status": "DONE"}
        other = tenant("00000000000000000000000000000002", ce_status="PENDING")
        self.assertEqual(scan.clone_v2_for_uuid(payload(other, tenant(**v2)), UUID), v2)

    def test_absent_duplicate_or_malformed_uuid_is_refused(self):
        self.assertIsNone(scan.clone_v2_for_uuid(payload(tenant("00000000000000000000000000000002")), UUID))
        self.assertIsNone(scan.clone_v2_for_uuid(payload(tenant(), tenant()), UUID))  # ambiguous: fail closed
        for bad in ("", "short", None, 5, "G" * 32):
            self.assertIsNone(scan.clone_v2_for_uuid(payload(tenant()), bad))

    def test_a_v1_clone_has_no_scan_columns(self):
        self.assertIsNone(scan.clone_v2_for_uuid({"row_schema": "v1", "tenants": [{"account_uuid": UUID}]}, UUID))


if __name__ == "__main__":
    unittest.main()
