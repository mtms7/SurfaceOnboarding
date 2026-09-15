from __future__ import annotations

import unittest

from phase2_leonardo.case4_comment_validation import validate_case4_onboarding_comments


class Case4CommentValidationTests(unittest.TestCase):
    def test_source_unavailable_requires_manual_review(self):
        result = validate_case4_onboarding_comments(None, source_read_available=False)
        self.assertEqual(result["decision"], "manual_review_required")
        self.assertEqual(result["error_category"], "onboarding_comments_source_unavailable")
        self.assertFalse(result["proceed"])

    def test_non_date_comment_requires_manual_review_without_echoing_content(self):
        comment = "commercial notes need review"
        result = validate_case4_onboarding_comments(comment, source_read_available=True)
        self.assertEqual(result["decision"], "manual_review_required")
        self.assertEqual(result["error_category"], "dealhub_date_range_missing")
        self.assertNotIn(comment, str(result))

    def test_one_date_range_remains_route_blocked(self):
        result = validate_case4_onboarding_comments("2026-08-01 - 2029-07-31", source_read_available=True)
        self.assertEqual(result["decision"], "case_not_mapped_yet")
        self.assertEqual(result["comment_validation_status"], "single_date_range_present")
        self.assertFalse(result["proceed"])

    def test_ambiguous_date_ranges_require_manual_review(self):
        result = validate_case4_onboarding_comments("2026-08-01 - 2029-07-31; 2025-08-01 - 2026-07-31", source_read_available=True)
        self.assertEqual(result["decision"], "manual_review_required")
        self.assertEqual(result["error_category"], "dealhub_date_range_ambiguous")
