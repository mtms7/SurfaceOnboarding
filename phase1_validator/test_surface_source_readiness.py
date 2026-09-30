from datetime import date
import unittest

from phase1_validator.surface_source_readiness import (
    PRIME_DEFAULT_SUBDOMAINS,
    SurfaceProduct,
    classify_surface_product,
    evaluate_new_surface_source,
    surface_scanning_interval,
)


class SurfaceProductClassificationTests(unittest.TestCase):
    def test_newer_baselines(self):
        self.assertEqual(classify_surface_product("Pentera Surface Go - 500 Subdomains"),
                         SurfaceProduct("baseline", "go", 500))
        self.assertEqual(classify_surface_product("Pentera Surface Prime - 1000 Subdomains"),
                         SurfaceProduct("baseline", "prime", 1000))
        self.assertEqual(classify_surface_product("pentera surface prime - 2000 sub-domains"),
                         SurfaceProduct("baseline", "prime", 2000))

    def test_prime_without_a_number_defaults_to_1000(self):
        self.assertEqual(PRIME_DEFAULT_SUBDOMAINS, 1000)
        self.assertEqual(classify_surface_product("Pentera Surface Prime"),
                         SurfaceProduct("baseline", "prime", 1000))

    def test_go_without_a_number_is_unrecognized(self):
        self.assertEqual(classify_surface_product("Pentera Surface Go").kind, "unrecognized")

    def test_older_baselines(self):
        cases = {
            "Pentera Surface Software - Essentials - 150 Sub-Domains & 1 Domains": ("essentials", 150, 1),
            "Pentera Surface Software - Essential - 150 Sub-Domains & 1 Domains": ("essentials", 150, 1),
            "Pentera Surface Software Professional - 150 Sub-Domains & 3 Domains": ("professional", 150, 3),
            "Pentera Surface Software Enterprise - Up to 650 Sub-Domains & 5 Domains": ("enterprise", 650, 5),
            "Pentera Surface Software - Enterprise - 650 Sub-Domains & 5 Domains": ("enterprise", 650, 5),
            "Pentera Surface Software Essentials - 150 Sub-Domains & 1 Domain": ("essentials", 150, 1),
        }
        for name, (tier, subdomains, domains) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_surface_product(name),
                                 SurfaceProduct("baseline", tier, subdomains, domains))

    def test_addons(self):
        self.assertEqual(
            classify_surface_product("Pentera Surface Software Enterprise - Sub-Domain Add-on - 1 Bulks of 400 Sub-Domains"),
            SurfaceProduct("subdomain_addon", None, 400))
        self.assertEqual(
            classify_surface_product("Pentera Surface Software Enterprise - Sub-Domain Add-on - 2 Bulks of 400 Sub-Domains"),
            SurfaceProduct("subdomain_addon", None, 800))
        self.assertEqual(classify_surface_product("Pentera Surface Add-on - Additional 250 Subdomains"),
                         SurfaceProduct("subdomain_addon", None, 250))
        domain_addon = classify_surface_product("Pentera Surface Software Enterprise - Domain Add-on - 1 bulks of 10 Domains")
        self.assertEqual(domain_addon, SurfaceProduct("domain_addon", None, 0, 10))

    def test_tierless_baseline_has_no_interval(self):
        product = classify_surface_product("Pentera Surface - 1000 Subdomains")
        self.assertEqual(product, SurfaceProduct("baseline", None, 1000))
        self.assertIsNone(surface_scanning_interval(product.tier))

    def test_non_surface_and_unrecognized_rows(self):
        self.assertIsNone(classify_surface_product("Pentera Core Plus Commercial - 500 End Points"))
        self.assertIsNone(classify_surface_product("Security Validation Advisor - Incl. Surface"))
        self.assertIsNone(classify_surface_product(None))
        self.assertEqual(classify_surface_product("Pentera Surface Ultra - 5 Subdomains").kind, "unrecognized")

    def test_tier_intervals(self):
        self.assertEqual(surface_scanning_interval("prime"), "Weekly")
        self.assertEqual(surface_scanning_interval("go"), "Monthly")
        self.assertEqual(surface_scanning_interval("enterprise"), "Weekly")
        self.assertEqual(surface_scanning_interval("essentials"), "Monthly")
        self.assertEqual(surface_scanning_interval("professional"), "Monthly")
        self.assertIsNone(surface_scanning_interval("ultra"))


class SurfaceSourceReadinessTests(unittest.TestCase):
    def test_prime_baseline_is_commercially_ready(self):
        result = evaluate_new_surface_source(
            onboarding_comments=None, main_domain="example.test",
            subscriptions=[{"product_full_name": "Pentera Surface Prime - 1000 Subdomains", "status": "Active",
                            "start_date": "2026-09-01", "end_date": "2027-08-31"}],
            today=date(2026, 9, 15))
        self.assertTrue(result["commercial_ready"])
        self.assertEqual(result["total_subdomains"], 1000)

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
