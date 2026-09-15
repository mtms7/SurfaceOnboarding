from datetime import date
import unittest

from phase1_validator.surface_source_readiness import evaluate_new_surface_source


class SurfaceSourceReadinessTests(unittest.TestCase):
    def test_pending_surface_starting_this_month_is_commercially_ready(self):
        result = evaluate_new_surface_source(
            onboarding_comments=None,
            main_domain="bonpreu.cat",
            subscriptions=[{
                "product_full_name": "Pentera Surface Go - 500 Subdomains",
                "status": "Pending",
                "start_date": "2026-09-30",
                "end_date": "2028-09-29",
            }],
            today=date(2026, 9, 15),
        )
        self.assertTrue(result["commercial_ready"])
        self.assertFalse(result["manual_review_required"])
        self.assertEqual(result["total_subdomains"], 500)

    def test_named_subdomain_addon_is_added_to_baseline(self):
        result = evaluate_new_surface_source(
            onboarding_comments="",
            main_domain="example.test",
            subscriptions=[
                {"product_full_name": "Pentera Surface Go - 500 Subdomains", "status": "Active", "start_date": "2026-09-01", "end_date": "2027-08-31"},
                {"product_full_name": "Pentera Surface Add-on - Additional 250 Subdomains", "status": "Pending", "start_date": "2026-09-01", "end_date": "2027-08-31"},
            ],
            today=date(2026, 9, 15),
        )
        self.assertTrue(result["commercial_ready"])
        self.assertEqual(result["addon_subdomains"], 250)
        self.assertEqual(result["total_subdomains"], 750)

    def test_future_month_surface_subscription_requires_review(self):
        result = evaluate_new_surface_source(
            onboarding_comments=None,
            main_domain="example.test",
            subscriptions=[{"product_full_name": "Pentera Surface Go - 500 Subdomains", "status": "Pending", "start_date": "2026-10-01", "end_date": "2027-09-30"}],
            today=date(2026, 9, 15),
        )
        self.assertFalse(result["commercial_ready"])
        self.assertEqual(result["reason"], "surface_subscription_not_current")

    def test_existing_valid_comment_requires_manual_review_without_losing_readiness(self):
        result = evaluate_new_surface_source(
            onboarding_comments="2026-09-01 - 2027-08-31",
            main_domain="example.test",
            subscriptions=[{"product_full_name": "Pentera Surface Go - 500 Subdomains", "status": "Pending", "start_date": "2026-09-01", "end_date": "2027-08-31"}],
            today=date(2026, 9, 15),
        )
        self.assertTrue(result["commercial_ready"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["reason"], "onboarding_comments_present")

    def test_blank_main_domain_and_multiple_baselines_fail_closed(self):
        missing_domain = evaluate_new_surface_source(onboarding_comments=None, main_domain="", subscriptions=[], today=date(2026, 9, 15))
        ambiguous = evaluate_new_surface_source(
            onboarding_comments=None,
            main_domain="example.test",
            subscriptions=[
                {"product_full_name": "Pentera Surface Go - 500 Subdomains", "status": "Active", "start_date": "2026-09-01", "end_date": "2027-08-31"},
                {"product_full_name": "Pentera Surface - 1000 Subdomains", "status": "Active", "start_date": "2026-09-01", "end_date": "2027-08-31"},
            ],
            today=date(2026, 9, 15),
        )
        self.assertEqual(missing_domain["reason"], "main_domain_missing")
        self.assertEqual(ambiguous["reason"], "surface_baseline_missing_or_ambiguous")


if __name__ == "__main__":
    unittest.main()
