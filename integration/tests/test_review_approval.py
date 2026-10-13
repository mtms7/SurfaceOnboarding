import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integration.onboarding import review_approval as ra

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
SUMMARY = {"reference": "CO-0780", "route": "case_1_new_surface_only", "tenant": "Acme", "domains": ["acme.example"]}


class SummaryHashTests(unittest.TestCase):
    def test_hash_is_stable_independent_of_key_order_and_changes_with_content(self):
        digest = ra.summary_hash(SUMMARY)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(digest, ra.summary_hash(dict(reversed(list(SUMMARY.items())))))
        self.assertNotEqual(digest, ra.summary_hash({**SUMMARY, "tenant": "Acme Two"}))
        self.assertNotEqual(digest, ra.summary_hash({**SUMMARY, "domains": ["acme.example", "x.example"]}))


class ApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "approvals.json"
        self.digest = ra.summary_hash(SUMMARY)

    def issue(self, **over):
        args = dict(reference="CO-0780", route="r1", source_revision="rev1", digest=self.digest,
                    operator="milton.stevenson@pentera.io", now=NOW)
        args.update(over)
        return ra.issue(self.path, **args)

    def check(self, **over):
        args = dict(reference="CO-0780", route="r1", source_revision="rev1", digest=self.digest, now=NOW)
        args.update(over)
        return ra.check(self.path, **args)

    def test_nothing_is_approved_by_default(self):
        self.assertEqual(self.check(), "approval_missing")

    def test_issue_then_valid_for_exactly_this_summary(self):
        record = self.issue()
        self.assertEqual(record["expires_at"], "2026-10-13T16:00:00Z")
        self.assertIsNone(self.check())
        self.assertIsNone(self.check(now=NOW + timedelta(hours=3, minutes=59)))

    def test_refusals_for_each_kind_of_drift(self):
        self.issue()
        self.assertEqual(self.check(now=NOW + timedelta(hours=4)), "approval_expired")
        self.assertEqual(self.check(route="r2"), "route_changed")
        self.assertEqual(self.check(source_revision="rev2"), "revision_changed")
        self.assertEqual(self.check(digest=ra.summary_hash({**SUMMARY, "tenant": "Other"})), "summary_changed")
        self.assertEqual(self.check(reference="CO-0781"), "approval_missing")

    def test_consume_is_one_use(self):
        self.issue()
        self.assertIsNone(ra.consume(self.path, "CO-0780", "r1", "rev1", self.digest, NOW))
        self.assertEqual(ra.consume(self.path, "CO-0780", "r1", "rev1", self.digest, NOW), "approval_consumed")
        self.assertEqual(self.check(), "approval_consumed")

    def test_consume_refuses_without_changing_the_file_on_drift(self):
        self.issue()
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(ra.consume(self.path, "CO-0780", "r1", "rev2", self.digest, NOW), "revision_changed")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_new_approval_replaces_the_old_one_after_the_co_was_fixed(self):
        self.issue()
        fixed = ra.summary_hash({**SUMMARY, "tenant": "Acme Fixed"})
        self.assertEqual(self.check(digest=fixed), "summary_changed")
        self.issue(digest=fixed, source_revision="rev2")
        self.assertIsNone(self.check(digest=fixed, source_revision="rev2"))
        self.assertEqual(self.check(), "revision_changed")

    def test_invalid_requests_are_refused(self):
        for over in ({"reference": "CO-1"}, {"reference": "co-0780"}, {"route": ""}, {"source_revision": ""},
                     {"digest": "abc"}, {"operator": ""}, {"operator": "two words"}, {"operator": "a<b"},
                     {"now": datetime(2026, 10, 13)}):
            with self.subTest(over=over), self.assertRaises((ra.ApprovalRefused, ValueError)):
                self.issue(**over)
        with self.assertRaises(ra.ApprovalRefused):
            ra.issue(self.path, "CO-0780", "r1", "rev1", self.digest, "a@b.io", NOW, ttl=timedelta(days=2))
        self.assertFalse(self.path.exists())

    def test_corrupt_files_fail_closed(self):
        for text in ("not json", "[]", json.dumps({"CO-0780": "x"}), json.dumps({"bad": {}}),
                     json.dumps({"CO-0780": {"operator": "a@b.io"}}),
                     json.dumps({"CO-0780": {"operator": "a", "approved_at": "x", "expires_at": "x", "route": "r",
                                             "source_revision": "v", "summary_hash": "h", "extra": 1}})):
            with self.subTest(text=text[:20]):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ra.ApprovalUnavailable):
                    self.check()
                with self.assertRaises(ra.ApprovalUnavailable):
                    self.issue()

    def test_bad_expiry_text_is_expired_not_approved(self):
        self.issue()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["CO-0780"]["expires_at"] = "tomorrow"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.check(), "approval_expired")

    def test_no_temp_files_are_left_behind(self):
        self.issue()
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["approvals.json"])


if __name__ == "__main__":
    unittest.main()
