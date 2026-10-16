import unittest

from integration.onboarding import salesforce_uuid_writeback as w

UUID = "b" * 32
CO_ID = "a0B5g00000AbCdEFGH"
ROW = {"Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
       "Account_UUID__c": None}
ROUTE = "case_2_new_ce_only"


def plan(**over):
    args = dict(environment="production", route=ROUTE, co_row=dict(ROW), readback_uuid=UUID,
                readback_verified=True, source_revision_now="r1", source_revision_at_approval="r1", explicit_go=True)
    args.update(over)
    return w.plan(**args)


class PlanTests(unittest.TestCase):
    def test_ready_in_both_named_environments(self):
        for env in ("production", "development"):
            p = plan(environment=env)
            self.assertEqual((p.status, p.reason, p.uuid), ("ready_to_write", "ready", UUID))

    def test_environment_blocked(self):
        for env in (None, "", "prod", "Production", "staging", "dev", " production", 1):
            self.assertEqual(plan(environment=env).reason, "environment_not_named", env)
            self.assertEqual(plan(environment=env).status, "blocked")

    def test_route_blocked(self):
        for route in (None, "", "case_1_new_surface_only", "case_3_combined_baseline", "case_4_renewal", "x"):
            p = plan(route=route)
            self.assertEqual((p.status, p.reason), ("blocked", "route_not_ce_only_new"), route)

    def test_surface_only_and_renewal_rows_refused(self):
        surface = {**ROW, "Onboarding_Product__c": "Surface"}
        renewal = {**ROW, "Onboarding_Type__c": "Renewal of Existing Product"}
        both = {**ROW, "Onboarding_Product__c": "Surface & Credential Exposure"}
        for row in (surface, renewal, both):
            self.assertEqual(plan(co_row=row).reason, "route_not_ce_only_new")

    def test_not_verified(self):
        for v in (False, None, "yes", 1):
            self.assertEqual(plan(readback_verified=v).reason, "readback_not_verified")

    def test_uuid_invalid(self):
        for bad in (None, "", "b" * 31, "b" * 33, "g" * 32, "b" * 31 + "-", "bb\nbb" + "b" * 28, 123,
                    "b" * 16 + " " + "b" * 15, "b" * 32 + ";x"):
            p = plan(readback_uuid=bad)
            self.assertEqual((p.status, p.reason), ("blocked", "uuid_invalid"), bad)

    def test_uuid_normalised(self):
        p = plan(readback_uuid=" " + "B" * 32 + "\n")
        self.assertEqual((p.status, p.uuid), ("ready_to_write", UUID))

    def test_revision(self):
        for now, then in (("r2", "r1"), (None, "r1"), ("r1", None), ("", ""), (None, None)):
            self.assertEqual(plan(source_revision_now=now, source_revision_at_approval=then).reason, "revision_changed")

    def test_explicit_go(self):
        for go in (False, None, "true", 1):
            self.assertEqual(plan(explicit_go=go).reason, "explicit_go_missing")

    def test_already_present(self):
        p = plan(co_row={**ROW, "Account_UUID__c": " " + "B" * 32 + " "})
        self.assertEqual((p.status, p.reason), ("already_present", "equal"))

    def test_conflict(self):
        p = plan(co_row={**ROW, "Account_UUID__c": "c" * 32})
        self.assertEqual((p.status, p.reason), ("conflict", "different_value"))
        # A malformed existing value is also never overwritten.
        self.assertEqual(plan(co_row={**ROW, "Account_UUID__c": "legacy"}).status, "conflict")

    def test_empty_values_are_ready(self):
        for empty in (None, "", "  "):
            self.assertEqual(plan(co_row={**ROW, "Account_UUID__c": empty}).status, "ready_to_write")

    def test_absent_uuid_field_means_row_unreadable(self):
        row = {k: v for k, v in ROW.items() if k != "Account_UUID__c"}
        p = plan(co_row=row)
        self.assertEqual((p.status, p.reason), ("blocked", "co_row_unreadable"))
        self.assertEqual(plan(co_row={**ROW, "Account_UUID__c": None}).status, "ready_to_write")
        self.assertEqual(plan(co_row={**ROW, "Account_UUID__c": ""}).status, "ready_to_write")

    def test_unreadable_row(self):
        self.assertEqual(plan(co_row=None).reason, "co_row_unreadable")
        self.assertEqual(plan(co_row={**ROW, "Account_UUID__c": 5}).reason, "co_row_unreadable")

    def test_result_immutable(self):
        p = plan()
        with self.assertRaises(Exception):
            p.status = "conflict"

    def test_plan_does_not_mutate_row(self):
        row = dict(ROW)
        plan(co_row=row)
        self.assertEqual(row, ROW)


def ready_plan(uuid=UUID):
    return plan(readback_uuid=uuid)


class ArgvTests(unittest.TestCase):
    def test_exact_shape(self):
        self.assertEqual(
            w.update_argv(ready_plan(), CO_ID, "my-alias"),
            ["sf", "data", "update", "record", "--sobject", "Customer_Onboarding__c", "--record-id", CO_ID,
             "--values", "Account_UUID__c=" + UUID, "--target-org", "my-alias"])

    def test_15_char_id_and_normalised_uuid(self):
        argv = w.update_argv(ready_plan("B" * 32), "a0B5g00000AbCdE", "alias")
        self.assertEqual(argv[7], "a0B5g00000AbCdE")
        self.assertEqual(argv[9], "Account_UUID__c=" + UUID)

    def test_only_one_field(self):
        argv = w.update_argv(ready_plan(), CO_ID, "alias")
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(a, str) for a in argv))
        self.assertEqual(argv.count("--values"), 1)
        self.assertEqual(sum("__c" in a for a in argv), 2)  # sobject + the one field
        self.assertNotIn("Onboarding_Stage__c", " ".join(argv))
        self.assertEqual(w.WRITE_FIELD, "Account_UUID__c")

    def test_only_a_ready_to_write_plan_is_accepted(self):
        blocked = plan(explicit_go=False)
        present = plan(co_row={**ROW, "Account_UUID__c": UUID})
        conflict = plan(co_row={**ROW, "Account_UUID__c": "c" * 32})
        self.assertEqual((blocked.status, present.status, conflict.status),
                         ("blocked", "already_present", "conflict"))
        for bad in (blocked, present, conflict, w.WritebackPlan("blocked", "x", UUID), UUID, None, "ready_to_write",
                    {"status": "ready_to_write", "uuid": UUID}):
            with self.assertRaises(ValueError, msg=bad):
                w.update_argv(bad, CO_ID, "alias")

    def test_ready_plan_with_bad_uuid_is_refused(self):
        for bad in (None, "x", UUID + " Name=x", UUID + ";rm", '"' + UUID):
            with self.assertRaises(ValueError, msg=bad):
                w.update_argv(w.WritebackPlan("ready_to_write", "ready", bad), CO_ID, "alias")

    def test_plan_with_other_field_is_refused(self):
        other = w.WritebackPlan("ready_to_write", "ready", UUID, "Onboarding_Stage__c")
        with self.assertRaises(ValueError):
            w.update_argv(other, CO_ID, "alias")

    def test_bad_ids(self):
        for bad in (None, "", "a0B5g0000", "a0B5g00000AbCdEFGHI", "a0B5g00000AbCdE;", "a0B5g00000AbCd E",
                    "a0B5g00000AbCdEFG'", "a0B5g00000AbCdEF\n", " " + CO_ID, 5):
            with self.assertRaises(ValueError, msg=bad):
                w.update_argv(ready_plan(), bad, "alias")

    def test_bad_target_org(self):
        for bad in (None, "", "-x", "a b", "a;b", "a'b", '"a"', "a\nb", 3):
            with self.assertRaises(ValueError, msg=bad):
                w.update_argv(ready_plan(), CO_ID, bad)


class VerifyTests(unittest.TestCase):
    def test_verified(self):
        self.assertEqual(w.verify_after_write({"Account_UUID__c": " " + "B" * 32}, UUID), "verified")

    def test_mismatch(self):
        self.assertEqual(w.verify_after_write({"Account_UUID__c": "c" * 32}, UUID), "mismatch")
        self.assertEqual(w.verify_after_write({"Account_UUID__c": ""}, UUID), "mismatch")
        self.assertEqual(w.verify_after_write({"Account_UUID__c": None}, UUID), "mismatch")

    def test_unreadable(self):
        for row in (None, {}, [], "x", {"Name": "CO-1"}, {"Account_UUID__c": 5}):
            self.assertEqual(w.verify_after_write(row, UUID), "unreadable", row)
        self.assertEqual(w.verify_after_write({"Account_UUID__c": UUID}, "bad"), "unreadable")


if __name__ == "__main__":
    unittest.main()
