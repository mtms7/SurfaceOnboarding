"""A tenant verified live by the read-only Case 3 readback counts as onboarded before the clone catches up (owner request 2026-10-21).

The readback leaves one ``readback_verified`` record (route, tenant name, Account ID, Account UUID). The production match of a CO whose
Account UUID is set but not yet in the clone becomes ``onboarded`` only when such a record carries exactly the CO's own Account UUID.
Pure and hermetic: no I/O.
"""

from __future__ import annotations

import unittest

from integration.onboarding import verified_tenant as vt

UUID = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"
ROW_ID = "0123456789abcdef01234567"
CASE3 = "case_3_combined_baseline"


def record(**over):
    data = {"co_reference": "CO-0800", "outcome": "readback_verified", "route": CASE3, "tenant_name": "Example TG",
            "account_id": ROW_ID, "account_uuid": UUID, "recorded_at": "2026-10-21T21:00:00Z", "redactions": 0}
    data.update(over)
    return data


def match(status="uuid_not_in_clone", **over):
    data = {"status": status, "reason": "UUID set but tenant not found in clone (the clone may be older than the create).",
            "fields": [], "differs": [], "candidates": [], "target": None, "captured_at": "2026-10-21T20:11:31Z",
            "age_seconds": 1500, "tenants_checked": 5449, "identity_only": True}
    data.update(over)
    return data


class LatestTests(unittest.TestCase):
    def test_the_newest_matching_record_wins(self):
        records = [record(recorded_at="2026-10-21T20:00:00Z", tenant_name="Old"), record(), record(outcome="dry_run_passed")]
        self.assertEqual(vt.latest_verified(records, "CO-0800", UUID)["tenant_name"], "Example TG")

    def test_uuid_formatting_is_normalised_but_a_different_uuid_never_matches(self):
        self.assertIsNotNone(vt.latest_verified([record()], "CO-0800", UUID.upper()))
        self.assertIsNotNone(vt.latest_verified([record()], "CO-0800", " " + UUID + " "))
        self.assertIsNone(vt.latest_verified([record()], "CO-0800", OTHER))
        self.assertIsNone(vt.latest_verified([record()], "CO-0800", None))
        self.assertIsNone(vt.latest_verified([record()], "CO-0800", ""))

    def test_only_a_case_3_readback_verified_record_of_this_co_counts(self):
        for bad in (record(outcome="dry_run_passed"), record(outcome="created_unverified"), record(route="case_2_new_ce_only"),
                    record(co_reference="CO-0801"), record(account_uuid="not-a-uuid"), record(account_id="short"),
                    record(tenant_name=""), record(recorded_at="yesterday")):
            self.assertIsNone(vt.latest_verified([bad], "CO-0800", UUID), bad)

    def test_garbage_input_never_raises(self):
        for records in (None, "x", [None, 5, {}], [{"outcome": "readback_verified"}]):
            self.assertIsNone(vt.latest_verified(records, "CO-0800", UUID))


class ApplyTests(unittest.TestCase):
    def test_a_uuid_not_in_clone_becomes_onboarded_with_the_live_evidence(self):
        result = vt.apply(match(), vt.latest_verified([record()], "CO-0800", UUID))
        self.assertEqual(result["status"], "onboarded")
        self.assertIn("verified live", result["reason"])
        self.assertIn("2026-10-21 21:00 UTC", result["reason"])
        self.assertEqual(result["review"], {"line": result["reason"], "warnings": []})
        self.assertEqual(result["target"]["id"], ROW_ID)
        self.assertEqual(result["target"]["name"], "Example TG")
        self.assertEqual(result["verified_live"], {"recorded_at": "2026-10-21T21:00:00Z", "account_id": ROW_ID,
                                                   "account_uuid": UUID, "tenant_name": "Example TG"})
        self.assertEqual(result["tenants_checked"], 5449)  # the clone facts stay as they were

    def test_every_other_status_is_left_untouched(self):
        verified = vt.latest_verified([record()], "CO-0800", UUID)
        for status in ("onboarded", "ambiguous", "exists_other", "exists_differs", "clone_unavailable", "not_checked",
                       "not_in_production"):
            original = match(status=status)
            self.assertEqual(vt.apply(dict(original), verified), original, status)

    def test_without_a_verified_record_nothing_changes(self):
        original = match()
        self.assertEqual(vt.apply(dict(original), None), original)

    def test_the_input_is_not_mutated(self):
        original = match()
        before = dict(original)
        vt.apply(original, vt.latest_verified([record()], "CO-0800", UUID))
        self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()
