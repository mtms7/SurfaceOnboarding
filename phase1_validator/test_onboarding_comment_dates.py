import unittest

from phase1_validator.onboarding_comment_dates import extract_dealhub_dates


class OnboardingCommentDateTests(unittest.TestCase):
    def test_one_valid_range_is_ready_for_cse_review_without_returning_comment(self):
        comment = "DealHub validation complete. 2026-10-24 - 2029-10-23. Internal note."

        result = extract_dealhub_dates(comment)

        self.assertEqual(result["result"], "dealhub_dates_extracted")
        self.assertEqual(result["dealhub_start_date"], "2026-10-24")
        self.assertEqual(result["dealhub_end_date"], "2029-10-23")
        self.assertEqual(result["queue_status"], "ready_for_cse_review")
        self.assertTrue(result["ready_for_cse_review"])
        self.assertFalse(result["proceed"])
        self.assertFalse(result["eligible_for_automation"])
        self.assertEqual(result["action_required"], "cse_manual_validation")
        self.assertNotIn(comment, str(result))
        self.assertNotIn("Internal note", str(result))

    def test_valid_date_range_allows_a_non_date_test_suffix(self):
        result = extract_dealhub_dates("2026-07-01 - 2029-06-30 TEST")

        self.assertTrue(result["ready_for_cse_review"])
        self.assertFalse(result["eligible_for_automation"])
        self.assertNotIn("TEST", str(result))

    def test_missing_range_is_blocked(self):
        result = extract_dealhub_dates("DealHub validation has no usable term.")

        self.assertEqual(result["queue_status"], "blocked")
        self.assertEqual(result["error_category"], "dealhub_date_range_missing")
        self.assertFalse(result["ready_for_cse_review"])
        self.assertFalse(result["proceed"])

    def test_multiple_ranges_are_blocked_as_ambiguous(self):
        result = extract_dealhub_dates(
            "2026-10-24 - 2029-10-23; historical 2023-10-24 - 2026-10-23"
        )

        self.assertEqual(result["queue_status"], "blocked")
        self.assertEqual(result["error_category"], "dealhub_date_range_ambiguous")

    def test_invalid_or_reversed_range_is_blocked(self):
        invalid = extract_dealhub_dates("2026-02-30 - 2029-02-28")
        reversed_range = extract_dealhub_dates("2029-10-23 - 2026-10-24")

        self.assertEqual(invalid["error_category"], "dealhub_date_range_invalid")
        self.assertEqual(reversed_range["error_category"], "dealhub_date_range_invalid")
        self.assertFalse(invalid["proceed"])
        self.assertFalse(reversed_range["proceed"])

    def test_missing_or_wrong_type_comment_is_blocked(self):
        missing = extract_dealhub_dates(None)
        wrong_type = extract_dealhub_dates(["2026-10-24 - 2029-10-23"])

        self.assertEqual(missing["error_category"], "onboarding_comments_missing")
        self.assertEqual(wrong_type["error_category"], "onboarding_comments_invalid_type")


if __name__ == "__main__":
    unittest.main()
