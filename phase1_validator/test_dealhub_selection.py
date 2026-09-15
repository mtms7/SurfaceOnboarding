import unittest

from phase1_validator.dealhub_selection import (
    AmbiguousSubscriptionSelection,
    DealHubSelectionError,
    classify_product_groups,
    select_surface_core_addons,
    select_unambiguous_best,
)


class DealHubSelectionTests(unittest.TestCase):
    def test_empty_group_returns_none(self):
        self.assertIsNone(select_unambiguous_best([]))

    def test_single_candidate_is_selected(self):
        row = {"status": "Active", "marker": "selected"}
        self.assertIs(select_unambiguous_best([row]), row)

    def test_unique_best_rank_wins_over_lower_rank(self):
        active = {"status": "ACTIVE", "marker": "selected"}
        pending = {"status": "pending", "marker": "not-selected"}
        self.assertIs(select_unambiguous_best([pending, active]), active)

    def test_equal_best_rank_fails_closed(self):
        rows = [
            {"status": "Active", "restricted": "must-not-leak-a"},
            {"status": "active", "restricted": "must-not-leak-b"},
        ]
        with self.assertRaises(AmbiguousSubscriptionSelection) as raised:
            select_unambiguous_best(rows)
        self.assertEqual(str(raised.exception), "ambiguous_subscription_selection")
        self.assertNotIn("must-not-leak", str(raised.exception))

    def test_lower_rank_tie_does_not_override_unique_best(self):
        active = {"status": "active", "marker": "selected"}
        rows = [active, {"status": "trial"}, {"status": "TRIAL"}]
        self.assertIs(select_unambiguous_best(rows), active)

    def test_equal_unknown_rank_fails_closed(self):
        with self.assertRaises(AmbiguousSubscriptionSelection):
            select_unambiguous_best([{"status": None}, {"status": "other"}])

    def test_surface_core_and_embedded_sva_form_one_bundle(self):
        rows = [
            {
                "status": "Active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Current Product - Remaining Usage Value",
            },
            {
                "status": "Active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Surface Go",
                "product_family": "Pentera Surface Software",
            },
            {
                "status": "Active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Core Plus Commercial",
                "product_family": "Pentera Core Plus Software",
                "sva_type": "SVA Essentials",
            },
            {
                "status": "Expired",
                "opportunity_id": "historical-opportunity",
                "product_full_name": "Pentera Core Software",
            },
        ]
        selected = select_surface_core_addons(rows)
        self.assertEqual(selected["opportunity_id"], "opportunity-a")
        self.assertIn("surface", classify_product_groups(selected["surface"]))
        self.assertIn("core", classify_product_groups(selected["core"]))
        self.assertEqual(selected["addons"], [selected["core"]])

    def test_equal_rank_across_surface_and_core_is_not_ambiguous(self):
        rows = [
            {
                "status": "active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Surface",
            },
            {
                "status": "active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Core",
            },
        ]
        selected = select_surface_core_addons(rows)
        self.assertIsNotNone(selected["surface"])
        self.assertIsNotNone(selected["core"])

    def test_duplicate_surface_rows_fail_closed(self):
        rows = [
            {
                "status": "active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Surface Go",
            },
            {
                "status": "active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Surface Advanced",
            },
        ]
        with self.assertRaises(AmbiguousSubscriptionSelection):
            select_surface_core_addons(rows)

    def test_unknown_active_product_fails_closed(self):
        with self.assertRaisesRegex(
            DealHubSelectionError, "unknown_subscription_product"
        ):
            select_surface_core_addons(
                [
                    {
                        "status": "active",
                        "opportunity_id": "opportunity-a",
                        "product_full_name": "Unmapped Product",
                    }
                ]
            )

    def test_relevant_rows_on_different_opportunities_fail_closed(self):
        rows = [
            {
                "status": "active",
                "opportunity_id": "opportunity-a",
                "product_full_name": "Pentera Surface",
            },
            {
                "status": "active",
                "opportunity_id": "opportunity-b",
                "product_full_name": "Pentera Core",
            },
        ]
        with self.assertRaisesRegex(
            DealHubSelectionError, "ambiguous_subscription_opportunity"
        ):
            select_surface_core_addons(rows)


if __name__ == "__main__":
    unittest.main()
