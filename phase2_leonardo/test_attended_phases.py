import unittest

import phase2_leonardo.attended_phases as attended_phases
from phase2_leonardo.attended_phases import (
    AttendedPhaseEvidence,
    evaluate_attended_phases,
)


def future_ready_evidence(**overrides):
    """Synthetic gate flags only; contains no customer-derived values."""

    values = {
        "preflight_proceed": True,
        "leonardo_request_allowed": True,
        "unapproved_field_group_count": 0,
        "source_reread_matches": True,
        "primary_user_binding_verified": True,
        "separated_approval_verified": True,
        "duplicate_authority_verified": True,
        "duplicate_count": 0,
        "human_confirmation_attested": True,
        "readback_authority_verified": True,
        "readback_verified": True,
        "writeback_approval_verified": True,
    }
    values.update(overrides)
    return AttendedPhaseEvidence(**values)


class AttendedPhaseTests(unittest.TestCase):
    def phase(self, plan, name):
        return next(phase for phase in plan.phases if phase.name == name)

    def test_current_v5_preflight_is_fail_closed(self):
        plan = evaluate_attended_phases(AttendedPhaseEvidence())

        self.assertFalse(plan.proceed)
        self.assertFalse(plan.external_action_callable)
        self.assertIn("mapping:v5_execution_unapproved", plan.blockers)
        self.assertIn("preflight:proceed_false", plan.blockers)
        self.assertIn("preflight:leonardo_request_not_allowed", plan.blockers)
        self.assertIn("mapping:unapproved_field_groups_present", plan.blockers)
        self.assertNotIn("preflight:v5_invariant_violation", plan.blockers)
        self.assertEqual(self.phase(plan, "mapping_source_reread").status, "blocked")
        self.assertEqual(self.phase(plan, "fill_and_pause").status, "blocked")

    def test_primary_user_binding_and_separated_approval_are_required(self):
        missing_user = evaluate_attended_phases(
            future_ready_evidence(primary_user_binding_verified=False)
        )
        self.assertIn("primary_user:binding_not_verified", missing_user.blockers)
        self.assertEqual(self.phase(missing_user, "mapping_source_reread").status, "blocked")

        missing_approval = evaluate_attended_phases(
            future_ready_evidence(separated_approval_verified=False)
        )
        self.assertIn(
            "approval:separated_approval_not_verified", missing_approval.blockers
        )
        self.assertEqual(self.phase(missing_approval, "fill_and_pause").status, "blocked")

    def test_duplicate_and_readback_authorities_are_required(self):
        no_duplicate_authority = evaluate_attended_phases(
            future_ready_evidence(duplicate_authority_verified=False)
        )
        self.assertIn(
            "duplicate_preflight:authority_not_verified",
            no_duplicate_authority.blockers,
        )
        self.assertEqual(
            self.phase(no_duplicate_authority, "duplicate_preflight").status,
            "blocked",
        )

        no_readback_authority = evaluate_attended_phases(
            future_ready_evidence(readback_authority_verified=False)
        )
        self.assertIn("readback:authority_not_verified", no_readback_authority.blockers)
        self.assertEqual(self.phase(no_readback_authority, "readback").status, "blocked")

    def test_duplicate_requires_exactly_zero_and_readback_exact_match(self):
        duplicate = evaluate_attended_phases(future_ready_evidence(duplicate_count=1))
        self.assertIn("duplicate_preflight:zero_matches_required", duplicate.blockers)

        uncertain_readback = evaluate_attended_phases(
            future_ready_evidence(readback_verified=False)
        )
        self.assertIn("readback:exact_match_not_verified", uncertain_readback.blockers)
        self.assertEqual(self.phase(uncertain_readback, "writeback").status, "blocked")

    def test_forged_v5_state_cannot_make_any_external_phase_ready(self):
        plan = evaluate_attended_phases(future_ready_evidence())

        self.assertFalse(plan.proceed)
        self.assertFalse(plan.external_action_callable)
        self.assertIn("mapping:v5_execution_unapproved", plan.blockers)
        self.assertIn("preflight:v5_invariant_violation", plan.blockers)
        self.assertEqual(self.phase(plan, "mapping_source_reread").status, "blocked")
        self.assertEqual(self.phase(plan, "fill_and_pause").status, "blocked")
        self.assertEqual(self.phase(plan, "human_confirm").status, "external_human_only")
        self.assertEqual(self.phase(plan, "readback").status, "blocked")
        self.assertEqual(self.phase(plan, "writeback").status, "blocked")
        self.assertTrue(all(not phase.local_callable for phase in plan.phases))
        self.assertFalse(callable(getattr(attended_phases, "confirm", None)))
        self.assertFalse(callable(getattr(attended_phases, "create", None)))

    def test_truthy_non_booleans_and_invalid_counts_fail_closed(self):
        evidence = future_ready_evidence(
            preflight_proceed=1,
            unapproved_field_group_count=-1,
            duplicate_count=True,
        )
        plan = evaluate_attended_phases(evidence)

        self.assertIn("preflight:proceed_false", plan.blockers)
        self.assertIn("mapping:unapproved_group_count_invalid", plan.blockers)
        self.assertIn("duplicate_preflight:zero_matches_required", plan.blockers)

    def test_invalid_evidence_type_is_fail_closed(self):
        plan = evaluate_attended_phases({})

        self.assertFalse(plan.proceed)
        self.assertEqual(plan.blockers, ("evidence:invalid_type",))
        self.assertTrue(all(not phase.local_callable for phase in plan.phases))


if __name__ == "__main__":
    unittest.main()
