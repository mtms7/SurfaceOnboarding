from datetime import date
import unittest

from phase1_validator.commercial_evidence import EvidenceError, parse_contract_pages


SIGNED_CORE_PLUS_PAGE = """
Subscription Details
Product Start Date End Date Initial Term (months)
Pentera Core Plus Enterprise - 10,000 End Points 31-07-2026 30-07-2027 12
Pentera Core Plus Enterprise consists of Pentera Core Software, Pentera Resolve
Essentials, the RansomwareReady Module, the Credentials Exposure Module, and
Security Validation Advisor Advanced.
Payment Terms
"""


class CommercialEvidenceTests(unittest.TestCase):
    def test_active_core_plus_with_explicit_ce_clause_is_bundled_verified(self):
        result = parse_contract_pages(
            ["cover page", SIGNED_CORE_PLUS_PAGE],
            document_ref="ContentVersion:masked",
            extraction_method="pdf_text",
            today=date(2026, 8, 14),
        )
        self.assertEqual(result["document_state"], "active")
        self.assertEqual(result["start_date"], "2026-07-31")
        self.assertEqual(result["end_date"], "2027-07-30")
        self.assertEqual(result["credential_exposure"], "bundled_verified")
        self.assertEqual(result["evidence"]["page"], 2)
        self.assertEqual(result["evidence"]["rule_id"], "bundle.core_plus.v2")

    def test_core_plus_commercial_with_explicit_clause_is_supported(self):
        commercial = SIGNED_CORE_PLUS_PAGE.replace("Enterprise", "Commercial")
        result = parse_contract_pages(
            [commercial], document_ref="ContentVersion:masked", extraction_method="pdf_text", today=date(2026, 8, 14)
        )
        self.assertEqual(result["canonical_product"], "core_plus_commercial")
        self.assertEqual(result["credential_exposure"], "bundled_verified")

    def test_ocr_result_requires_visual_confirmation(self):
        result = parse_contract_pages(
            [SIGNED_CORE_PLUS_PAGE],
            document_ref="ContentVersion:masked",
            extraction_method="local_ocr",
            today=date(2026, 8, 14),
        )
        self.assertEqual(result["credential_exposure"], "unverified")
        self.assertEqual(result["review_reason"], "ocr_requires_visual_confirmation")

    def test_missing_dates_fail_closed(self):
        with self.assertRaisesRegex(EvidenceError, "ambiguous_product_term_dates"):
            parse_contract_pages(
                ["Subscription Details Pentera Core Plus Enterprise Credentials Exposure Module"],
                document_ref="ContentVersion:masked",
                extraction_method="pdf_text",
                today=date(2026, 8, 14),
            )


if __name__ == "__main__":
    unittest.main()
