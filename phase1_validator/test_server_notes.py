import unittest

from phase1_validator.server_notes import compare


class NotesCompareTests(unittest.TestCase):
    def test_blocked_result_has_sanitised_note_and_stable_hash(self):
        evidence = {
            "product": "Credential Exposure",
            "start_date": "2026-04-28",
            "end_date": "2027-04-27",
            "status": "Active",
            "subscription_name": "CE Core",
            "license_type": "Annual",
            "quantity": 1,
        }
        payload = {
            "co_number": "SHADOW-NOTE-001",
            "dealhub": evidence,
            "contract": {
                **evidence,
                "end_date": "2026-04-27",
                "source_file_name": "masked-contract.pdf",
            },
            "blocking_gates": ["approval_pending"],
        }

        result = compare(payload)

        self.assertEqual(result["decision"], "manual_review_required")
        self.assertTrue(result["should_append_rejection_note"])
        self.assertIn("SHADOW-NOTE-001 is blocked", result["onboarding_rejection_note"])
        self.assertIn("approval_status: Pending", result["onboarding_rejection_note"])
        self.assertNotIn("masked-contract.pdf", result["onboarding_rejection_note"])
        self.assertEqual(len(result["result_hash"]), 64)
        self.assertEqual(result["result_hash"], compare(payload)["result_hash"])

    def test_matched_result_never_generates_a_note(self):
        evidence = {
            "product": "Credential Exposure",
            "start_date": "2026-04-28",
            "end_date": "2027-04-27",
            "status": "Active",
            "subscription_name": "CE Core",
            "license_type": "Annual",
            "quantity": 1,
        }

        result = compare(
            {
                "co_number": "SHADOW-NOTE-002",
                "dealhub": evidence,
                "contract": evidence,
            }
        )

        self.assertFalse(result["should_append_rejection_note"])
        self.assertEqual(result["onboarding_rejection_note"], "")


if __name__ == "__main__":
    unittest.main()
