import json
import os
import stat
import contextlib
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integration.onboarding import production_approval as pa
from integration.onboarding import session_readiness
from integration.onboarding.models import EngineValue

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
REF = "PROD-CE-0797-20261016"
CO = "CO-0797"
ROUTE = EngineValue.CASE_2.value
REV = "2026-10-15T09:30:00.000+0000"
ISSUERS = frozenset({"owner@example.test"})
OWNER = "owner@example.test"


class ProductionApprovalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / pa.STATE_FILE_NAME

    def issue(self, **overrides):
        args = dict(approval_ref=REF, co_reference=CO, route=ROUTE, source_revision=REV, issued_by=OWNER,
                    allowed_issuers=ISSUERS, now=NOW)
        args.update(overrides)
        return pa.issue(self.path, **args)

    def refusal(self, **overrides):
        with self.assertRaises(pa.ApprovalRefused) as caught:
            self.issue(**overrides)
        return caught.exception.reason

    def check(self, **overrides):
        args = dict(approval_ref=REF, co_reference=CO, route=ROUTE, source_revision=REV, now=NOW)
        args.update(overrides)
        return pa.check(self.path, **args)

    def consume(self, **overrides):
        args = dict(approval_ref=REF, co_reference=CO, route=ROUTE, source_revision=REV, now=NOW)
        args.update(overrides)
        return pa.consume(self.path, **args)

    # --- issue ---
    def test_issue_records_bound_fields_with_seven_day_default(self):
        record = self.issue()
        self.assertEqual(record["co_reference"], CO)
        self.assertEqual(record["route"], ROUTE)
        self.assertEqual(record["scope"], "ce_only_single_co")
        self.assertEqual(record["source_revision"], REV)
        self.assertEqual(record["issued_by"], OWNER)
        self.assertEqual(record["issued_at"], "2026-10-16T12:00:00Z")
        self.assertEqual(record["expires_at"], "2026-10-23T12:00:00Z")
        self.assertNotIn("consumed_at", record)
        self.assertIsNone(self.check())

    def test_record_holds_only_allowed_fields(self):
        record = self.issue()
        self.assertTrue(set(record) <= pa.RECORD_FIELDS)
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(stored), {REF})
        self.assertTrue(set(stored[REF]) <= pa.RECORD_FIELDS)
        self.consume()
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(set(stored[REF]) <= pa.RECORD_FIELDS)
        self.assertIn("consumed_at", stored[REF])

    def test_route_must_be_ce_only(self):
        for route in (EngineValue.CASE_1.value, EngineValue.CASE_3.value, "case_2_ce_only", "", None, 5):
            self.assertEqual(self.refusal(route=route), "route_not_ce_only")
        self.assertFalse(self.path.exists())

    def test_issuer_must_be_in_allowed_set(self):
        self.assertEqual(self.refusal(issued_by="other@example.test"), "issuer_not_allowed")
        self.assertEqual(self.refusal(allowed_issuers=frozenset()), "issuer_not_allowed")
        self.assertEqual(self.refusal(issued_by=""), "issuer_not_allowed")
        self.assertEqual(self.refusal(allowed_issuers=["owner@example.test"]), "issuer_not_allowed")
        self.assertFalse(self.path.exists())

    def test_bad_references_and_revision(self):
        for bad in ("short", "lower-case-ref", "PROD CE", "", None, "A" * 65):
            self.assertEqual(self.refusal(approval_ref=bad), "invalid_approval_ref")
        for bad in ("CO-12", "co-0797", "CO-0797 ", "", None, "CO-12345678901"):
            self.assertEqual(self.refusal(co_reference=bad), "invalid_co_reference")
        for bad in ("", None, "x" * 129, 7):
            self.assertEqual(self.refusal(source_revision=bad), "invalid_revision")

    def test_lifetime_bounds(self):
        self.assertEqual(self.refusal(lifetime=timedelta(days=31, seconds=1)), "invalid_lifetime")
        self.assertEqual(self.refusal(lifetime=timedelta(0)), "invalid_lifetime")
        self.assertEqual(self.refusal(lifetime=timedelta(days=-1)), "invalid_lifetime")
        self.assertEqual(self.refusal(lifetime=None), "invalid_lifetime")
        record = self.issue(lifetime=session_readiness.MAX_APPROVAL_LIFETIME)
        self.assertEqual(record["expires_at"], "2026-11-16T12:00:00Z")

    def test_naive_datetime_refused(self):
        self.assertEqual(self.refusal(now=datetime(2026, 10, 16, 12, 0, 0)), "naive_datetime")
        self.assertEqual(self.refusal(now="2026-10-16"), "naive_datetime")

    def test_one_active_approval_at_a_time(self):
        self.issue()
        self.assertEqual(self.refusal(approval_ref="PROD-CE-0800-20261016", co_reference="CO-0800"),
                         "other_approval_active")
        # the same CO and the same revision may not hold a second live approval
        self.assertEqual(self.refusal(approval_ref="PROD-CE-0797-20261017", now=NOW + timedelta(hours=1)),
                         "approval_already_active_for_co")

    def test_changed_revision_supersedes_and_never_deletes(self):
        self.issue()
        later = NOW + timedelta(hours=1)
        self.issue(approval_ref="PROD-CE-0797-20261017", source_revision=REV + "x", now=later)
        records = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(records), {REF, "PROD-CE-0797-20261017"})
        self.assertEqual(records[REF]["superseded_at"], "2026-10-16T13:00:00Z")
        self.assertEqual(self.check(now=later), "approval_superseded")
        self.assertEqual(self.consume(now=later), "approval_superseded")
        self.assertIsNone(self.check(approval_ref="PROD-CE-0797-20261017", source_revision=REV + "x", now=later))

    def test_issue_refuses_while_the_lock_is_held_and_never_removes_it(self):
        lock = self.path.with_name(self.path.name + pa.LOCK_SUFFIX)
        lock.write_text("", encoding="utf-8")
        self.assertEqual(self.refusal(), "approval_locked")
        self.assertTrue(lock.exists())
        self.assertFalse(self.path.exists())

    def test_superseded_at_must_be_a_stamp(self):
        self.issue()
        records = json.loads(self.path.read_text(encoding="utf-8"))
        records[REF]["superseded_at"] = "yesterday"
        self.path.write_text(json.dumps(records), encoding="utf-8")
        self.assertEqual(self.check(), "approval_unreadable")

    def test_other_co_allowed_after_consumed_or_expired(self):
        self.issue()
        self.assertIsNone(self.consume())
        self.issue(approval_ref="PROD-CE-0800-20261016", co_reference="CO-0800")
        later = NOW + timedelta(days=8)
        self.issue(approval_ref="PROD-CE-0900-20261024", co_reference="CO-0900", now=later)

    def test_approval_ref_cannot_be_reused(self):
        self.issue()
        self.assertEqual(self.refusal(), "approval_ref_reused")
        self.consume()
        self.assertEqual(self.refusal(), "approval_ref_reused")

    def test_issue_refuses_on_corrupt_file(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.refusal(), "approval_unreadable")

    # --- check / consume reason codes ---
    def test_missing(self):
        self.assertEqual(self.check(), "approval_missing")
        self.assertEqual(self.consume(), "approval_missing")
        self.issue()
        self.assertEqual(self.check(approval_ref="PROD-CE-0001-20261016"), "approval_missing")

    def test_expired_and_boundary(self):
        self.issue()
        expires = NOW + timedelta(days=7)
        self.assertIsNone(self.check(now=expires - timedelta(seconds=1)))
        self.assertEqual(self.check(now=expires), "approval_expired")
        self.assertEqual(self.check(now=expires + timedelta(seconds=1)), "approval_expired")
        self.assertEqual(self.consume(now=expires), "approval_expired")
        self.assertIsNone(self.consume(now=expires - timedelta(seconds=1)))

    def test_naive_now_in_check_fails_closed(self):
        self.issue()
        self.assertEqual(self.check(now=datetime(2026, 10, 17)), "approval_expired")
        self.assertEqual(self.consume(now=datetime(2026, 10, 17)), "approval_expired")
        self.assertIsNone(self.check())

    def test_not_yet_valid_is_not_usable(self):
        self.issue()
        self.assertEqual(self.check(now=NOW - timedelta(seconds=1)), "approval_expired")

    def test_wrong_co(self):
        self.issue()
        self.assertEqual(self.check(co_reference="CO-0800"), "approval_co_mismatch")
        self.assertEqual(self.consume(co_reference="CO-0800"), "approval_co_mismatch")
        self.assertIsNone(self.check())

    def test_wrong_route(self):
        self.issue()
        for route in (EngineValue.CASE_1.value, "case_2_ce_only", "", None):
            self.assertEqual(self.check(route=route), "approval_route_mismatch")
        self.assertEqual(self.consume(route=EngineValue.CASE_3.value), "approval_route_mismatch")
        self.assertIsNone(self.check())

    def test_revision_drift(self):
        self.issue()
        self.assertEqual(self.check(source_revision=REV + "x"), "approval_revision_drift")
        self.assertEqual(self.consume(source_revision="other"), "approval_revision_drift")
        self.assertEqual(self.consume(source_revision=None), "approval_revision_drift")
        self.assertIsNone(self.check())

    def test_failed_consume_does_not_consume(self):
        self.issue()
        self.assertEqual(self.consume(co_reference="CO-0800"), "approval_co_mismatch")
        self.assertIsNone(self.consume())

    def test_consume_once(self):
        self.issue()
        self.assertIsNone(self.consume())
        self.assertEqual(self.consume(), "approval_consumed")
        self.assertEqual(self.check(), "approval_consumed")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored[REF]["consumed_at"], "2026-10-16T12:00:00Z")

    def test_consumed_takes_precedence_over_mismatch(self):
        self.issue()
        self.consume()
        self.assertEqual(self.check(co_reference="CO-0800"), "approval_consumed")

    # --- cross-process consume guard ---
    def lock_path(self):
        return self.path.with_name(self.path.name + ".lock")

    def test_existing_lock_fails_closed_and_is_left_alone(self):
        self.issue()
        before = self.path.read_text(encoding="utf-8")
        self.lock_path().write_text("", encoding="utf-8")
        self.assertEqual(self.consume(), "approval_locked")
        self.assertTrue(self.lock_path().exists())
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_stale_lock_is_reported_never_deleted(self):
        self.issue()
        self.lock_path().write_text("", encoding="utf-8")
        old = time.time() - 61
        os.utime(self.lock_path(), (old, old))
        self.assertEqual(self.consume(), "approval_locked_stale")
        self.assertTrue(self.lock_path().exists())
        self.assertNotIn("consumed_at", json.loads(self.path.read_text(encoding="utf-8"))[REF])

    def test_lock_is_released_after_success_and_after_refusal(self):
        self.issue()
        self.assertEqual(self.consume(co_reference="CO-0800"), "approval_co_mismatch")
        self.assertFalse(self.lock_path().exists())
        self.assertIsNone(self.consume())
        self.assertFalse(self.lock_path().exists())
        self.assertEqual(self.consume(), "approval_consumed")
        self.assertFalse(self.lock_path().exists())

    def test_lock_is_released_when_the_write_fails(self):
        self.issue()
        original = os.replace

        def boom(*_a, **_k):
            raise OSError("disk")
        pa.os.replace = boom
        try:
            with self.assertRaises(OSError):
                self.consume()
        finally:
            pa.os.replace = original
        self.assertFalse(self.lock_path().exists())
        self.assertIsNone(self.consume())

    def test_two_threads_exactly_one_consume_succeeds(self):
        self.issue()
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            results.append(self.consume())
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(None), 1, results)
        self.assertEqual(len(results), 2)

    def test_file_lock_alone_keeps_the_exactly_one_property(self):
        """Without the in-process lock (a second process) the lock file must still allow only one consume."""
        self.issue()
        results = []
        barrier = threading.Barrier(8)
        original = pa._lock
        pa._lock = contextlib.nullcontext()
        try:
            def worker():
                barrier.wait()
                results.append(self.consume())
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            pa._lock = original
        self.assertEqual(results.count(None), 1, results)
        self.assertTrue(all(r in (None, "approval_locked", "approval_consumed") for r in results), results)
        self.assertFalse(self.lock_path().exists())

    # --- corrupt / partial files ---
    def test_corrupt_files_mean_unreadable(self):
        valid = {"approval_ref": REF, "co_reference": CO, "route": ROUTE, "scope": "ce_only_single_co",
                 "source_revision": REV, "issued_by": OWNER, "issued_at": "2026-10-16T12:00:00Z",
                 "expires_at": "2026-10-23T12:00:00Z"}
        cases = {
            "garbage": "{not json",
            "empty": "",
            "list": "[]",
            "null": "null",
            "bad_key": json.dumps({"lower": valid}),
            "record_not_dict": json.dumps({REF: "x"}),
            "missing_field": json.dumps({REF: {k: v for k, v in valid.items() if k != "source_revision"}}),
            "extra_field": json.dumps({REF: {**valid, "password": "x"}}),
            "bad_time": json.dumps({REF: {**valid, "expires_at": "tomorrow"}}),
            "bad_scope": json.dumps({REF: {**valid, "scope": "everything"}}),
            "wrong_route": json.dumps({REF: {**valid, "route": EngineValue.CASE_1.value}}),
            "key_mismatch": json.dumps({"PROD-CE-0001-20261016": valid}),
            "bad_co": json.dumps({REF: {**valid, "co_reference": "x"}}),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.path.write_text(text, encoding="utf-8")
                self.assertEqual(self.check(), "approval_unreadable")
                self.assertEqual(self.consume(), "approval_unreadable")
                self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_valid_handwritten_file_is_accepted(self):
        record = self.issue()
        self.path.write_text(json.dumps({REF: record}), encoding="utf-8")
        self.assertIsNone(self.check())

    def test_unreadable_when_path_is_directory(self):
        self.path.mkdir()
        self.assertEqual(self.check(), "approval_unreadable")

    def test_failed_write_leaves_no_temp_files_and_original_intact(self):
        self.issue()
        before = self.path.read_text(encoding="utf-8")
        original = os.replace

        def boom(*_a, **_k):
            raise OSError("disk")
        pa.os.replace = boom
        try:
            with self.assertRaises(OSError):
                self.consume()
        finally:
            pa.os.replace = original
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [pa.STATE_FILE_NAME])
        self.assertIsNone(self.consume())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits only")
    def test_file_is_private(self):
        self.issue()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.consume()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_state_file_name_is_separate_from_review_approvals(self):
        self.assertEqual(pa.STATE_FILE_NAME, "production_create_approvals.json")

    # --- unlock_environ ---
    def test_unlock_environ_has_only_three_values(self):
        record = self.issue()
        env = pa.unlock_environ(record)
        self.assertEqual(set(env), {"SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF",
                                    "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_ISSUED",
                                    "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES"})
        self.assertNotIn("SURFACE_ONBOARDING_PRODUCTION_SESSIONS", env)
        self.assertEqual(env["SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"], REF)

    def test_unlock_environ_feeds_session_readiness(self):
        env = pa.unlock_environ(self.issue())
        full = {**env, "SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1"}
        # Everything but the (still False) runner flag passes; the guard is untouched.
        self.assertFalse(session_readiness.PRODUCTION_RUNNER_SUPPORTED)
        self.assertEqual(session_readiness.production_unlock_problem(full, NOW), "production_runner_not_supported")
        self.assertEqual(session_readiness.production_unlock_problem(full, NOW + timedelta(days=8)),
                         "production_approval_expired")
        self.assertEqual(session_readiness.production_unlock_problem(env, NOW), "production_sessions_flag_missing")

    def test_unlock_environ_rejects_bad_record(self):
        with self.assertRaises(pa.ApprovalRefused):
            pa.unlock_environ({"approval_ref": REF})
        with self.assertRaises(pa.ApprovalRefused):
            pa.unlock_environ(None)


if __name__ == "__main__":
    unittest.main()
