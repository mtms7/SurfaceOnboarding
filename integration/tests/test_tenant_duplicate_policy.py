from __future__ import annotations

import unittest

from integration.onboarding.tenant_duplicate_policy import (
    TenantMatchDisposition,
    TenantSearchCandidate,
    canonical_domain,
    classify_tenant_candidate,
    evaluate_duplicate_preflight,
    normalized_company_name,
)


class TenantDuplicatePolicyTests(unittest.TestCase):
    def test_exact_domain_blocks_a_create_even_when_names_differ(self):
        result = classify_tenant_candidate(
            "Example Customer", "customer.example.", TenantSearchCandidate("Different Historical Name", "CUSTOMER.EXAMPLE")
        )
        self.assertIs(result, TenantMatchDisposition.BLOCK_CREATE)

    def test_exact_normalized_name_blocks_a_create(self):
        result = classify_tenant_candidate(
            "Café Group", None, TenantSearchCandidate("Cafe Group", None)
        )
        self.assertIs(result, TenantMatchDisposition.BLOCK_CREATE)

    def test_meaningful_partial_name_requires_review_not_automatic_match(self):
        result = classify_tenant_candidate(
            "Blue River Community Bank", None, TenantSearchCandidate("Blue River CB", None)
        )
        self.assertIs(result, TenantMatchDisposition.MANUAL_REVIEW)

    def test_initialism_requires_review_not_automatic_match(self):
        result = classify_tenant_candidate(
            "Blue River Community", None, TenantSearchCandidate("BRC", None)
        )
        self.assertIs(result, TenantMatchDisposition.MANUAL_REVIEW)

    def test_unrelated_candidate_does_not_create_a_match(self):
        result = classify_tenant_candidate(
            "Blue River Community", "blue.example", TenantSearchCandidate("Green Field Partners", "green.example")
        )
        self.assertIs(result, TenantMatchDisposition.NO_CANDIDATE)

    def test_normalizers_fail_closed_for_missing_or_invalid_values(self):
        self.assertIsNone(canonical_domain(" "))
        self.assertIsNone(canonical_domain("bad domain"))
        self.assertIsNone(normalized_company_name(None))

    def test_preflight_requires_all_three_searches_before_no_candidate(self):
        result = evaluate_duplicate_preflight(
            exact_domain_searched=True,
            full_name_searched=True,
            normalized_name_searched=False,
            candidate_dispositions=(),
        )
        self.assertIs(result, TenantMatchDisposition.MANUAL_REVIEW)

    def test_preflight_propagates_block_or_ambiguity(self):
        for disposition, expected in (
            (TenantMatchDisposition.BLOCK_CREATE, TenantMatchDisposition.BLOCK_CREATE),
            (TenantMatchDisposition.MANUAL_REVIEW, TenantMatchDisposition.MANUAL_REVIEW),
        ):
            with self.subTest(disposition=disposition):
                result = evaluate_duplicate_preflight(
                    exact_domain_searched=True,
                    full_name_searched=True,
                    normalized_name_searched=True,
                    candidate_dispositions=(disposition,),
                )
                self.assertIs(result, expected)

    def test_complete_empty_preflight_is_no_candidate(self):
        result = evaluate_duplicate_preflight(
            exact_domain_searched=True,
            full_name_searched=True,
            normalized_name_searched=True,
            candidate_dispositions=(),
        )
        self.assertIs(result, TenantMatchDisposition.NO_CANDIDATE)
