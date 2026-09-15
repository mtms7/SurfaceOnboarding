import unittest

from phase1_validator.rejection_notes import render_rejection_note


class RejectionNoteTests(unittest.TestCase):
    def test_renders_sanitised_scope_mismatch_and_blocking_gates(self):
        note = render_rejection_note(
            co_number="CO-0696",
            mismatches=[
                {
                    "field": "license_type",
                    "dealhub_value": "Pentera Core Plus Enterprise - 4000 End Points",
                    "contract_value": "Pentera Core Plus Enterprise - 10000 End Points",
                },
                {
                    "field": "quantity",
                    "dealhub_value": 4000,
                    "contract_value": 10000,
                },
            ],
            blocking_gates=(
                "approval_status: Pending",
                "credential_exposure: OCR evidence requires visual confirmation",
            ),
        )

        self.assertIn("CO-0696 is blocked", note)
        self.assertIn("license_type", note)
        self.assertIn("quantity", note)
        self.assertIn("approval_status: Pending", note)
        self.assertIn("No provisioning is authorized", note)
        self.assertNotIn("masked-contract.pdf", note)

    def test_bounds_note_length(self):
        note = render_rejection_note(
            co_number="CO-TEST",
            mismatches=[
                {"field": "quantity", "dealhub_value": "a" * 1000, "contract_value": "b" * 1000}
            ],
        )

        self.assertLessEqual(len(note), 1800)


if __name__ == "__main__":
    unittest.main()
