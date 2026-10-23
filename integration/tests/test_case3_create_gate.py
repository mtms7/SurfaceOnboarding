"""Path B step (a): the production CREATE gate for Case 3 (Surface & Credential Exposure), CO-0802 first.

Owner go 2026-10-22. The gate opens Case 3 for a live create only when the caller passes the exact boolean
``case3_create=True``; the default stays CE-only so the existing live path is unchanged. The production lock
constants stay False: a gate that accepts the route never opens the lock. Hermetic: fake target and approval check.
"""

import unittest

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create as pc
from integration.onboarding import session_readiness
from integration.tests import test_production_create as base
from integration.tests.test_production_create import NOW, make_inputs, target_ok
from integration.tests.locked_build import locked_create_build  # noqa: E402

CASE3 = "case_3_combined_baseline"
CE = "case_2_new_ce_only"
OTHERS = ("case_1_new_surface_only", "case_1_surface_only", "case_4_renew_surface_new_ce", "case_5_renew_ce_new_surface",
          "case_6_renew_both", "renewal_run", "", "case_3", "CASE_3_COMBINED_BASELINE")


def gate(inputs, **kwargs):
    # The tenant-name binding has its own tests (test_case3_approval_tenant_name.py); here the name check always passes.
    return pc.evaluate_gate(inputs, production_target_fn=target_ok, approval_check_fn=base.approval_ok,
                            approval_name_fn=lambda *args: None, **kwargs)


class Case3CreateGateTests(unittest.TestCase):
    def test_a_case_3_live_create_opens_only_with_the_explicit_flag(self):
        self.assertIsNone(gate(make_inputs(route=CASE3), case3_create=True))
        self.assertEqual(gate(make_inputs(route=CASE3)), "route_not_ce_only_new")
        self.assertEqual(gate(make_inputs(route=CASE3), case3_create=False), "route_not_ce_only_new")

    def test_only_the_exact_boolean_true_counts(self):
        for flag in (1, "true", "yes", object(), None, [True]):
            with self.subTest(flag=flag):
                self.assertEqual(gate(make_inputs(route=CASE3), case3_create=flag), "route_not_ce_only_new")

    def test_the_flag_never_widens_any_other_route(self):
        for route in OTHERS:
            with self.subTest(route=route):
                self.assertEqual(gate(make_inputs(route=route), case3_create=True), "route_not_ce_only_new")

    def test_ce_only_is_unchanged_with_or_without_the_flag(self):
        self.assertIsNone(gate(make_inputs(route=CE)))
        self.assertIsNone(gate(make_inputs(route=CE), case3_create=True))

    def test_every_other_gate_still_applies_to_a_case_3_create(self):
        cases = (("duplicate_in_production", {"duplicate_found": True}),
                 ("salesforce_email_domain_not_single", {"sf_email_domains": ("a.com", "b.com")}),
                 ("salesforce_source_mismatch", {"sf_source_consistent": False}),
                 ("clone_stale", {"clone_collected_at": NOW.replace(year=2025)}),
                 ("salesforce_prior_uuid_present", {"prior_account_uuid": base.UUID}),
                 ("approval_revision_drift", {"source_revision_now": "r30"}),
                 ("approval_ref_mismatch", {"environ": {base.REF_ENV: "PROD-C3-0001-20261016"}}),
                 ("sessions_not_ready", {"sessions_ready": False}),
                 ("schema_unexpected", {"schema_ok": False}))
        for reason, over in cases:
            with self.subTest(reason=reason):
                self.assertEqual(gate(make_inputs(route=CASE3, **over), case3_create=True), reason)

    def test_a_case_3_approval_must_carry_the_case_3_scope(self):
        self.assertEqual(approval.scope_for(CASE3), "case3_single_co")
        self.assertNotEqual(approval.scope_for(CASE3), approval.scope_for(CE))

        def wrong_scope(path, ref, co, route, revision, now):
            return "approval_route_mismatch"
        self.assertEqual(pc.evaluate_gate(make_inputs(route=CASE3), production_target_fn=target_ok,
                                          approval_check_fn=wrong_scope, case3_create=True), "approval_route_mismatch")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_the_production_locks_stay_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # dry run only unlocked for the CO-0802 dry run; the create stays locked


if __name__ == "__main__":
    unittest.main()
