"""Owner request 2026-10-22: a guarded way to retire ONE unused production approval (for example CO-0800's, which was never consumed because
the tenant was created by hand), so the next CO can be approved (only one approval may be live at a time).

``production_approval.revoke`` sets ``superseded_at`` on one live approval under the same cross-process lock as issue/consume and
changes nothing else. ``tools/revoke_production_approval.py`` is the owner-run, interactive tool around it: desktop only, the
approval's issuer must be an allowed issuer, a typed phrase, a dry run that writes nothing. Hermetic: temporary folders.
"""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integration.onboarding import production_approval as pa
from tools import revoke_production_approval as tool

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
OWNER = "owner@example.test"
ISSUERS = frozenset({OWNER})
CE = pa.ALLOWED_ROUTE
CASE3 = pa.CASE3_ROUTE
REF_A, REF_B = "PROD-C3-0800-20261020", "PROD-CE-0798-20261017"
REV = "2026-10-20T00:35:37.000+0000"


class RevokeFunctionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / pa.STATE_FILE_NAME

    def issue(self, ref=REF_A, co="CO-0800", route=CASE3, when=NOW - timedelta(days=2), **over):
        if route == CASE3:
            over.setdefault("tenant_name", "Example Tenant")
        return pa.issue(self.path, ref, co, route, REV, OWNER, ISSUERS, when, **over)

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_a_live_approval_is_superseded_and_nothing_else_changes(self):
        self.issue()
        before = self.stored()[REF_A]
        record = pa.revoke(self.path, REF_A, NOW)
        self.assertEqual(record["superseded_at"], "2026-10-22T12:00:00Z")
        after = self.stored()[REF_A]
        self.assertEqual({k: v for k, v in after.items() if k != "superseded_at"}, before)
        self.assertEqual(pa.check(self.path, REF_A, "CO-0800", CASE3, REV, NOW), "approval_superseded")

    def test_it_frees_the_next_co(self):
        self.issue()
        with self.assertRaises(pa.ApprovalRefused) as blocked:
            self.issue(ref="PROD-C3-0802-20261022", co="CO-0802", when=NOW)
        self.assertEqual(blocked.exception.reason, "other_approval_active")
        pa.revoke(self.path, REF_A, NOW)
        self.assertEqual(self.issue(ref="PROD-C3-0802-20261022", co="CO-0802", when=NOW)["co_reference"], "CO-0802")

    def test_only_the_named_approval_is_touched(self):
        self.issue(ref=REF_B, co="CO-0798", route=CE, when=NOW - timedelta(days=5))
        pa.consume(self.path, REF_B, "CO-0798", CE, REV, NOW - timedelta(days=4))
        self.issue()
        pa.revoke(self.path, REF_A, NOW)
        data = self.stored()
        self.assertNotIn("superseded_at", data[REF_B])
        self.assertIn("consumed_at", data[REF_B])

    def test_unusable_approvals_are_refused_and_nothing_is_written(self):
        self.issue()
        pa.revoke(self.path, REF_A, NOW)
        snapshot = self.path.read_text(encoding="utf-8")
        for ref, now, reason in ((REF_A, NOW, "approval_superseded"), ("PROD-C3-0000-20261022", NOW, "approval_missing"),
                                 ("not a ref", NOW, "invalid_approval_ref")):
            with self.subTest(reason=reason), self.assertRaises(pa.ApprovalRefused) as caught:
                pa.revoke(self.path, ref, now)
            self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.path.read_text(encoding="utf-8"), snapshot)

    def test_consumed_expired_and_naive_times_are_refused(self):
        self.issue(ref=REF_B, co="CO-0798", route=CE, when=NOW - timedelta(days=1))
        pa.consume(self.path, REF_B, "CO-0798", CE, REV, NOW - timedelta(hours=1))
        with self.assertRaises(pa.ApprovalRefused) as consumed:
            pa.revoke(self.path, REF_B, NOW)
        self.assertEqual(consumed.exception.reason, "approval_consumed")
        self.setUp()
        self.issue()
        with self.assertRaises(pa.ApprovalRefused) as expired:
            pa.revoke(self.path, REF_A, NOW + timedelta(days=30))
        self.assertEqual(expired.exception.reason, "approval_expired")
        with self.assertRaises(pa.ApprovalRefused) as naive:
            pa.revoke(self.path, REF_A, datetime(2026, 10, 22, 12, 0, 0))
        self.assertEqual(naive.exception.reason, "naive_datetime")

    def test_an_unreadable_file_or_a_held_lock_fails_closed(self):
        self.path.write_text("{garbage", encoding="utf-8")
        with self.assertRaises(pa.ApprovalRefused) as unreadable:
            pa.revoke(self.path, REF_A, NOW)
        self.assertEqual(unreadable.exception.reason, "approval_unreadable")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{garbage")
        self.setUp()
        self.issue()
        lock = self.path.with_name(self.path.name + pa.LOCK_SUFFIX)
        lock.write_text("held", encoding="utf-8")
        with self.assertRaises(pa.ApprovalRefused):
            pa.revoke(self.path, REF_A, NOW)
        self.assertNotIn("superseded_at", self.stored()[REF_A])


class Run:
    def __init__(self, test, argv, *, env=None, tty=True, typed=None, now=NOW, state=None, ref=REF_A):
        self.typed_prompts = []
        out = io.StringIO()
        phrase = typed if typed is not None else "REVOKE " + ref
        with contextlib.redirect_stdout(out):
            self.code = tool.main(argv, now_fn=lambda: now, environ=dict({"SURFACE_ONBOARDING_APPROVAL_ISSUERS": OWNER}
                                                                      if env is None else env),
                                  input_fn=lambda p="": self.typed_prompts.append(p) or phrase, isatty_fn=lambda: tty,
                                  state_path=state)
        self.out = out.getvalue()

    def result(self):
        return json.loads(self.out.strip().splitlines()[-1])


class ToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / pa.STATE_FILE_NAME
        pa.issue(self.path, REF_A, "CO-0800", CASE3, REV, OWNER, ISSUERS, NOW - timedelta(days=2), tenant_name="Example Tenant")

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def run_tool(self, extra=(), **kw):
        return Run(self, ["--approval-ref", REF_A, *extra], state=self.path, **kw)

    def test_the_correct_phrase_revokes_exactly_that_approval(self):
        run = self.run_tool()
        self.assertEqual(run.code, 0, run.out)
        self.assertEqual(run.result(), {"result": "revoked", "approval_ref": REF_A})
        self.assertIn("superseded_at", self.stored()[REF_A])
        self.assertIn("CO-0800", run.out)
        self.assertIn(REF_A, run.typed_prompts[0])

    def test_a_dry_run_shows_the_summary_and_writes_nothing(self):
        before = self.path.read_text(encoding="utf-8")
        run = self.run_tool(["--dry-run"])
        self.assertEqual(run.code, 0, run.out)
        self.assertIn("CO-0800", run.out)
        self.assertIn("Dry run: nothing was written.", run.out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(run.typed_prompts, [])

    def test_everything_else_writes_nothing(self):
        before = self.path.read_text(encoding="utf-8")
        cases = (("wrong phrase", dict(typed="REVOKE something")), ("empty phrase", dict(typed="")),
                 ("not a tty", dict(tty=False)), ("vm", dict(env={"SURFACE_ONBOARDING_RUNTIME": "vm",
                                                                 "SURFACE_ONBOARDING_APPROVAL_ISSUERS": OWNER})),
                 ("no issuer allow-list", dict(env={})),
                 ("issuer not allowed", dict(env={"SURFACE_ONBOARDING_APPROVAL_ISSUERS": "someone.else@example.test"})))
        for label, kw in cases:
            with self.subTest(case=label):
                run = self.run_tool(**kw)
                self.assertEqual(run.code, 2, (label, run.out))
                self.assertEqual(run.result()["result"], "refused")
                self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_unknown_and_invalid_references_and_arguments_refuse(self):
        before = self.path.read_text(encoding="utf-8")
        for argv in (["--approval-ref", "PROD-C3-0000-20261022"], ["--approval-ref", "bad ref"], [], ["--approval-ref", REF_A, "--x"]):
            run = Run(self, argv, state=self.path)
            self.assertEqual(run.code, 2, argv)
            self.assertEqual(run.result()["result"], "refused")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_second_revoke_and_an_eof_at_the_prompt_write_nothing(self):
        self.run_tool()
        snapshot = self.path.read_text(encoding="utf-8")
        self.assertEqual(self.run_tool().result()["reason"], "approval_superseded")
        self.assertEqual(self.path.read_text(encoding="utf-8"), snapshot)

    def test_output_never_contains_the_tenant_name_or_the_issuer_address(self):
        run = self.run_tool()
        self.assertNotIn("Example Tenant", run.out)
        self.assertNotIn(OWNER, run.out)


if __name__ == "__main__":
    unittest.main()
