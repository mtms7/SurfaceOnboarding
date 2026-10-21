"""Guarded Case 3 ID write-back PLAN (owner request 2026-10-21): after a tenant is verified live, the Account ID and Account UUID may be written
to ONE Salesforce CO (two fields, nothing else, no stage change). Pure: the plan, the fixed argument list and the after-write check only;
nothing here runs a process or touches Salesforce, and nothing in the dashboard calls it yet. Hermetic.
"""

from __future__ import annotations

import unittest

from integration.onboarding import salesforce_case3_ids_writeback as wb

UUID = "0123456789abcdef0123456789abcdef"
ROW_ID = "0123456789abcdef01234567"
OTHER_UUID = "fedcba9876543210fedcba9876543210"
OTHER_ID = "fedcba9876543210fedcba98"
REV = "2026-10-21T15:03:34.000+0000"
CO_ID = "a0B5g00000AbCdEFGH"
VERIFIED = {"recorded_at": "2026-10-21T21:00:00Z", "account_id": ROW_ID, "account_uuid": UUID, "tenant_name": "Example TG"}


def row(**over):
    data = {"Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
            "Surface_Account_ID__c": None, "Account_UUID__c": None}
    data.update(over)
    return data


def plan(co_row=None, **over):
    args = dict(environment="production", route=wb.CASE3_ROUTE, co_row=co_row if co_row is not None else row(), verified=VERIFIED,
                source_revision_now=REV, source_revision_at_approval=REV, explicit_go=True)
    args.update(over)
    return wb.plan(**args)


class PlanTests(unittest.TestCase):
    def test_two_empty_fields_are_ready_to_write(self):
        result = plan()
        self.assertEqual((result.status, result.reason), ("ready_to_write", "ready"))
        self.assertEqual(result.values, {"Surface_Account_ID__c": ROW_ID, "Account_UUID__c": UUID})

    def test_only_the_empty_field_is_written_when_the_other_is_equal(self):
        result = plan(row(Account_UUID__c=UUID.upper()))
        self.assertEqual((result.status, result.values), ("ready_to_write", {"Surface_Account_ID__c": ROW_ID}))
        result = plan(row(Surface_Account_ID__c=ROW_ID))
        self.assertEqual((result.status, result.values), ("ready_to_write", {"Account_UUID__c": UUID}))

    def test_both_fields_already_equal_is_already_present_and_writes_nothing(self):
        result = plan(row(Surface_Account_ID__c=ROW_ID, Account_UUID__c=UUID))
        self.assertEqual((result.status, result.reason, result.values), ("already_present", "equal", {}))

    def test_a_different_existing_value_is_a_conflict_and_nothing_is_overwritten(self):
        for over in (dict(Account_UUID__c=OTHER_UUID), dict(Surface_Account_ID__c=OTHER_ID),
                     dict(Surface_Account_ID__c=ROW_ID, Account_UUID__c=OTHER_UUID)):
            result = plan(row(**over))
            self.assertEqual((result.status, result.reason, result.values), ("conflict", "different_value", {}), over)

    def test_every_guard_blocks_with_its_own_reason(self):
        cases = (
            (dict(environment="staging"), "environment_not_named"), (dict(co_row="not a row"), "co_row_unreadable"),
            (dict(route="case_2_new_ce_only"), "route_not_case3_new"),
            (dict(co_row=row(Onboarding_Product__c="Credential Exposure")), "route_not_case3_new"),
            (dict(verified=None), "tenant_not_verified"),
            (dict(verified={**VERIFIED, "account_id": "short"}), "tenant_ids_invalid"),
            (dict(verified={**VERIFIED, "account_uuid": "nope"}), "tenant_ids_invalid"),
            (dict(source_revision_now="other"), "revision_changed"),
            (dict(source_revision_now=""), "revision_changed"),
            (dict(explicit_go=False), "explicit_go_missing"), (dict(explicit_go="yes"), "explicit_go_missing"),
        )
        for over, reason in cases:
            result = plan(**over)
            self.assertEqual((result.status, result.reason, result.values), ("blocked", reason, {}), over)

    def test_a_field_that_was_not_read_is_never_treated_as_empty(self):
        for missing in ("Surface_Account_ID__c", "Account_UUID__c"):
            data = row()
            del data[missing]
            self.assertEqual(plan(data).reason, "co_row_unreadable", missing)
        self.assertEqual(plan(row(Account_UUID__c=5)).reason, "co_row_unreadable")


class ArgvTests(unittest.TestCase):
    def test_a_fixed_argument_list_writes_only_the_planned_fields_on_one_co(self):
        argv = wb.update_argv(plan(), CO_ID, "surface-onboarding")
        self.assertEqual(argv[:8], ["sf", "data", "update", "record", "--sobject", "Customer_Onboarding__c", "--record-id", CO_ID])
        values = argv[argv.index("--values") + 1]
        self.assertEqual(sorted(values.split(" ")), sorted(["Surface_Account_ID__c=" + ROW_ID, "Account_UUID__c=" + UUID]))
        self.assertEqual(argv[-2:], ["--target-org", "surface-onboarding"])
        self.assertTrue(all(isinstance(part, str) for part in argv))

    def test_only_a_ready_plan_and_valid_ids_are_accepted(self):
        for bad in (plan(row(Surface_Account_ID__c=ROW_ID, Account_UUID__c=UUID)), plan(row(Account_UUID__c=OTHER_UUID)),
                    plan(explicit_go=False), None, "x"):
            with self.assertRaises(ValueError):
                wb.update_argv(bad, CO_ID, "surface-onboarding")
        for record_id in ("", "abc", "a0B5g00000AbCdE;GH", None):
            with self.assertRaises(ValueError):
                wb.update_argv(plan(), record_id, "surface-onboarding")
        for org in ("", "a b", "x;y", None):
            with self.assertRaises(ValueError):
                wb.update_argv(plan(), CO_ID, org)

    def test_the_module_never_runs_anything(self):
        import inspect
        text = inspect.getsource(wb)
        for forbidden in ("subprocess", "os.system", "urllib", "socket", "requests"):
            self.assertNotIn(forbidden, text)


class VerifyTests(unittest.TestCase):
    def test_after_the_write_both_fields_must_read_back_equal(self):
        self.assertEqual(wb.verify_after_write(row(Surface_Account_ID__c=ROW_ID, Account_UUID__c=UUID), VERIFIED), "verified")
        self.assertEqual(wb.verify_after_write(row(Surface_Account_ID__c=ROW_ID, Account_UUID__c=OTHER_UUID), VERIFIED), "mismatch")
        self.assertEqual(wb.verify_after_write(row(Surface_Account_ID__c=None, Account_UUID__c=UUID), VERIFIED), "mismatch")
        for bad in (None, {}, row(Account_UUID__c=5)):
            self.assertEqual(wb.verify_after_write(bad, VERIFIED), "unreadable")
        self.assertEqual(wb.verify_after_write(row(Surface_Account_ID__c=ROW_ID, Account_UUID__c=UUID), None), "unreadable")


if __name__ == "__main__":
    unittest.main()
