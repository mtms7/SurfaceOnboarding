"""CO-0800 (Surface & Credential Exposure, New Product Onboarding = Case 3): route selection for the production DRY RUN only.

Owner decisions 2026-10-20: the production dry run may be extended to ``case_3_combined_baseline``; the production CREATE
stays CE-only (a Case 3 live create is refused ``route_not_ce_only_new`` before any fill, click or approval consume); every
other route (Surface-only, renewals, anything unknown) stays refused in both modes. Hermetic: fake target, fake approval check,
temporary folders; no Salesforce, Redash, network or browser.
"""

import tempfile
import unittest
from pathlib import Path

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create as pc
from integration.onboarding import production_create_ui as ui
from integration.tests import test_production_create as base
from integration.tests.test_production_create import NOW, REF, make_inputs, target_ok

CASE3 = "case_3_combined_baseline"
CE = "case_2_new_ce_only"
ALTERNATES = ("case_1_new_surface_only", "case_1_surface_only", "case_4_renew_surface_new_ce", "case_5_renew_ce_new_surface",
              "case_6_renew_both", "renewal_run", "", "case_3", "CASE_3_COMBINED_BASELINE")


def gate(inputs, **kwargs):
    return pc.evaluate_gate(inputs, production_target_fn=target_ok, approval_check_fn=base.approval_ok, **kwargs)


class GateRouteTests(unittest.TestCase):
    def test_the_dry_run_accepts_case_3_and_ce_only(self):
        for route in (CE, CASE3):
            with self.subTest(route=route):
                self.assertIsNone(gate(make_inputs(route=route), dry_run=True))

    def test_a_live_create_stays_ce_only(self):
        self.assertIsNone(gate(make_inputs(route=CE)))
        self.assertIsNone(gate(make_inputs(route=CE), dry_run=False))
        self.assertEqual(gate(make_inputs(route=CASE3)), "route_not_ce_only_new")
        self.assertEqual(gate(make_inputs(route=CASE3), dry_run=False), "route_not_ce_only_new")

    def test_every_alternate_route_is_refused_in_both_modes(self):
        for route in ALTERNATES:
            for dry in (True, False):
                with self.subTest(route=route, dry_run=dry):
                    self.assertEqual(gate(make_inputs(route=route), dry_run=dry), "route_not_ce_only_new")

    def test_only_the_exact_boolean_true_opens_the_dry_run_route(self):
        for flag in (1, "true", "yes", object(), None, [True]):
            with self.subTest(flag=flag):
                self.assertEqual(gate(make_inputs(route=CASE3), dry_run=flag), "route_not_ce_only_new")

    def test_the_other_gates_still_apply_to_a_case_3_dry_run(self):
        cases = (("duplicate_in_production", {"duplicate_found": True}),
                 ("salesforce_email_domain_not_single", {"sf_email_domains": ("a.com", "b.com")}),
                 ("clone_stale", {"clone_collected_at": NOW.replace(year=2025)}),
                 ("salesforce_prior_uuid_present", {"prior_account_uuid": base.UUID}),
                 ("approval_revision_drift", {"source_revision_now": "r30"}),
                 ("approval_ref_mismatch", {"environ": {base.REF_ENV: "PROD-C3-0001-20261016"}}))
        for reason, over in cases:
            with self.subTest(reason=reason):
                self.assertEqual(gate(make_inputs(route=CASE3, **over), dry_run=True), reason)

    def test_the_approval_check_is_asked_about_the_case_3_route(self):
        seen = []

        def check(path, ref, co, route, revision, now):
            seen.append(route)
            return None

        pc.evaluate_gate(make_inputs(route=CASE3), production_target_fn=target_ok, approval_check_fn=check, dry_run=True)
        self.assertEqual(seen, [CASE3])

    def test_the_create_route_constant_is_unchanged(self):
        self.assertEqual(approval.ALLOWED_ROUTE, CE)
        self.assertEqual(pc.DRY_RUN_ROUTES, (CE, CASE3))


class RunRouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.approval_path = self.state / "approvals.json"

    def issue(self, route):
        approval.issue(self.approval_path, REF, "CO-0797", route, "r29", "milton", frozenset({"milton"}),
                       NOW.replace(hour=11), tenant_name=("Test Tenant" if route == "case_3_combined_baseline" else None))

    def harness(self):
        return base.Harness(self.state, self.approval_path)

    def inputs(self, route):
        return make_inputs(route=route, approval_path=self.approval_path)

    def run_it(self, route, dry_run, harness):
        kwargs = dict(owner_go=True, _production_target_fn=target_ok, clock=lambda: NOW)
        if not dry_run:
            kwargs.update(prior_records=[base.DRY_RECORD], live_duplicate_check=harness.dup_check,
                          live_source_check=harness.source_check, live_form_recheck=harness.form_recheck)
        return pc.run(self.inputs(route), dry_run, harness.fill_form, harness.click_confirm, harness.read_back,
                      harness.write_record, **kwargs)

    def test_a_case_3_dry_run_fills_and_records_but_never_clicks_or_consumes(self):
        self.issue(CASE3)
        h = self.harness()
        result = self.run_it(CASE3, True, h)
        self.assertEqual((result.outcome, result.reason), ("dry_run_passed", ""))
        self.assertEqual(h.counts, {"fill": 1, "click": 0, "readback": 0, "write": 1})
        self.assertEqual(ui.load_co_records(self.state, "CO-0797")[-1]["route"], CASE3)
        self.assertIsNone(approval.check(self.approval_path, REF, "CO-0797", CASE3, "r29", NOW))  # still unconsumed

    def test_a_case_3_live_create_is_refused_before_any_fill_click_or_consume(self):
        self.issue(CASE3)
        h = self.harness()
        result = self.run_it(CASE3, False, h)
        self.assertEqual((result.outcome, result.reason), ("refused", "route_not_ce_only_new"))
        self.assertEqual(h.counts["fill"] + h.counts["click"] + h.counts["readback"], 0)
        self.assertIsNone(approval.check(self.approval_path, REF, "CO-0797", CASE3, "r29", NOW))

    def test_a_ce_only_approval_cannot_run_a_case_3_dry_run(self):
        self.issue(CE)
        h = self.harness()
        result = self.run_it(CASE3, True, h)
        self.assertEqual(result.outcome, "refused")
        self.assertNotEqual(result.reason, "")
        self.assertEqual(h.counts["fill"], 0)

    def test_an_alternate_route_dry_run_is_refused_without_a_fill(self):
        self.issue(CE)
        for route in ("case_1_new_surface_only", "case_4_renew_surface_new_ce"):
            with self.subTest(route=route):
                h = self.harness()
                result = self.run_it(route, True, h)
                self.assertEqual((result.outcome, h.counts["fill"]), ("refused", 0))

    def test_ce_only_runs_are_unchanged(self):
        self.issue(CE)
        h = self.harness()
        self.assertEqual(self.run_it(CE, True, h).outcome, "dry_run_passed")


if __name__ == "__main__":
    unittest.main()
